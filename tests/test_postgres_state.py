from agent_gateway.runtime.state.postgres import (
    POSTGRES_STATE_TABLES,
    PostgresReadRepository,
    PostgresWriteRepository,
    build_postgres_schema_sql,
    check_postgres_schema,
    initialize_postgres_schema,
)


def test_postgres_state_tables_cover_core_runtime_entities() -> None:
    table_names = [table.name for table in POSTGRES_STATE_TABLES]

    assert table_names == [
        "agents",
        "bindings",
        "profiles",
        "channels",
        "delivery_entries",
        "sessions",
        "tasks",
        "runtime_events",
        "errors",
        "metrics",
        "memory_entries",
        "config_audits",
        "webhook_dedup_entries",
        "feishu_webhook_events",
        "feishu_onboarding_sessions",
        "channel_offsets",
        "cron_runs",
        "news_items",
        "user_profiles",
        "weight_logs",
        "meal_logs",
        "daily_nutrition_summaries",
        "diet_plans",
        "feishu_card_states",
        "session_lanes",
        "session_lane_events",
    ]
    assert POSTGRES_STATE_TABLES[0].primary_key == "id"
    assert "tool_policy" in POSTGRES_STATE_TABLES[0].columns
    assert "match_key" in POSTGRES_STATE_TABLES[1].columns
    assert "api_key_env" in POSTGRES_STATE_TABLES[2].columns
    assert "account_id" in POSTGRES_STATE_TABLES[3].columns
    assert "next_retry_at" in POSTGRES_STATE_TABLES[4].columns
    assert "locked_by" in POSTGRES_STATE_TABLES[4].columns
    assert "locked_at" in POSTGRES_STATE_TABLES[4].columns
    assert "session_key" in POSTGRES_STATE_TABLES[5].columns
    assert "payload" in POSTGRES_STATE_TABLES[6].columns
    assert "correlation_id" in POSTGRES_STATE_TABLES[7].columns
    assert "category" in POSTGRES_STATE_TABLES[8].columns
    assert "labels" in POSTGRES_STATE_TABLES[9].columns
    assert "content" in POSTGRES_STATE_TABLES[10].columns
    assert "actor" in POSTGRES_STATE_TABLES[11].columns
    assert "expires_at" in POSTGRES_STATE_TABLES[12].columns
    assert "body_sha256" in POSTGRES_STATE_TABLES[13].columns
    assert "binding_code" in POSTGRES_STATE_TABLES[14].columns
    assert "offset_value" in POSTGRES_STATE_TABLES[15].columns
    assert "output_preview" in POSTGRES_STATE_TABLES[16].columns
    assert "item_id" in POSTGRES_STATE_TABLES[17].columns
    assert "user_scope" in POSTGRES_STATE_TABLES[18].columns
    assert "weight_kg" in POSTGRES_STATE_TABLES[19].columns
    assert "estimated_calories" in POSTGRES_STATE_TABLES[20].columns
    assert "actual_calories" in POSTGRES_STATE_TABLES[21].columns
    assert "target_calories" in POSTGRES_STATE_TABLES[22].columns
    assert "page_index" in POSTGRES_STATE_TABLES[23].columns
    assert "owner_token" in POSTGRES_STATE_TABLES[24].columns
    assert "renewed_at" in POSTGRES_STATE_TABLES[24].columns
    assert POSTGRES_STATE_TABLES[25].primary_key == "id"
    assert "occurred_at" in POSTGRES_STATE_TABLES[25].columns


def test_postgres_schema_sql_covers_tables_and_indexes() -> None:
    sql = build_postgres_schema_sql()

    assert 'CREATE TABLE IF NOT EXISTS "agents"' in sql
    assert '"tool_policy" JSONB NOT NULL DEFAULT' in sql
    assert 'CREATE TABLE IF NOT EXISTS "sessions"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "delivery_entries"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "webhook_dedup_entries"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "feishu_webhook_events"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "feishu_onboarding_sessions"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "channel_offsets"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "cron_runs"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "news_items"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "user_profiles"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "weight_logs"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "meal_logs"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "daily_nutrition_summaries"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "diet_plans"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "feishu_card_states"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "session_lanes"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "session_lane_events"' in sql
    assert '"metadata" JSONB NOT NULL DEFAULT' in sql
    assert '"ttl_seconds" INTEGER NOT NULL DEFAULT 0' in sql
    assert 'PRIMARY KEY ("id")' in sql
    assert 'PRIMARY KEY ("key")' in sql
    assert 'PRIMARY KEY ("session_key")' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_bindings_agent_id"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_delivery_entries_state_next_retry_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_delivery_entries_locked_by_locked_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_runtime_events_component_status_timestamp"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_feishu_webhook_events_outcome_received_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_feishu_onboarding_sessions_binding_code"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_channel_offsets_channel_account_id"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_cron_runs_job_id_run_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_news_items_store_name_state"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_meal_logs_user_scope_meal_date"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_diet_plans_user_scope_plan_date"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_feishu_card_states_owner_account_id_updated_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_session_lanes_state_updated_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_session_lanes_worker_id_updated_at"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_session_lane_events_session_key_occurred_at"' in sql
    assert 'ALTER TABLE "delivery_entries" ADD COLUMN IF NOT EXISTS "locked_by"' in sql
    assert 'ALTER TABLE "delivery_entries" ADD COLUMN IF NOT EXISTS "locked_at"' in sql
    assert 'FROM "feishu_dedup_entries"' in sql


