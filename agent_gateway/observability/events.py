"""Runtime event JSONL store.

The event store is intentionally local and append-only. It gives the control
plane and dashboard a compact way to inspect the latest runtime chain without
introducing a database dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any


ERROR_STATUSES = {"error", "failed", "rejected", "critical"}


@dataclass(slots=True)
class RuntimeEvent:
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
    def __init__(self, path: Path, *, max_line_bytes: int = 64_000) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_line_bytes = max(1024, int(max_line_bytes))
        self._lock = threading.Lock()

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
        self._append(row)
        return row

    def tail(
        self,
        *,
        limit: int = 100,
        event_type: str = "",
        component: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        rows = self._read_tail(max(1, min(int(limit), 500)))
        filtered = []
        for row in rows:
            if event_type and row.get("type") != event_type:
                continue
            if component and row.get("component") != component:
                continue
            if status and row.get("status") != status:
                continue
            filtered.append(row)
        return filtered[-max(1, min(int(limit), 500)) :]

    def recent_errors(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._read_tail(max(50, min(int(limit) * 5, 1000)))
        errors = [
            row
            for row in rows
            if row.get("error") or str(row.get("status", "")).lower() in ERROR_STATUSES
        ]
        return errors[-max(1, min(int(limit), 200)) :]

    def _append(self, row: dict[str, Any]) -> None:
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        encoded = payload.encode("utf-8")
        if len(encoded) > self.max_line_bytes:
            row = dict(row)
            row["metadata"] = {"truncated": True}
            row["message"] = str(row.get("message", ""))[:1000]
            row["error"] = str(row.get("error", ""))[:1000]
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    def _read_tail(self, limit: int) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        rows.sort(key=lambda row: float(row.get("timestamp", 0.0) or 0.0))
        return rows

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
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
