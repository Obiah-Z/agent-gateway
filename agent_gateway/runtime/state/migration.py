from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Protocol
from urllib.parse import unquote

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import (
    read_agents_source,
    read_bindings_source,
    read_channels_source,
    read_profiles_source,
)
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.store import LocalTaskStore
from agent_gateway.runtime.state.store import SessionStore


class MigrationSink(Protocol):
    """迁移脚手架的备份写入接口。"""

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> None:
        """备份单条会话消息。"""

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> None:
        """备份一整段会话历史。"""

    def write_task(self, task: TaskInstance) -> None:
        """备份单条任务状态。"""

    def write_event(self, event: dict[str, Any]) -> None:
        """备份单条运行事件。"""

    def write_memory(self, content: str, category: str = "general") -> None:
        """备份单条记忆。"""


class StateWriteRepository(Protocol):
    """本地数据回填到外部状态仓储时使用的最小写接口。"""

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """写入或更新一条状态记录。"""


class BulkStateWriteRepository(StateWriteRepository, Protocol):
    """支持批量写入的状态仓储接口。"""

    def bulk_upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        batch_size: int = 500,
    ) -> int:
        """批量写入或更新状态记录。"""


@dataclass(slots=True)
class LocalBackfillReport:
    """本地文件回填到 PostgreSQL 的执行结果。"""

    dry_run: bool
    scanned: dict[str, int] = field(default_factory=dict)
    written: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def scan(self, table: str, count: int = 1) -> None:
        self.scanned[table] = self.scanned.get(table, 0) + count

    def write(self, table: str, count: int = 1) -> None:
        self.written[table] = self.written.get(table, 0) + count

    def skip(self, table: str, count: int = 1) -> None:
        self.skipped[table] = self.skipped.get(table, 0) + count

    def to_dict(self) -> dict[str, Any]:
        """转成 CLI 可直接打印的 JSON 友好结构。"""

        return {
            "dry_run": self.dry_run,
            "scanned": dict(sorted(self.scanned.items())),
            "written": dict(sorted(self.written.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "errors": self.errors,
        }


def backfill_local_state_to_repository(
    settings: GatewaySettings,
    writer: StateWriteRepository,
    *,
    dry_run: bool = False,
    batch_size: int = 500,
) -> LocalBackfillReport:
    """把当前本地 JSON/JSONL 状态回填到外部状态仓储。

    回填只读取现有本地文件，不删除、不搬迁源文件；所有行都使用稳定主键，便于重复执行。
    """

    report = LocalBackfillReport(dry_run=dry_run)
    buffer = _BackfillBuffer(writer=writer, report=report, batch_size=batch_size)
    _backfill_config(settings, buffer)
    _backfill_sessions(settings.sessions_dir, buffer)
    _backfill_tasks(settings.tasks_dir, buffer)
    _backfill_delivery_queue(settings.delivery_queue_dir, buffer)
    _backfill_feishu_webhook(settings.feishu_webhook_dir, buffer)
    _backfill_feishu_onboarding(settings.data_dir / "onboarding" / "feishu", buffer)
    _backfill_channel_offsets(settings.data_dir / "channel-state", buffer)
    _backfill_feishu_card_states(settings.data_dir / "channel-state" / "feishu", buffer)
    _backfill_cron_runs(settings.workspace_root / "cron", buffer)
    _backfill_news_items(settings.data_dir, buffer)
    _backfill_jsonl_dir(settings.events_dir, "runtime_events", buffer)
    _backfill_metrics(settings.metrics_dir, buffer)
    _backfill_alerts(settings.alerts_dir, buffer)
    _backfill_memory(settings.workspace_root, buffer)
    buffer.flush()
    return report


@dataclass(slots=True)
class _BackfillBuffer:
    """按表聚合回填行，优先使用仓储批量写入能力。"""

    writer: StateWriteRepository
    report: LocalBackfillReport
    batch_size: int = 500
    rows_by_table: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add(self, table: str, row: dict[str, Any]) -> None:
        self.report.scan(table)
        if not row:
            self.report.skip(table)
            return
        if self.report.dry_run:
            return
        rows = self.rows_by_table.setdefault(table, [])
        rows.append(row)
        if len(rows) >= self.batch_size:
            self.flush_table(table)

    def flush(self) -> None:
        for table in list(self.rows_by_table):
            self.flush_table(table)

    def flush_table(self, table: str) -> None:
        rows = self.rows_by_table.get(table, [])
        if not rows:
            return
        self.rows_by_table[table] = []
        bulk_upsert = getattr(self.writer, "bulk_upsert", None)
        try:
            if bulk_upsert is not None:
                written = int(bulk_upsert(table, rows, batch_size=self.batch_size) or 0)
                self.report.write(table, written)
                return
            for row in rows:
                self.writer.upsert(table, row)
                self.report.write(table)
        except Exception as exc:
            self.report.errors.append(f"{table}:bulk:{len(rows)}: {exc}")
            self.report.skip(table, len(rows))


def _backfill_config(
    settings: GatewaySettings,
    buffer: _BackfillBuffer,
) -> None:
    now = time.time()
    for row in read_agents_source(settings).get("agents", []):
        if not isinstance(row, dict):
            buffer.report.skip("agents")
            continue
        payload = {
            "id": str(row.get("id", "")),
            "name": str(row.get("name", "")),
            "personality": str(row.get("personality", "")),
            "model": str(row.get("model", "")),
            "dm_scope": str(row.get("dm_scope", "per-peer")),
            "extra_system": str(row.get("extra_system", "")),
            "tool_policy": dict(row.get("tool_policy", {}) or {}),
            "memory_policy": dict(row.get("memory_policy", {}) or {}),
            "prompt_policy": dict(row.get("prompt_policy", {}) or {}),
            "updated_at": now,
        }
        buffer.add("agents", payload)

    for row in read_bindings_source(settings).get("bindings", []):
        if not isinstance(row, dict):
            buffer.report.skip("bindings")
            continue
        agent_id = str(row.get("agent_id", ""))
        match_key = str(row.get("match_key", ""))
        match_value = str(row.get("match_value", ""))
        payload = {
            "key": f"{agent_id}\x1f{match_key}\x1f{match_value}",
            "agent_id": agent_id,
            "tier": int(row.get("tier", 5) or 5),
            "match_key": match_key,
            "match_value": match_value,
            "priority": int(row.get("priority", 0) or 0),
            "updated_at": now,
        }
        buffer.add("bindings", payload)

    for row in read_profiles_source(settings).get("profiles", []):
        if not isinstance(row, dict):
            buffer.report.skip("profiles")
            continue
        payload = {
            "name": str(row.get("name", "")),
            "provider": str(row.get("provider", "anthropic")),
            "api_key": str(row.get("api_key", "")),
            "api_key_env": str(row.get("api_key_env", "")),
            "base_url": str(row.get("base_url", "")),
            "base_url_env": str(row.get("base_url_env", "")),
            "updated_at": now,
        }
        buffer.add("profiles", payload)

    for row in read_channels_source(settings).get("channels", []):
        if not isinstance(row, dict):
            buffer.report.skip("channels")
            continue
        channel = str(row.get("channel", ""))
        account_id = str(row.get("account_id", ""))
        payload = {
            "key": f"{channel}\x1f{account_id}",
            "channel": channel,
            "account_id": account_id,
            "enabled": bool(row.get("enabled", True)),
            "label": str(row.get("label", "")),
            "token": str(row.get("token", "")),
            "token_env": str(row.get("token_env", "")),
            "config": dict(row.get("config", {}) or {}),
            "updated_at": now,
        }
        buffer.add("channels", payload)


def _backfill_sessions(
    sessions_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    agents_dir = sessions_dir / "agents"
    if not agents_dir.is_dir():
        return
    loader = SessionStore(sessions_dir)
    for agent_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
        agent_id = agent_dir.name
        for path in sorted(agent_dir.glob("*.jsonl")):
            session_key = unquote(path.stem)
            try:
                messages = loader._rebuild_history(path)
            except OSError as exc:
                buffer.report.errors.append(f"sessions:{path}: {exc}")
                buffer.report.skip("sessions")
                continue
            stat = path.stat()
            payload = {
                "id": f"{agent_id}:{session_key}:snapshot",
                "agent_id": agent_id,
                "session_key": session_key,
                "channel": "",
                "account_id": "",
                "peer_id": "",
                "title": "",
                "summary": "",
                "created_at": stat.st_ctime,
                "updated_at": stat.st_mtime,
                "last_message_at": stat.st_mtime,
                "message_count": len(messages),
                "metadata": {"kind": "snapshot", "messages": messages},
            }
            buffer.add("sessions", payload)


def _backfill_tasks(
    tasks_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    if not tasks_dir.is_dir():
        return
    for path in sorted(tasks_dir.glob("*.json")):
        row = _read_json_file(path, buffer.report, "tasks")
        if row is None:
            continue
        buffer.add("tasks", row)


def _backfill_delivery_queue(
    delivery_queue_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把本地可靠投递 pending/failed 文件回填到 delivery_entries。"""

    if not delivery_queue_dir.is_dir():
        return
    _backfill_delivery_dir(delivery_queue_dir, "pending", buffer)
    _backfill_delivery_dir(delivery_queue_dir / "failed", "failed", buffer)


def _backfill_delivery_dir(
    root: Path,
    state: str,
    buffer: _BackfillBuffer,
) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        row = _read_json_file(path, buffer.report, "delivery_entries")
        if row is None:
            continue
        now = path.stat().st_mtime
        payload = {
            "id": str(row.get("id", path.stem)),
            "state": state,
            "channel": str(row.get("channel", "")),
            "to": str(row.get("to", "")),
            "text": str(row.get("text", "")),
            "retry_count": int(row.get("retry_count", 0) or 0),
            "last_error": str(row.get("last_error") or ""),
            "metadata": dict(row.get("metadata", {}) or {}),
            "enqueued_at": float(row.get("enqueued_at", now) or now),
            "next_retry_at": float(row.get("next_retry_at", 0.0) or 0.0),
            "updated_at": now,
        }
        buffer.add("delivery_entries", payload)


def _backfill_jsonl_dir(
    root_dir: Path,
    table: str,
    buffer: _BackfillBuffer,
) -> None:
    if not root_dir.is_dir():
        return
    for path in sorted(root_dir.glob("*.jsonl")):
        for row in _iter_jsonl(path, buffer.report, table):
            buffer.add(table, row)


def _backfill_feishu_webhook(
    feishu_webhook_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把飞书 Webhook 本地去重与审计文件回填到 PostgreSQL。"""

    if not feishu_webhook_dir.is_dir():
        return
    seen_file = feishu_webhook_dir / "dedup" / "seen-events.jsonl"
    if seen_file.exists():
        for row in _iter_jsonl(seen_file, buffer.report, "feishu_dedup_entries"):
            event_id = str(row.get("event_id", ""))
            if not event_id:
                buffer.report.skip("feishu_dedup_entries")
                continue
            seen_at = float(row.get("seen_at", 0.0) or 0.0)
            expires_at = float(row.get("expires_at", 0.0) or 0.0)
            buffer.add(
                "feishu_dedup_entries",
                {
                    "event_id": event_id,
                    "seen_at": seen_at,
                    "expires_at": expires_at,
                    "metadata": row,
                },
            )

    events_file = feishu_webhook_dir / "events.jsonl"
    if events_file.exists():
        for index, row in enumerate(_iter_jsonl(events_file, buffer.report, "feishu_webhook_events"), start=1):
            received_at = float(row.get("ts", 0.0) or 0.0)
            row_hash = hashlib.sha256(
                json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            buffer.add(
                "feishu_webhook_events",
                {
                    "id": str(row.get("id") or f"{events_file.stem}:{index}:{row_hash[:16]}"),
                    "received_at": received_at,
                    "outcome": str(row.get("outcome", "")),
                    "reason": str(row.get("reason", "")),
                    "http_status": int(row.get("http_status", 0) or 0),
                    "channel_account": str(row.get("channel_account", "")),
                    "event_id": str(row.get("event_id", "")),
                    "message_id": str(row.get("message_id", "")),
                    "chat_id": str(row.get("chat_id", "")),
                    "chat_type": str(row.get("chat_type", "")),
                    "sender_open_id": str(row.get("sender_open_id", "")),
                    "sender_user_id": str(row.get("sender_user_id", "")),
                    "body_sha256": str(row.get("body_sha256", "")),
                    "metadata": {
                        "headers": row.get("headers", {}),
                        "inbound": row.get("inbound", {}),
                        "source": "local-feishu-webhook-jsonl",
                    },
                },
            )


def _backfill_feishu_onboarding(
    onboarding_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把飞书 onboarding 本地会话文件回填到 PostgreSQL。"""

    sessions_file = onboarding_dir / "sessions.json"
    if not sessions_file.exists():
        return
    row = _read_json_file(sessions_file, buffer.report, "feishu_onboarding_sessions")
    if row is None:
        return
    sessions = row.get("sessions", [])
    if not isinstance(sessions, list):
        buffer.report.skip("feishu_onboarding_sessions")
        return
    for item in sessions:
        if not isinstance(item, dict):
            buffer.report.skip("feishu_onboarding_sessions")
            continue
        session_id = str(item.get("session_id", ""))
        binding_code = str(item.get("binding_code", ""))
        if not session_id or not binding_code:
            buffer.report.skip("feishu_onboarding_sessions")
            continue
        updated_at = float(item.get("bound_at") or item.get("created_at") or sessions_file.stat().st_mtime)
        buffer.add(
            "feishu_onboarding_sessions",
            {
                "session_id": session_id,
                "binding_code": binding_code,
                "mode": str(item.get("mode", "personal")),
                "status": str(item.get("status", "pending")),
                "account_id": str(item.get("account_id", "feishu-long-local")),
                "agent_id": str(item.get("agent_id", "")),
                "agent_name": str(item.get("agent_name", "")),
                "created_at": float(item.get("created_at", 0.0) or 0.0),
                "expires_at": float(item.get("expires_at", 0.0) or 0.0),
                "bound_at": float(item.get("bound_at", 0.0) or 0.0),
                "bound_peer_id": str(item.get("bound_peer_id", "")),
                "bound_sender_id": str(item.get("bound_sender_id", "")),
                "bound_is_group": bool(item.get("bound_is_group", False)),
                "last_error": str(item.get("last_error", "")),
                "updated_at": updated_at,
                "metadata": {"source": "local-feishu-onboarding-sessions"},
            },
        )


def _backfill_channel_offsets(
    channel_state_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把本地通道消费 offset 回填到 PostgreSQL。"""

    telegram_dir = channel_state_dir / "telegram"
    if not telegram_dir.is_dir():
        return
    for path in sorted(telegram_dir.glob("offset-*.txt")):
        account_id = path.stem.removeprefix("offset-")
        try:
            offset = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            buffer.report.errors.append(f"channel_offsets:{path}: {exc}")
            buffer.report.skip("channel_offsets")
            continue
        buffer.add(
            "channel_offsets",
            {
                "key": f"telegram\x1f{account_id}",
                "channel": "telegram",
                "account_id": account_id,
                "offset_value": offset,
                "updated_at": path.stat().st_mtime,
                "metadata": {"source": "local-telegram-offset"},
            },
        )


def _backfill_feishu_card_states(
    feishu_state_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把本地飞书有状态卡片 JSON 回填到 PostgreSQL。"""

    if not feishu_state_dir.is_dir():
        return
    for account_dir in sorted(path for path in feishu_state_dir.iterdir() if path.is_dir()):
        cards_dir = account_dir / "cards"
        if not cards_dir.is_dir():
            continue
        for path in sorted(cards_dir.glob("*.json")):
            row = _read_json_file(path, buffer.report, "feishu_card_states")
            if row is None:
                continue
            card_id = str(row.get("card_id") or path.stem)
            if not card_id:
                buffer.report.skip("feishu_card_states")
                continue
            buffer.add(
                "feishu_card_states",
                {
                    "card_id": card_id,
                    "owner_channel": str(row.get("owner_channel", "feishu")),
                    "owner_account_id": str(row.get("owner_account_id") or account_dir.name),
                    "peer_id": str(row.get("peer_id", "")),
                    "message_id": str(row.get("message_id", "")),
                    "title": str(row.get("title", "")),
                    "summary": str(row.get("summary", "")),
                    "template": str(row.get("template", "blue")),
                    "card_link": str(row.get("card_link", "")),
                    "blocks": list(row.get("blocks", []) or []),
                    "structured_blocks": list(row.get("structured_blocks", []) or []),
                    "actions": list(row.get("actions", []) or []),
                    "page_size": int(row.get("page_size", 4) or 4),
                    "page_index": int(row.get("page_index", 0) or 0),
                    "expanded": bool(row.get("expanded", False)),
                    "updated_at": float(row.get("updated_at", path.stat().st_mtime) or path.stat().st_mtime),
                    "metadata": row,
                },
            )


def _backfill_cron_runs(
    cron_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把本地 Cron 运行日志回填到 PostgreSQL。"""

    run_log = cron_dir / "cron-runs.jsonl"
    if not run_log.exists():
        return
    for index, row in enumerate(_iter_jsonl(run_log, buffer.report, "cron_runs"), start=1):
        run_at_raw = str(row.get("run_at", ""))
        run_at = _parse_time(run_at_raw) or run_log.stat().st_mtime
        job_id = str(row.get("job_id", ""))
        buffer.add(
            "cron_runs",
            {
                "id": str(row.get("id") or f"{job_id}:{run_at_raw or index}"),
                "job_id": job_id,
                "config_id": str(row.get("config_id", "")),
                "agent_id": str(row.get("agent_id", "")),
                "scope": str(row.get("scope", "")),
                "run_at": run_at,
                "status": str(row.get("status", "")),
                "output_preview": str(row.get("output_preview", "")),
                "error": str(row.get("error", "")),
                "metadata": row,
            },
        )


def _backfill_news_items(
    data_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    """把新闻简报本地 seen/collected 状态回填到 PostgreSQL。"""

    for store_name in ("news-digest", "github-skill-digest"):
        root = data_dir / store_name
        if not root.is_dir():
            continue
        _backfill_news_seen(root / "seen-items.jsonl", store_name, buffer)
        _backfill_news_collected(root / "collected-items.jsonl", store_name, buffer)


def _backfill_news_seen(path: Path, store_name: str, buffer: _BackfillBuffer) -> None:
    if not path.exists():
        return
    for row in _iter_jsonl(path, buffer.report, "news_items"):
        item_id = str(row.get("id", ""))
        if not item_id:
            buffer.report.skip("news_items")
            continue
        seen_at = float(row.get("seen_at", 0.0) or 0.0)
        buffer.add(
            "news_items",
            {
                "key": f"{store_name}\x1fseen\x1f{item_id}",
                "store_name": store_name,
                "state": "seen",
                "item_id": item_id,
                "source_id": str(row.get("source_id", "")),
                "source_type": str(row.get("source_type", "")),
                "title": str(row.get("title", "")),
                "url": str(row.get("url", "")),
                "published_at": str(row.get("published_at", "")),
                "summary": str(row.get("summary", "")),
                "tags": list(row.get("tags", []) or []),
                "seen_at": seen_at,
                "collected_at": 0.0,
                "updated_at": seen_at or path.stat().st_mtime,
                "metadata": row,
            },
        )


def _backfill_news_collected(path: Path, store_name: str, buffer: _BackfillBuffer) -> None:
    if not path.exists():
        return
    for row in _iter_jsonl(path, buffer.report, "news_items"):
        item_id = str(row.get("id", ""))
        if not item_id:
            buffer.report.skip("news_items")
            continue
        collected_at = float(row.get("collected_at", 0.0) or 0.0)
        buffer.add(
            "news_items",
            {
                "key": f"{store_name}\x1fcollected\x1f{item_id}",
                "store_name": store_name,
                "state": "collected",
                "item_id": item_id,
                "source_id": str(row.get("source_id", "")),
                "source_type": str(row.get("source_type", "")),
                "title": str(row.get("title", "")),
                "url": str(row.get("url", "")),
                "published_at": str(row.get("published_at", "")),
                "summary": str(row.get("summary", "")),
                "tags": list(row.get("tags", []) or []),
                "seen_at": 0.0,
                "collected_at": collected_at,
                "updated_at": collected_at or path.stat().st_mtime,
                "metadata": row,
            },
        )


def _backfill_metrics(
    metrics_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    if not metrics_dir.is_dir():
        return
    for path in sorted(metrics_dir.glob("metrics-*.jsonl")):
        for index, row in enumerate(_iter_jsonl(path, buffer.report, "metrics"), start=1):
            timestamp = float(row.get("timestamp", 0.0) or 0.0)
            payload = {
                "id": str(row.get("id") or f"{path.stem}:{index}"),
                "timestamp": timestamp,
                "kind": "snapshot",
                "name": "runtime",
                "value": 0.0,
                "labels": {},
                "window_seconds": 0,
                "metadata": row,
            }
            buffer.add("metrics", payload)


def _backfill_alerts(
    alerts_dir: Path,
    buffer: _BackfillBuffer,
) -> None:
    if not alerts_dir.is_dir():
        return
    for path in sorted(alerts_dir.glob("alerts-*.jsonl")):
        for index, row in enumerate(_iter_jsonl(path, buffer.report, "errors"), start=1):
            rule = row.get("rule", {}) if isinstance(row.get("rule"), dict) else {}
            payload = {
                "id": str(row.get("id") or f"{path.stem}:{index}"),
                "event_id": str(row.get("event_id") or ""),
                "timestamp": float(row.get("timestamp", 0.0) or 0.0),
                "component": "alerts",
                "category": str(row.get("event", "alert")),
                "severity": str(rule.get("severity", "")),
                "message": str(row.get("message", "")),
                "error": "",
                "correlation_id": "",
                "agent_id": "",
                "session_key": "",
                "metadata": row,
            }
            buffer.add("errors", payload)


def _backfill_memory(
    workspace_root: Path,
    buffer: _BackfillBuffer,
) -> None:
    daily_dir = workspace_root / "memory" / "daily"
    if not daily_dir.is_dir():
        return
    for path in sorted(daily_dir.glob("*.jsonl")):
        for index, row in enumerate(_iter_jsonl(path, buffer.report, "memory_entries"), start=1):
            ts = _parse_time(row.get("ts")) or path.stat().st_mtime
            payload = {
                "id": str(row.get("id") or f"{path.stem}:{index}"),
                "agent_id": str(row.get("agent_id", "")),
                "category": str(row.get("category", "general")),
                "content": str(row.get("content", "")),
                "source_file": path.name,
                "created_at": ts,
                "updated_at": ts,
                "metadata": row,
            }
            buffer.add("memory_entries", payload)


def _read_json_file(
    path: Path,
    report: LocalBackfillReport,
    table: str,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.errors.append(f"{table}:{path}: {exc}")
        report.skip(table)
        return None
    if not isinstance(payload, dict):
        report.skip(table)
        return None
    return payload


def _iter_jsonl(
    path: Path,
    report: LocalBackfillReport,
    table: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        report.errors.append(f"{table}:{path}: {exc}")
        report.skip(table)
        return rows
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            report.errors.append(f"{table}:{path}: {exc}")
            report.skip(table)
            continue
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            report.skip(table)
    return rows


def _parse_time(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


@dataclass(slots=True)
class CompositeMigrationSink(MigrationSink):
    """把多路迁移 sink 组合成一个单点入口。"""

    sinks: tuple[MigrationSink, ...]

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> None:
        for sink in self.sinks:
            try:
                sink.write_session_message(agent_id, session_key, role, content)
            except Exception:
                continue

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> None:
        for sink in self.sinks:
            try:
                sink.rewrite_session_messages(agent_id, session_key, messages)
            except Exception:
                continue

    def write_task(self, task: TaskInstance) -> None:
        for sink in self.sinks:
            try:
                sink.write_task(task)
            except Exception:
                continue

    def write_event(self, event: dict[str, Any]) -> None:
        for sink in self.sinks:
            try:
                sink.write_event(event)
            except Exception:
                continue

    def write_memory(self, content: str, category: str = "general") -> None:
        for sink in self.sinks:
            try:
                sink.write_memory(content, category=category)
            except Exception:
                continue


@dataclass(slots=True)
class LocalMigrationSink(MigrationSink):
    """把现有本地写入口包装成迁移时的备份写。"""

    sessions: SessionStore
    tasks: LocalTaskStore
    events: RuntimeEventStore
    memory: MemoryStore

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> None:
        self.sessions.append_message_to_disk(agent_id, session_key, role, content)

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> None:
        self.sessions.rewrite_messages_to_disk(agent_id, session_key, messages)

    def write_task(self, task: TaskInstance) -> None:
        self.tasks.write_task_to_disk(task)

    def write_event(self, event: dict[str, Any]) -> None:
        self.events.write_event_row(event)

    def write_memory(self, content: str, category: str = "general") -> None:
        self.memory.write_memory_migration(content, category=category)