def test_postgres_schema_sql_migrates_old_delivery_queue_tables() -> None:
    sql = build_postgres_schema_sql()

    assert (
        'ALTER TABLE "delivery_entries" '
        'ADD COLUMN IF NOT EXISTS "locked_by" TEXT NOT NULL DEFAULT \'\';'
    ) in sql
    assert (
        'ALTER TABLE "delivery_entries" '
        'ADD COLUMN IF NOT EXISTS "locked_at" DOUBLE PRECISION NOT NULL DEFAULT 0;'
    ) in sql
    assert '"locked_at" DOUBLE PRECISION NOT NULL DEFAULT 0' in sql
    assert sql.index('ALTER TABLE "delivery_entries" ADD COLUMN IF NOT EXISTS "locked_by"') < sql.index(
        'CREATE INDEX IF NOT EXISTS "idx_delivery_entries_locked_by_locked_at"'
    )


def test_initialize_postgres_schema_runs_generated_sql(monkeypatch) -> None:
    calls = []

    def fake_run_psql_sql(*, url: str, sql: str, connect_timeout_seconds: float) -> None:
        calls.append((url, sql, connect_timeout_seconds))

    monkeypatch.setattr(
        "agent_gateway.runtime.state.postgres._run_psql_sql",
        fake_run_psql_sql,
    )

    sql = initialize_postgres_schema(url="postgresql://local/db", connect_timeout_seconds=1.5)

    assert calls == [("postgresql://local/db", sql, 1.5)]
    assert 'CREATE TABLE IF NOT EXISTS "agents"' in sql
    assert 'ALTER TABLE "delivery_entries" ADD COLUMN IF NOT EXISTS "locked_by"' in sql


def test_postgres_read_repository_formats_time_fields(monkeypatch) -> None:
    class Completed:
        stdout = (
            '{"row": {"event_id": "e1", "timestamp": 1782615325.1191509, '
            '"metadata": {"updated_at": 1782615324.1962163}}}'
        )

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/psql")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Completed())
    repo = PostgresReadRepository(url="postgresql://local/db", enabled=True)

    rows = repo.query("runtime_events", sql="SELECT 1")

    assert rows[0]["row"]["timestamp_time"].startswith("2026年06月28日 ")
    assert rows[0]["row"]["timestamp_time"].endswith("分")
    assert rows[0]["row"]["metadata"]["updated_at_time"].startswith("2026年06月28日 ")


def test_check_postgres_schema_reports_drift(monkeypatch) -> None:
    rows = [
        {"table": "agents", "column": "id", "type": "text"},
        {"table": "agents", "column": "name", "type": "text"},
        {"table": "agents", "column": "personality", "type": "text"},
        {"table": "agents", "column": "model", "type": "text"},
        {"table": "agents", "column": "dm_scope", "type": "text"},
        {"table": "agents", "column": "extra_system", "type": "text"},
        {"table": "agents", "column": "tool_policy", "type": "jsonb"},
        {"table": "agents", "column": "memory_policy", "type": "jsonb"},
        {"table": "agents", "column": "prompt_policy", "type": "jsonb"},
        {"table": "agents", "column": "updated_at", "type": "text"},
    ]

    class Completed:
        stdout = "\n".join(__import__("json").dumps(row) for row in rows)

    def fake_run(command, **kwargs):
        return Completed()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/psql")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = check_postgres_schema(
        url="postgresql://local/db",
        tables=(POSTGRES_STATE_TABLES[0], POSTGRES_STATE_TABLES[1]),
    )

    assert result.ok is False
    assert result.missing_tables == ["bindings"]
    assert result.type_mismatches["agents"]["updated_at"] == {
        "expected": "double precision",
        "actual": "text",
    }
    assert result.to_dict()["ok"] is False


