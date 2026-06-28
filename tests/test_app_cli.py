import asyncio
import json
import sys

from agent_gateway import app as gateway_app
from agent_gateway.app import (
    build_dashboard_websocket_url,
    trigger_cron_once,
    trigger_cron_once_with_timeout,
)
from agent_gateway.config import GatewaySettings


class FakeCron:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.triggered: list[str] = []

    async def trigger_job(self, job_id: str) -> str:
        self.triggered.append(job_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        return f"{job_id} triggered"


class FakeAutonomyRuntime:
    def __init__(self, cron: FakeCron) -> None:
        self.cron = cron


class FakeDeliveryRuntime:
    def __init__(self, queue: "FakeDeliveryQueue") -> None:
        self.queue = queue
        self.flush_calls = 0

    def pending_count(self) -> int:
        return len(self.queue.pending_entries())

    async def flush_once(self) -> None:
        self.flush_calls += 1
        self.queue.pop_one()


class FakeDeliveryEntry:
    def __init__(self, entry_id: str, last_error: str | None = None) -> None:
        self.id = entry_id
        self.last_error = last_error


class FakeDeliveryQueue:
    def __init__(self, pending: int) -> None:
        self.entries = [
            FakeDeliveryEntry(f"delivery-{index + 1}")
            for index in range(pending)
        ]

    def pending_entries(self) -> list[FakeDeliveryEntry]:
        return list(self.entries)

    def pop_one(self) -> None:
        if self.entries:
            self.entries.pop(0)


class FakeApp:
    def __init__(self, *, pending: int = 0, cron_delay: float = 0.0) -> None:
        self.autonomy_runtime = FakeAutonomyRuntime(FakeCron(delay=cron_delay))
        self.delivery_queue = FakeDeliveryQueue(pending)
        self.delivery_runtime = FakeDeliveryRuntime(self.delivery_queue)


def test_trigger_cron_once_flushes_delivery_queue() -> None:
    app = FakeApp(pending=2)

    result = asyncio.run(trigger_cron_once(app, "agent-news-digest", flush_rounds=3))

    assert result == {
        "job_id": "agent-news-digest",
        "result": "agent-news-digest triggered",
        "pending_before_flush": 2,
        "pending_after_flush": 0,
        "pending_ids": [],
        "pending_errors": {},
    }
    assert app.delivery_runtime.flush_calls == 2


def test_trigger_cron_once_reports_remaining_pending_delivery() -> None:
    app = FakeApp(pending=3)
    app.delivery_queue.entries[-1].last_error = "delivery failed"

    result = asyncio.run(trigger_cron_once(app, "agent-news-digest", flush_rounds=1))

    assert result == {
        "job_id": "agent-news-digest",
        "result": "agent-news-digest triggered",
        "pending_before_flush": 3,
        "pending_after_flush": 1,
        "pending_ids": ["delivery-3"],
        "pending_errors": {"delivery-3": "delivery failed"},
    }
    assert app.delivery_runtime.flush_calls == 2


def test_trigger_cron_once_with_timeout_returns_timeout_result() -> None:
    app = FakeApp(cron_delay=0.05)

    result = asyncio.run(
        trigger_cron_once_with_timeout(
            app,
            "agent-news-digest",
            timeout_seconds=0.01,
        )
    )

    assert result == {
        "job_id": "agent-news-digest",
        "result": "timeout",
        "timeout_seconds": 0.01,
        "pending_after_timeout": 0,
    }


def test_build_dashboard_websocket_url_uses_loopback_for_wildcard_host() -> None:
    settings = GatewaySettings(host="0.0.0.0", port=8765)

    assert build_dashboard_websocket_url(settings) == "ws://127.0.0.1:8765"


def test_build_dashboard_websocket_url_wraps_ipv6_host() -> None:
    settings = GatewaySettings(host="::1", port=8765)

    assert build_dashboard_websocket_url(settings) == "ws://[::1]:8765"


def test_postgres_init_print_sql_does_not_build_application(
    monkeypatch,
    capsys,
) -> None:
    def fail_build_application():
        raise AssertionError("postgres-init --print-sql must not build the gateway app")

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "postgres-init", "--print-sql"])
    monkeypatch.setattr(gateway_app, "build_application", fail_build_application)

    gateway_app.main()

    output = capsys.readouterr().out
    assert 'CREATE TABLE IF NOT EXISTS "agents"' in output
    assert 'CREATE TABLE IF NOT EXISTS "runtime_events"' in output


