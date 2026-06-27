from __future__ import annotations

import json
from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import ensure_default_project_files
from agent_gateway.runtime.state.migration import backfill_local_state_to_repository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.store import LocalTaskStore


class RecordingWriter:
    """记录回填写入调用的测试 writer。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, object]]] = []
        self.batches: list[tuple[str, list[dict[str, object]], int]] = []

    def upsert(self, table: str, row: dict[str, object]) -> dict[str, object]:
        self.rows.append((table, row))
        return row

    def bulk_upsert(
        self,
        table: str,
        rows: list[dict[str, object]],
        *,
        batch_size: int = 500,
    ) -> int:
        self.batches.append((table, list(rows), batch_size))
        return len(rows)


def _settings(tmp_path: Path) -> GatewaySettings:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    ensure_default_project_files(settings)
    return settings


def test_backfill_local_state_dry_run_scans_without_writing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    writer = RecordingWriter()

    report = backfill_local_state_to_repository(settings, writer, dry_run=True)

    assert report.dry_run is True
    assert report.scanned["agents"] >= 1
    assert report.scanned["bindings"] >= 1
    assert report.scanned["profiles"] >= 1
    assert report.scanned["channels"] >= 1
    assert report.written == {}
    assert writer.rows == []


def test_backfill_local_state_writes_config_and_runtime_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    SessionStore(settings.sessions_dir).rewrite_messages(
        "main",
        "cli:peer",
        [{"role": "user", "content": "hello"}],
    )
    LocalTaskStore(settings.tasks_dir).create(
        TaskInstance.create(task_type="cron", source="test", agent_id="main")
    )
    delivery_path = settings.delivery_queue_dir / "delivery-pending.json"
    delivery_path.write_text(
        json.dumps(
            {
                "id": "delivery-pending",
                "channel": "cli",
                "to": "peer-1",
                "text": "pending",
                "retry_count": 0,
                "metadata": {"kind": "reply"},
                "enqueued_at": 1.0,
                "next_retry_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    failed_dir = settings.delivery_queue_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_path = failed_dir / "delivery-failed.json"
    failed_path.write_text(
        json.dumps(
            {
                "id": "delivery-failed",
                "channel": "feishu",
                "to": "ou_xxx",
                "text": "failed",
                "retry_count": 3,
                "last_error": "invalid target",
                "metadata": {"kind": "reply"},
                "enqueued_at": 2.0,
                "next_retry_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    feishu_dedup_path = settings.feishu_webhook_dir / "dedup" / "seen-events.jsonl"
    feishu_dedup_path.parent.mkdir(parents=True, exist_ok=True)
    feishu_dedup_path.write_text(
        json.dumps({"event_id": "default:evt-1", "seen_at": 3.0, "expires_at": 63.0})
        + "\n",
        encoding="utf-8",
    )
    feishu_audit_path = settings.feishu_webhook_dir / "events.jsonl"
    feishu_audit_path.write_text(
        json.dumps(
            {
                "ts": 4.0,
                "outcome": "accepted",
                "reason": "event accepted",
                "http_status": 200,
                "channel_account": "default",
                "event_id": "evt-1",
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "sender_open_id": "ou_1",
                "sender_user_id": "u_1",
                "body_sha256": "abc",
                "headers": {"x-lark-request-timestamp": "1"},
                "inbound": {"sender_id": "ou_1"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    onboarding_path = settings.data_dir / "onboarding" / "feishu" / "sessions.json"
    onboarding_path.parent.mkdir(parents=True, exist_ok=True)
    onboarding_path.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "session_id": "ob_1",
                        "binding_code": "GATEWAY-ABC123",
                        "mode": "personal",
                        "status": "pending",
                        "account_id": "feishu-long-local",
                        "created_at": 5.0,
                        "expires_at": 905.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    telegram_offset_path = settings.data_dir / "channel-state" / "telegram" / "offset-telegram-main.txt"
    telegram_offset_path.parent.mkdir(parents=True, exist_ok=True)
    telegram_offset_path.write_text("12345", encoding="utf-8")
    cron_run_path = settings.workspace_root / "cron" / "cron-runs.jsonl"
    cron_run_path.parent.mkdir(parents=True, exist_ok=True)
    cron_run_path.write_text(
        json.dumps(
            {
                "job_id": "system-ping",
                "config_id": "global",
                "agent_id": "main",
                "scope": "global",
                "run_at": "2026-06-28T10:00:00+00:00",
                "status": "ok",
                "output_preview": "Ping",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    news_seen_path = settings.data_dir / "news-digest" / "seen-items.jsonl"
    news_seen_path.parent.mkdir(parents=True, exist_ok=True)
    news_seen_path.write_text(
        json.dumps(
            {
                "id": "news-1",
                "url": "https://example.com/news-1",
                "source_id": "source-a",
                "seen_at": 6.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    news_collected_path = settings.data_dir / "news-digest" / "collected-items.jsonl"
    news_collected_path.write_text(
        json.dumps(
            {
                "id": "news-2",
                "source_id": "source-a",
                "source_type": "github_releases",
                "title": "Collected News",
                "url": "https://example.com/news-2",
                "published_at": "2026-06-28T10:00:00Z",
                "summary": "Summary",
                "tags": ["agent"],
                "metadata": {"stars": 10},
                "collected_at": 7.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    event_path = settings.events_dir / "events-2026-06-28.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "event_id": "evt-1",
                "timestamp": 1.0,
                "type": "inbound.received",
                "status": "ok",
                "component": "channel_runtime",
                "message": "received",
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    memory_path = settings.workspace_root / "memory" / "daily" / "2026-06-28.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps({"ts": "2026-06-28T10:00:00+00:00", "category": "note", "content": "remember me"})
        + "\n",
        encoding="utf-8",
    )
    writer = RecordingWriter()

    report = backfill_local_state_to_repository(settings, writer, dry_run=False)

    tables = [table for table, _, _ in writer.batches]
    assert "agents" in tables
    assert "sessions" in tables
    assert "tasks" in tables
    assert "delivery_entries" in tables
    assert "feishu_dedup_entries" in tables
    assert "feishu_webhook_events" in tables
    assert "feishu_onboarding_sessions" in tables
    assert "channel_offsets" in tables
    assert "cron_runs" in tables
    assert "news_items" in tables
    assert "runtime_events" in tables
    assert "memory_entries" in tables
    assert report.written["sessions"] == 1
    assert report.written["delivery_entries"] == 2
    assert report.written["feishu_dedup_entries"] == 1
    assert report.written["feishu_webhook_events"] == 1
    assert report.written["feishu_onboarding_sessions"] == 1
    assert report.written["channel_offsets"] == 1
    assert report.written["cron_runs"] == 1
    assert report.written["news_items"] == 2
    assert report.written["runtime_events"] == 1
    assert report.errors == []
    assert writer.rows == []
    delivery_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "delivery_entries"
        for row in rows
    ]
    assert {row["state"] for row in delivery_rows} == {"pending", "failed"}
    feishu_dedup_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "feishu_dedup_entries"
        for row in rows
    ]
    assert feishu_dedup_rows[0]["event_id"] == "default:evt-1"
    feishu_audit_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "feishu_webhook_events"
        for row in rows
    ]
    assert feishu_audit_rows[0]["event_id"] == "evt-1"
    assert feishu_audit_rows[0]["metadata"]["source"] == "local-feishu-webhook-jsonl"
    onboarding_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "feishu_onboarding_sessions"
        for row in rows
    ]
    assert onboarding_rows[0]["session_id"] == "ob_1"
    assert onboarding_rows[0]["metadata"]["source"] == "local-feishu-onboarding-sessions"
    offset_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "channel_offsets"
        for row in rows
    ]
    assert offset_rows[0]["key"] == "telegram\x1ftelegram-main"
    assert offset_rows[0]["offset_value"] == 12345
    cron_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "cron_runs"
        for row in rows
    ]
    assert cron_rows[0]["job_id"] == "system-ping"
    assert cron_rows[0]["output_preview"] == "Ping"
    news_rows = [
        row
        for table, rows, _ in writer.batches
        if table == "news_items"
        for row in rows
    ]
    assert {row["state"] for row in news_rows} == {"seen", "collected"}
    assert {row["item_id"] for row in news_rows} == {"news-1", "news-2"}


def test_backfill_local_state_flushes_bulk_batches(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for index in range(3):
        event_path = settings.events_dir / f"events-2026-06-2{index}.jsonl"
        event_path.write_text(
            json.dumps(
                {
                    "event_id": f"evt-{index}",
                    "timestamp": float(index),
                    "type": "inbound.received",
                    "status": "ok",
                    "component": "test",
                    "message": "received",
                    "metadata": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
    writer = RecordingWriter()

    report = backfill_local_state_to_repository(settings, writer, batch_size=2)

    event_batches = [
        rows
        for table, rows, _ in writer.batches
        if table == "runtime_events"
    ]
    assert [len(rows) for rows in event_batches] == [2, 1]
    assert report.written["runtime_events"] == 3