def test_postgres_write_query_sends_sql_via_stdin(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

        class Completed:
            stdout = ""

        return Completed()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/psql")
    monkeypatch.setattr("subprocess.run", fake_run)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    repo.query("agents", sql="SELECT 1")

    command, kwargs = calls[0]
    assert "-v" in command
    assert "ON_ERROR_STOP=1" in command
    assert "-f" in command
    assert "-" in command
    assert "-c" not in command
    assert kwargs["input"] == "SELECT 1"


def test_postgres_write_bulk_upsert_batches_rows(monkeypatch) -> None:
    calls = []

    def fake_query(self, table, *, sql, params=None):
        calls.append((table, sql, params))
        return []

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    written = repo.bulk_upsert(
        "agents",
        [
            {
                "id": "a1",
                "name": "Agent 1",
                "personality": "",
                "model": "",
                "dm_scope": "per-peer",
                "extra_system": "",
                "tool_policy": {},
                "memory_policy": {},
                "prompt_policy": {},
                "updated_at": 1.0,
            },
            {
                "id": "a2",
                "name": "Agent 2",
                "personality": "",
                "model": "",
                "dm_scope": "per-peer",
                "extra_system": "",
                "tool_policy": {},
                "memory_policy": {},
                "prompt_policy": {},
                "updated_at": 2.0,
            },
        ],
        batch_size=1,
    )

    assert written == 2
    assert len(calls) == 2
    assert "json_populate_recordset" in calls[0][1]


def test_postgres_write_metric_normalizes_snapshot_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_metric({"timestamp": 1.0, "runtime": {"uptime_seconds": 9}})

    assert captured[0][0] == "metrics"
    assert row["id"] == "metric_1000"
    assert row["kind"] == "snapshot"
    assert row["name"] == "runtime"
    assert row["metadata"]["runtime"]["uptime_seconds"] == 9


def test_postgres_read_session_messages_sanitizes_snapshot_rows(monkeypatch) -> None:
    rows = [
        {
            "id": "research:system:heartbeat:research:snapshot",
            "agent_id": "research",
            "session_key": "system:heartbeat:research",
            "metadata": {
                "kind": "snapshot",
                "messages": [
                    {"role": "user", "content": "heartbeat"},
                    {"role": "assistant", "content": []},
                    {"role": "assistant", "content": [{"type": "text", "text": ""}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        }
    ]

    monkeypatch.setattr(PostgresReadRepository, "_list_session_rows", lambda *args, **kwargs: rows)
    repo = PostgresReadRepository(url="postgresql://local/db", enabled=True)

    messages = repo.read_session_messages("research", "system:heartbeat:research")

    assert messages == [
        {"role": "user", "content": "heartbeat"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]


def test_postgres_write_session_snapshot_sanitizes_messages(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.rewrite_session_messages(
        "research",
        "system:heartbeat:research",
        [
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    )

    assert captured[0][0] == "sessions"
    assert row["message_count"] == 2
    assert row["metadata"]["messages"] == [
        {"role": "user", "content": "heartbeat"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]


def test_postgres_write_session_message_skips_empty_content(monkeypatch) -> None:
    captured = []

    def fake_append(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "append", fake_append)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_session_message("research", "system:heartbeat:research", "assistant", [])

    assert row == {}
    assert captured == []


def test_postgres_write_alert_normalizes_alert_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_alert(
        {
            "timestamp": 2.0,
            "event": "triggered",
            "message": "disk high",
            "rule": {"severity": "critical"},
        }
    )

    assert captured[0][0] == "errors"
    assert row["id"] == "alert_2000"
    assert row["event_id"] == "alert_2000"
    assert row["category"] == "triggered"
    assert row["severity"] == "critical"


def test_postgres_write_runtime_rows_use_upsert(monkeypatch) -> None:
    calls = []

    def fake_upsert(self, table, row):
        calls.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    repo.write_task({"id": "task-1", "task_type": "cron", "source": "test"})
    repo.write_event({"event_id": "evt-1", "timestamp": 1.0, "type": "test", "status": "ok", "component": "test", "message": "ok"})
    repo.write_memory("remember", category="note", user_scope="user:alice")
    repo.write_metric({"timestamp": 1.0, "runtime": {}})
    repo.write_alert({"timestamp": 1.0, "message": "alert"})

    assert [table for table, _ in calls] == [
        "tasks",
        "runtime_events",
        "memory_entries",
        "metrics",
        "errors",
    ]
    memory_row = calls[2][1]
    assert memory_row["metadata"]["user_scope"] == "user:alice"


def test_postgres_write_delivery_entry_normalizes_state(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_delivery_entry(
        {
            "id": "delivery-1",
            "channel": "cli",
            "to": "peer-1",
            "text": "hello",
            "metadata": {"kind": "reply"},
            "enqueued_at": 1.0,
            "next_retry_at": 0.0,
        },
        state="failed",
    )

    assert captured[0][0] == "delivery_entries"
    assert row["state"] == "failed"
    assert row["id"] == "delivery-1"
    assert row["updated_at"] > 0


def test_postgres_write_webhook_event_dedup_uses_insert_do_nothing(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params))
        return [{"row": {"inserted": True}}]

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    assert repo.mark_webhook_event_if_new("account:evt-1", seen_at=1.0, expires_at=61.0) is True

    table, sql, params = captured[0]
    assert table == "webhook_dedup_entries"
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert params["row"]["event_id"] == "account:evt-1"
    assert params["row"]["expires_at"] == 61.0


def test_postgres_write_feishu_webhook_event_appends_row(monkeypatch) -> None:
    captured = []

    def fake_append(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "append", fake_append)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_feishu_webhook_event(
        {
            "id": "audit-1",
            "received_at": 1.0,
            "outcome": "accepted",
            "reason": "ok",
            "http_status": 200,
            "channel_account": "default",
            "event_id": "evt-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "chat_type": "p2p",
            "sender_open_id": "ou_1",
            "sender_user_id": "u_1",
            "body_sha256": "abc",
            "metadata": {},
        }
    )

    assert captured[0][0] == "feishu_webhook_events"
    assert row["event_id"] == "evt-1"


def test_postgres_reserve_delivery_uses_skip_locked(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params or {}))
        return [
            {
                "row": {
                    "id": "del-1",
                    "state": "running",
                    "channel": "cli",
                    "to": "peer-1",
                    "text": "hello",
                    "retry_count": 0,
                    "last_error": "",
                    "metadata": {},
                    "enqueued_at": 1.0,
                    "next_retry_at": 0.0,
                    "locked_by": "worker-a",
                    "locked_at": 10.0,
                    "updated_at": 10.0,
                }
            }
        ]

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.reserve_delivery(worker_id="worker-a", now=10.0)

    assert row is not None
    assert row["id"] == "del-1"
    table, sql, params = captured[0]
    assert table == "delivery_entries"
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "state = 'running'" in sql
    assert params["states"] == ["pending", "retrying"]
    assert params["worker_id"] == "worker-a"
    assert params["now"] == 10.0


def test_postgres_write_feishu_onboarding_session_upserts_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_feishu_onboarding_session(
        {
            "session_id": "ob_1",
            "binding_code": "GATEWAY-ABC123",
            "mode": "personal",
            "status": "pending",
            "account_id": "feishu-long-local",
            "agent_id": "",
            "agent_name": "",
            "created_at": 1.0,
            "expires_at": 901.0,
            "bound_at": 0.0,
            "bound_peer_id": "",
            "bound_sender_id": "",
            "bound_is_group": False,
            "last_error": "",
            "updated_at": 1.0,
            "metadata": {},
        }
    )

    assert captured[0][0] == "feishu_onboarding_sessions"
    assert row["session_id"] == "ob_1"


def test_postgres_write_channel_offset_upserts_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_channel_offset("telegram", "telegram-main", 42)

    assert captured[0][0] == "channel_offsets"
    assert row["key"] == "telegram\x1ftelegram-main"
    assert row["offset_value"] == 42


def test_postgres_write_cron_run_upserts_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_cron_run(
        {
            "id": "system-ping:1",
            "job_id": "system-ping",
            "config_id": "global",
            "agent_id": "main",
            "scope": "global",
            "run_at": 1.0,
            "status": "ok",
            "output_preview": "Ping",
            "error": "",
            "metadata": {},
        }
    )

    assert captured[0][0] == "cron_runs"
    assert row["job_id"] == "system-ping"


def test_postgres_write_news_item_upserts_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_news_item(
        {
            "key": "news-digest\x1fseen\x1fitem-1",
            "store_name": "news-digest",
            "state": "seen",
            "item_id": "item-1",
            "source_id": "source",
            "source_type": "github_releases",
            "title": "Title",
            "url": "https://example.com",
            "published_at": "2026-06-28T10:00:00Z",
            "summary": "Summary",
            "tags": ["agent"],
            "seen_at": 1.0,
            "collected_at": 0.0,
            "updated_at": 1.0,
            "metadata": {},
        }
    )

    assert captured[0][0] == "news_items"
    assert row["item_id"] == "item-1"


def test_postgres_write_feishu_card_state_upserts_row(monkeypatch) -> None:
    captured = []

    def fake_upsert(self, table, row):
        captured.append((table, row))
        return row

    monkeypatch.setattr(PostgresWriteRepository, "upsert", fake_upsert)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_feishu_card_state(
        {
            "card_id": "card-1",
            "owner_channel": "feishu",
            "owner_account_id": "feishu-main",
            "peer_id": "oc_chat",
            "message_id": "om_1",
            "title": "Card",
            "summary": "Summary",
            "template": "blue",
            "card_link": "",
            "blocks": ["block"],
            "structured_blocks": [],
            "actions": [],
            "page_size": 4,
            "page_index": 1,
            "expanded": False,
            "updated_at": 1.0,
            "metadata": {},
        }
    )

    assert captured[0][0] == "feishu_card_states"
    assert row["card_id"] == "card-1"


def test_postgres_write_session_lane_normalizes_row(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params))
        return []

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_session_lane(
        {
            "session_key": "agent:feishu:user-1",
            "lane_key": "gateway:lane:agent:feishu:user-1",
            "worker_id": "worker-a",
            "task_id": "task-a",
            "owner_token": "worker-a:task-a",
            "ttl_seconds": 30,
            "acquired_at": 1.0,
            "renewed_at": 2.0,
            "metadata": {"source": "redis"},
        }
    )

    assert row["session_key"] == "agent:feishu:user-1"
    assert row["state"] == "owned"
    assert row["ttl_seconds"] == 30
    assert row["metadata"] == {"source": "redis"}
    table, sql, params = captured[0]
    assert table == "session_lanes"
    assert "ON CONFLICT" in sql
    assert params["row"]["owner_token"] == "worker-a:task-a"


def test_postgres_release_session_lane_checks_owner_token(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params))
        return [{"row": {"released": True}}]

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    released = repo.release_session_lane(
        "agent:feishu:user-1",
        owner_token="worker-a:task-a",
        reason="worker expired",
        now=3.0,
    )

    assert released is True
    table, sql, params = captured[0]
    assert table == "session_lanes"
    assert "owner_token = %(owner_token)s" in sql
    assert "state = 'released'" in sql
    assert params == {
        "session_key": "agent:feishu:user-1",
        "owner_token": "worker-a:task-a",
        "release_metadata": {"release_reason": "worker expired", "released_at": 3.0},
        "now": 3.0,
    }


def test_postgres_write_session_lane_event_appends_history(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params))
        return [params["row"]]

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.write_session_lane_event(
        {
            "session_key": "agent:feishu:user-1",
            "lane_key": "gateway:lane:agent:feishu:user-1",
            "worker_id": "worker-a",
            "task_id": "task-a",
            "owner_token": "worker-a:task-a",
            "event": "acquired",
            "ttl_seconds": 30,
            "occurred_at": 10.0,
            "metadata": {"source": "redis"},
        }
    )

    assert row["id"].startswith("agent:feishu:user-1:acquired:worker-a:task-a:")
    table, sql, params = captured[0]
    assert table == "session_lane_events"
    assert "INSERT INTO session_lane_events" in sql
    assert params["row"]["event"] == "acquired"
    assert params["row"]["occurred_at"] == 10.0
    assert params["row"]["metadata"] == {"source": "redis"}


def test_postgres_write_reserve_task_uses_atomic_update(monkeypatch) -> None:
    captured = []

    def fake_query(self, table, *, sql, params=None):
        captured.append((table, sql, params))
        return [
            {
                "row": {
                    "id": "task-1",
                    "task_type": "cron",
                    "source": "scheduler",
                    "status": "running",
                    "agent_id": "",
                    "session_key": "",
                    "priority": 10,
                    "idempotency_key": "",
                    "payload": {},
                    "result_preview": "",
                    "error": "",
                    "retry_count": 0,
                    "created_at": 1.0,
                    "updated_at": 2.0,
                    "started_at": 2.0,
                    "finished_at": 0.0,
                    "metadata": {"worker_id": "worker-1"},
                }
            }
        ]

    monkeypatch.setattr(PostgresWriteRepository, "query", fake_query)
    repo = PostgresWriteRepository(url="postgresql://local/db", enabled=True)

    row = repo.reserve_task(worker_id="worker-1", task_types=["cron"], now=2.0)

    assert row is not None
    assert row["id"] == "task-1"
    table, sql, params = captured[0]
    assert table == "tasks"
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE tasks SET" in sql
    assert params["worker_metadata"] == {"worker_id": "worker-1"}
    assert params["task_types"] == ["cron"]