def test_postgres_init_execute_initializes_schema(monkeypatch, capsys) -> None:
    calls = []

    def fake_initialize_postgres_schema(*, url: str, connect_timeout_seconds: float):
        calls.append((url, connect_timeout_seconds))
        return "schema sql"

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "postgres-init"])
    monkeypatch.setenv("GATEWAY_POSTGRES_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
    monkeypatch.setattr(gateway_app, "initialize_postgres_schema", fake_initialize_postgres_schema)

    gateway_app.main()

    assert calls == [("postgresql://postgres:postgres@127.0.0.1:5432/postgres", 2.0)]
    assert "'result': 'ok'" in capsys.readouterr().out


def test_postgres_check_schema_does_not_build_application(monkeypatch, capsys) -> None:
    calls = []

    class FakeResult:
        def to_dict(self):
            return {"ok": True, "missing_tables": []}

    def fake_check_postgres_schema(*, url: str, connect_timeout_seconds: float):
        calls.append((url, connect_timeout_seconds))
        return FakeResult()

    def fail_build_application():
        raise AssertionError("postgres-check-schema must not build the gateway app")

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "postgres-check-schema"])
    monkeypatch.setenv("GATEWAY_POSTGRES_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
    monkeypatch.setattr(gateway_app, "check_postgres_schema", fake_check_postgres_schema)
    monkeypatch.setattr(gateway_app, "build_application", fail_build_application)

    gateway_app.main()

    assert calls == [("postgresql://postgres:postgres@127.0.0.1:5432/postgres", 2.0)]
    assert "'ok': True" in capsys.readouterr().out


def test_postgres_migrate_local_dry_run_uses_migration_path(monkeypatch, capsys) -> None:
    calls = []

    class FakeWriter:
        def __init__(self, *, url: str, enabled: bool, connect_timeout_seconds: float) -> None:
            calls.append(("writer", url, enabled, connect_timeout_seconds))

    def fake_backfill(settings, writer, *, dry_run: bool):
        calls.append(("backfill", settings, writer, dry_run))

        class FakeReport:
            def to_dict(self):
                return {"dry_run": dry_run, "written": {}}

        return FakeReport()

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "postgres-migrate-local", "--dry-run"])
    monkeypatch.setattr(gateway_app, "PostgresWriteRepository", FakeWriter)
    monkeypatch.setattr(gateway_app, "backfill_local_state_to_repository", fake_backfill)

    gateway_app.main()

    assert calls[0][0] == "writer"
    assert calls[0][2] is True
    assert calls[1][0] == "backfill"
    assert calls[1][3] is True
    assert "'dry_run': True" in capsys.readouterr().out


def test_postgres_smoke_runs_verification_without_serving(monkeypatch, capsys) -> None:
    calls = []

    def fake_run_postgres_smoke(settings):
        calls.append(settings)
        return {"result": "ok", "marker": "pg-smoke-test"}

    def fail_build_application():
        raise AssertionError("postgres-smoke must not enter serve path")

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "postgres-smoke"])
    monkeypatch.setattr(gateway_app, "run_postgres_smoke", fake_run_postgres_smoke)
    monkeypatch.setattr(gateway_app, "build_application", fail_build_application)

    gateway_app.main()

    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "'result': 'ok'" in output
    assert "pg-smoke-test" in output


