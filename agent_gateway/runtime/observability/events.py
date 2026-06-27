"""运行时事件JSONL存储。

该事件存储特意设计为本地且仅追加的。它为控制平面和仪表盘提供了一种紧凑的方式来
检查最新的运行时链，而无需引入数据库依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any
from typing import Any


ERROR_STATUSES = {"error", "failed", "rejected", "critical"}
IGNORED_REJECTION_REASONS = {
    "method not allowed",
    "duplicate event",
    "event ignored by parser",
    "card action ignored by parser",
}
CORRELATION_ID_KEY = "correlation_id"


def new_correlation_id(prefix: str = "evt") -> str:
    """生成带前缀的关联 ID，用于串起一次完整运行链路。"""

    normalized = "".join(ch for ch in prefix.lower() if ch.isalnum() or ch in {"-", "_"}) or "evt"
    return f"{normalized}_{uuid.uuid4().hex[:16]}"


def ensure_correlation_id(metadata: dict[str, Any], *, prefix: str = "evt") -> str:
    """确保 metadata 中存在 correlation_id，没有则原地补齐。"""

    current = str(metadata.get(CORRELATION_ID_KEY, "") or "").strip()
    if current:
        return current
    current = new_correlation_id(prefix)
    metadata[CORRELATION_ID_KEY] = current
    return current


@dataclass(slots=True)
class RuntimeEvent:
    """单条运行事件。"""

    type: str
    status: str
    component: str
    message: str
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    correlation_id: str = ""
    agent_id: str = ""
    session_key: str = ""
    channel: str = ""
    account_id: str = ""
    peer_id: str = ""
    delivery_id: str = ""
    job_id: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转成 JSONL 可落盘结构。"""

        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "type": self.type,
            "status": self.status,
            "component": self.component,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "agent_id": self.agent_id,
            "session_key": self.session_key,
            "channel": self.channel,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "delivery_id": self.delivery_id,
            "job_id": self.job_id,
            "error": self.error,
            "metadata": self.metadata,
        }