def test_doctor_cli_prints_json_without_building_application(monkeypatch, capsys) -> None:
    def fail_build_application():
        raise AssertionError("doctor must not build the gateway app")

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "doctor", "--json"])
    monkeypatch.setattr(gateway_app, "build_application", fail_build_application)
    monkeypatch.setattr(
        gateway_app,
        "run_doctor",
        lambda settings, env_file=None: {
            "ok": True,
            "summary": {"pass": 1, "warn": 0, "fail": 0},
            "checks": [{"status": "pass", "name": "test", "message": "ok", "detail": {}}],
        },
    )

    gateway_app.main()

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["checks"][0]["name"] == "test"


def test_doctor_cli_exits_nonzero_on_fail(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["agent-gateway", "doctor"])
    monkeypatch.setattr(
        gateway_app,
        "run_doctor",
        lambda settings, env_file=None: {
            "ok": False,
            "summary": {"pass": 0, "warn": 0, "fail": 1},
            "checks": [{"status": "fail", "name": "model.api_key", "message": "missing", "detail": {}}],
        },
    )

    try:
        gateway_app.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:  # pragma: no cover - regression guard.
        raise AssertionError("doctor failure should exit nonzero")
    assert "FAIL" in capsys.readouterr().out


def test_delivery_republish_cli_republishes_without_serving(monkeypatch, capsys) -> None:
    calls = []

    class FakeControlPlane:
        def republish_deliveries(self, *, include_pending: bool, include_retrying: bool):
            calls.append((include_pending, include_retrying))
            return {"published": 2, "states": ["pending", "retrying"]}

    class FakeBuiltApp:
        control_plane = FakeControlPlane()

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "delivery-republish"])
    monkeypatch.setattr(gateway_app, "build_application", lambda: FakeBuiltApp())

    gateway_app.main()

    assert calls == [(True, True)]
    assert "'published': 2" in capsys.readouterr().out


def test_lane_doctor_cli_prints_json_without_serving(monkeypatch, capsys) -> None:
    class FakeControlPlane:
        def lane_doctor(self, *, limit: int):
            return {
                "ok": True,
                "status": "ok",
                "limit": limit,
                "summary": {"owned_lanes": 1},
                "checks": [],
            }

    class FakeBuiltApp:
        control_plane = FakeControlPlane()

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "lane-doctor", "--json", "--limit", "7"])
    monkeypatch.setattr(gateway_app, "build_application", lambda: FakeBuiltApp())

    gateway_app.main()

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["limit"] == 7
    assert output["summary"]["owned_lanes"] == 1


def test_lane_doctor_cli_prints_text(monkeypatch, capsys) -> None:
    class FakeControlPlane:
        def lane_doctor(self, *, limit: int):
            return {
                "ok": False,
                "status": "warning",
                "limit": limit,
                "summary": {
                    "ready": False,
                    "owned_lanes": 1,
                    "stale_lanes": 1,
                    "recovery_actions": 1,
                    "broker_messages": 2,
                    "broker_dead_letters": 1,
                },
                "checks": [
                    {"name": "session_lanes", "status": "warning", "owned": 1, "stale": 1},
                ],
                "readiness": {
                    "ready": False,
                    "status": "not_ready",
                    "passed": 7,
                    "failed": 1,
                    "checks": [
                        {
                            "name": "inbound_broker.rabbitmq",
                            "status": "fail",
                            "ok": False,
                            "message": "需要设置 GATEWAY_INBOUND_BROKER=rabbitmq 并确认 broker 可用",
                        }
                    ],
                },
                "recovery_plan": {"action_count": 1},
            }

    class FakeBuiltApp:
        control_plane = FakeControlPlane()

    monkeypatch.setattr(sys, "argv", ["agent-gateway", "lane-doctor"])
    monkeypatch.setattr(gateway_app, "build_application", lambda: FakeBuiltApp())

    gateway_app.main()

    output = capsys.readouterr().out
    assert "分布式 Lane 诊断：warning" in output
    assert "最终形态就绪：not_ready passed=7 failed=1" in output
    assert "FAIL inbound_broker.rabbitmq" in output
    assert "恢复建议" in output