class RuntimeEventStore:
    """本地 JSONL 事件存储。

    采用按天滚动、追加写入的方式保存运行事件，供 Dashboard、控制面和错误视图回看。
    """

    def __init__(
        self,
        path: Path,
        *,
        max_line_bytes: int = 64_000,
        retention_days: int = 14,
    ) -> None:
        self.root_dir = path if path.suffix == "" else path.parent
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._path_for_date(date.today())
        self.max_line_bytes = max(1024, int(max_line_bytes))
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.Lock()
        self.backup_sink = None
        self.read_backend: Any | None = None
        self.write_backend: Any | None = None

    def record(
        self,
        event_type: str,
        *,
        status: str,
        component: str,
        message: str,
        correlation_id: str = "",
        agent_id: str = "",
        session_key: str = "",
        channel: str = "",
        account_id: str = "",
        peer_id: str = "",
        delivery_id: str = "",
        job_id: str = "",
        error: str | Exception = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一条运行事件并立即落盘。"""

        event = RuntimeEvent(
            type=str(event_type),
            status=str(status),
            component=str(component),
            message=str(message),
            correlation_id=str(correlation_id or ""),
            agent_id=str(agent_id or ""),
            session_key=str(session_key or ""),
            channel=str(channel or ""),
            account_id=str(account_id or ""),
            peer_id=str(peer_id or ""),
            delivery_id=str(delivery_id or ""),
            job_id=str(job_id or ""),
            error=str(error or ""),
            metadata=self._sanitize_metadata(metadata or {}),
        )
        row = event.to_dict()
        self._write_primary(row)
        self.write_event_row(row)
        return row

    def write_event_row(self, row: dict[str, Any]) -> None:
        """仅追加本地事件文件，不触发备份镜像。"""

        self._append(dict(row))

    def tail(
        self,
        *,
        limit: int = 100,
        event_type: str = "",
        component: str = "",
        status: str = "",
        correlation_id: str = "",
        agent_id: str = "",
        channel: str = "",
        job_id: str = "",
        delivery_id: str = "",
    ) -> list[dict[str, Any]]:
        """按过滤条件回看最近事件。"""

        if self.read_backend is not None:
            try:
                return self.read_backend.list(
                    "runtime_events",
                    limit=limit,
                    filters={
                        "event_type": event_type,
                        "component": component,
                        "status": status,
                        "correlation_id": correlation_id,
                        "agent_id": agent_id,
                        "channel": channel,
                        "job_id": job_id,
                        "delivery_id": delivery_id,
                    },
                )
            except Exception:
                pass
        safe_limit = max(1, min(int(limit), 500))
        rows = self._read_tail(max(safe_limit, min(safe_limit * 5, 2000)))
        filtered = []
        for row in rows:
            if event_type and row.get("type") != event_type:
                continue
            if component and row.get("component") != component:
                continue
            if status and row.get("status") != status:
                continue
            if correlation_id and row.get("correlation_id") != correlation_id:
                continue
            if agent_id and row.get("agent_id") != agent_id:
                continue
            if channel and row.get("channel") != channel:
                continue
            if job_id and row.get("job_id") != job_id:
                continue
            if delivery_id and row.get("delivery_id") != delivery_id:
                continue
            filtered.append(row)
        return filtered[-safe_limit:]

    def recent_errors(
        self,
        *,
        limit: int = 50,
        component: str = "",
        correlation_id: str = "",
    ) -> list[dict[str, Any]]:
        """返回最近错误事件，并排除约定的无害拒绝项。"""

        if self.read_backend is not None:
            try:
                return self.read_backend.list(
                    "errors",
                    limit=limit,
                    filters={
                        "component": component,
                        "correlation_id": correlation_id,
                    },
                )
            except Exception:
                pass
        rows = self._read_tail(max(50, min(int(limit) * 5, 1000)))
        errors = [
            row
            for row in rows
            if self._is_error_event(row)
        ]
        if component:
            errors = [row for row in errors if row.get("component") == component]
        if correlation_id:
            errors = [row for row in errors if row.get("correlation_id") == correlation_id]
        return errors[-max(1, min(int(limit), 200)) :]

    @staticmethod
    def _is_error_event(row: dict[str, Any]) -> bool:
        """判断一条事件是否应计入错误视图。"""

        status = str(row.get("status", "")).lower()
        if status == "rejected" and RuntimeEventStore._is_ignorable_rejection(row):
            return False
        return bool(row.get("error")) or status in ERROR_STATUSES

    @staticmethod
    def _is_ignorable_rejection(row: dict[str, Any]) -> bool:
        """识别不应告警的拒绝类事件。"""

        reason = str(row.get("error") or row.get("metadata", {}).get("reason", "")).lower()
        return reason in IGNORED_REJECTION_REASONS

    def _append(self, row: dict[str, Any]) -> None:
        """把事件追加写入按日分片的 JSONL 文件。"""

        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        encoded = payload.encode("utf-8")
        if len(encoded) > self.max_line_bytes:
            row = dict(row)
            row["metadata"] = {"truncated": True}
            row["message"] = str(row.get("message", ""))[:1000]
            row["error"] = str(row.get("error", ""))[:1000]
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        path = self._path_for_timestamp(float(row.get("timestamp", time.time()) or time.time()))
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        self.cleanup()

    def _mirror(self, row: dict[str, Any]) -> None:
        """把事件镜像到备份 sink。"""

        sink = getattr(self, "backup_sink", None)
        if sink is None:
            return
        method = getattr(sink, "write_event", None)
        if method is None:
            return
        try:
            method(row)
        except Exception:
            pass

    def _write_primary(self, row: dict[str, Any]) -> None:
        """优先写入数据库主存储；不可用时退回备份 sink。"""

        backend = getattr(self, "write_backend", None)
        if backend is not None:
            method = getattr(backend, "write_event", None)
            if method is not None:
                try:
                    method(row)
                    return
                except Exception:
                    pass
        self._mirror(row)

    def _read_tail(self, limit: int) -> list[dict[str, Any]]:
        """从最近文件中逆序读取尾部事件。"""

        rows: list[dict[str, Any]] = []
        remaining = max(1, int(limit))
        for path in reversed(self._event_files()):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                continue
            for line in lines[-remaining:]:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
            if len(rows) >= limit:
                break
        rows.sort(key=lambda row: float(row.get("timestamp", 0.0) or 0.0))
        return rows[-limit:]

    def cleanup(self, *, now: date | None = None) -> None:
        """删除超过保留期的历史事件文件。"""

        cutoff = (now or date.today()) - timedelta(days=self.retention_days - 1)
        for path in self._event_files():
            suffix = path.stem.removeprefix("runtime-events-")
            try:
                file_date = date.fromisoformat(suffix)
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _event_files(self) -> list[Path]:
        """列出全部事件分片文件。"""

        return sorted(self.root_dir.glob("runtime-events-*.jsonl"))

    def _path_for_timestamp(self, timestamp: float) -> Path:
        """根据时间戳定位对应日期分片文件。"""

        event_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        return self._path_for_date(event_date)

    def _path_for_date(self, event_date: date) -> Path:
        """构造指定日期的事件文件路径。"""

        return self.root_dir / f"runtime-events-{event_date.isoformat()}.jsonl"

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """清洗 metadata，确保可 JSON 序列化且体积受控。"""

        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            if key.lower() in {"token", "secret", "app_secret", "authorization"}:
                safe[key] = "[redacted]"
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, (list, tuple)):
                safe[key] = [
                    item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                    for item in value[:20]
                ]
            elif isinstance(value, dict):
                safe[key] = RuntimeEventStore._sanitize_metadata(value)
            else:
                safe[key] = str(value)
        return safe
