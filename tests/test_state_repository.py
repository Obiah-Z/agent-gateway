from pathlib import Path

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.factory import build_state_repository
from agent_gateway.runtime.state.migration import CompositeMigrationSink
from agent_gateway.runtime.state.postgres import PostgresReadRepository, PostgresWriteRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.store import LocalTaskStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.state import STATE_TABLES, StateRepository


def test_state_repository_exposes_expected_tables() -> None:
    assert STATE_TABLES == (
        "sessions",
        "tasks",
        "runtime_events",
        "errors",
        "metrics",
        "memory_entries",
        "config_audits",
    )


def test_state_repository_protocol_is_runtime_checkable() -> None:
    class DummyRepository:
        def list(self, table, *, limit=50, cursor="", filters=None):
            return []

        def get(self, table, key):
            return None

        def append(self, table, row):
            return row

        def upsert(self, table, row):
            return row

        def delete(self, table, key):
            return True

        def query(self, table, *, sql, params=None):
            return []

    assert isinstance(DummyRepository(), StateRepository)


def test_build_state_repository_returns_local_backend(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        postgres_enabled=False,
    )
    settings.ensure_directories()
    bundle = build_state_repository(
        settings,
        sessions=SessionStore(settings.sessions_dir),
        tasks=LocalTaskStore(settings.tasks_dir),
        events=RuntimeEventStore(settings.events_dir),
        metrics=MetricsStore(settings.metrics_dir),
        alerts=AlertStore(settings.alerts_dir),
        memory=MemoryStore(settings.workspace_root),
    )

    assert isinstance(bundle.read, LocalStateReadRepository)
    assert bundle.write is not None
    assert isinstance(bundle.write, PostgresWriteRepository)


def test_build_state_repository_switches_to_postgres_backend(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        postgres_enabled=True,
        postgres_url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
    )
    settings.ensure_directories()
    sessions = SessionStore(settings.sessions_dir)
    tasks = LocalTaskStore(settings.tasks_dir)
    events = RuntimeEventStore(settings.events_dir)
    metrics = MetricsStore(settings.metrics_dir)
    alerts = AlertStore(settings.alerts_dir)
    memory = MemoryStore(settings.workspace_root)
    bundle = build_state_repository(
        settings,
        sessions=sessions,
        tasks=tasks,
        events=events,
        metrics=metrics,
        alerts=alerts,
        memory=memory,
    )

    assert isinstance(bundle.read, PostgresReadRepository)
    assert bundle.write is not None
    assert bundle.write.enabled is True
    assert sessions.write_backend is bundle.write
    assert tasks.write_backend is bundle.write
    assert events.write_backend is bundle.write
    assert metrics.write_backend is bundle.write
    assert alerts.write_backend is bundle.write
    assert memory.write_backend is bundle.write


def test_build_state_repository_enables_migration_backup_sink(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        postgres_enabled=False,
    )
    settings.ensure_directories()
    sessions = SessionStore(settings.sessions_dir)
    tasks = LocalTaskStore(settings.tasks_dir)
    events = RuntimeEventStore(settings.events_dir)
    memory = MemoryStore(settings.workspace_root)

    bundle = build_state_repository(
        settings,
        sessions=sessions,
        tasks=tasks,
        events=events,
        metrics=MetricsStore(settings.metrics_dir),
        alerts=AlertStore(settings.alerts_dir),
        memory=memory,
    )

    assert bundle.backup is not None
    assert isinstance(bundle.backup, CompositeMigrationSink)
    assert sessions.backup_sink is bundle.backup
    assert tasks.backup_sink is bundle.backup
    assert events.backup_sink is bundle.backup
    assert memory.backup_sink is bundle.backup
    assert bundle.write is not None
    assert sessions.write_backend is None
    assert tasks.write_backend is None
    assert events.write_backend is None
    assert memory.write_backend is None


def test_composite_migration_sink_fans_out_to_all_sinks() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

        def write_session_message(self, *args, **kwargs):
            self.calls.append(("write_session_message", args, kwargs))

        def rewrite_session_messages(self, *args, **kwargs):
            self.calls.append(("rewrite_session_messages", args, kwargs))

        def write_task(self, *args, **kwargs):
            self.calls.append(("write_task", args, kwargs))

        def write_event(self, *args, **kwargs):
            self.calls.append(("write_event", args, kwargs))

        def write_memory(self, *args, **kwargs):
            self.calls.append(("write_memory", args, kwargs))

    left = Recorder()
    right = Recorder()
    sink = CompositeMigrationSink((left, right))

    sink.write_memory("hello", category="test")

    assert left.calls and right.calls


def test_session_store_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def read_session_messages(self, agent_id: str, session_key: str):
            return [{"role": "user", "content": "from-db"}]

    store = SessionStore(tmp_path / "sessions")
    store.read_backend = FakeReadBackend()

    history = store.load_messages("main", "s1")

    assert history == [{"role": "user", "content": "from-db"}]


def test_task_store_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def get(self, table: str, key: str):
            return {
                "id": key,
                "task_type": "cron",
                "source": "db",
                "status": "done",
                "created_at": 1.0,
                "updated_at": 2.0,
            }

        def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
            return [
                {
                    "id": "t1",
                    "task_type": "cron",
                    "source": "db",
                    "status": "done",
                    "created_at": 1.0,
                    "updated_at": 2.0,
                }
            ]

    store = LocalTaskStore(tmp_path / "tasks")
    store.read_backend = FakeReadBackend()

    task = store.get("t1")
    tasks = store.list(limit=5)

    assert task is not None and task.task_type == "cron"
    assert tasks and tasks[0].id == "t1"


def test_metrics_store_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def list(self, table: str, limit: int = 50, cursor: str = "", filters=None):
            return [{"timestamp": 1.0, "time": "2026-01-01T00:00:00Z", "runtime": {"uptime_seconds": 9}}]

    store = MetricsStore(tmp_path / "metrics")
    store.read_backend = FakeReadBackend()

    latest = store.latest()
    tail = store.tail(limit=5)

    assert latest is not None and latest["runtime"]["uptime_seconds"] == 9
    assert tail and tail[0]["timestamp"] == 1.0


def test_alert_store_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def list(self, table: str, limit: int = 50, cursor: str = "", filters=None):
            return [{"timestamp": 1.0, "event": "triggered", "message": "alert"}]

    store = AlertStore(tmp_path / "alerts")
    store.read_backend = FakeReadBackend()

    rows = store.tail(limit=5)

    assert rows and rows[0]["event"] == "triggered"


def test_metrics_store_mirrors_to_backup_sink(tmp_path: Path) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.rows: list[dict[str, object]] = []

        def write_metric(self, row: dict[str, object]) -> dict[str, object]:
            self.rows.append(row)
            return row

    store = MetricsStore(tmp_path / "metrics")
    recorder = Recorder()
    store.backup_sink = recorder

    store.record(runtime={"uptime_seconds": 1})

    assert recorder.rows


def test_alert_store_mirrors_to_backup_sink(tmp_path: Path) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.rows: list[dict[str, object]] = []

        def write_alert(self, row: dict[str, object]) -> dict[str, object]:
            self.rows.append(row)
            return row

    store = AlertStore(tmp_path / "alerts")
    recorder = Recorder()
    store.backup_sink = recorder
    rule = type("Rule", (), {"id": "r1", "title": "t", "severity": "warning", "description": "d", "threshold": 1, "sustain_intervals": 1, "cooldown_seconds": 10})()
    state = type("State", (), {"to_dict": lambda self=None: {"status": "active"}})()

    store.append(rule=rule, state=state, event="triggered", message="msg", value=1.0)

    assert recorder.rows


def test_local_stores_mirror_to_backup_sink(tmp_path: Path) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def write_session_message(self, *args):
            self.calls.append(("write_session_message", args))

        def rewrite_session_messages(self, *args):
            self.calls.append(("rewrite_session_messages", args))

        def write_task(self, task):
            self.calls.append(("write_task", task))

        def write_event(self, event):
            self.calls.append(("write_event", event))

        def write_memory(self, content, category="general"):
            self.calls.append(("write_memory", (content, category)))

    recorder = Recorder()
    sessions = SessionStore(tmp_path / "sessions")
    tasks = LocalTaskStore(tmp_path / "tasks")
    events = RuntimeEventStore(tmp_path / "events")
    memory = MemoryStore(tmp_path / "workspace")
    sessions.backup_sink = recorder
    tasks.backup_sink = recorder
    events.backup_sink = recorder
    memory.backup_sink = recorder

    sessions.append_message("main", "s1", "user", "hi")
    tasks.create(TaskInstance.create(task_type="cron", source="cron"))
    events.record("test.event", status="ok", component="test", message="hello")
    memory.write_memory("remember", category="general")

    kinds = [kind for kind, _ in recorder.calls]
    assert "write_session_message" in kinds
    assert "write_task" in kinds
    assert "write_event" in kinds
    assert "write_memory" in kinds


def test_runtime_stores_prefer_write_backend_over_backup_sink(tmp_path: Path) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def write_session_message(self, *args):
            self.calls.append(("write_session_message", args))

        def rewrite_session_messages(self, *args):
            self.calls.append(("rewrite_session_messages", args))

        def write_task(self, task):
            self.calls.append(("write_task", task))

        def write_event(self, event):
            self.calls.append(("write_event", event))

        def write_memory(self, content, category="general"):
            self.calls.append(("write_memory", (content, category)))

        def write_metric(self, row):
            self.calls.append(("write_metric", row))

        def write_alert(self, row):
            self.calls.append(("write_alert", row))

    primary = Recorder()
    backup = Recorder()
    sessions = SessionStore(tmp_path / "sessions")
    tasks = LocalTaskStore(tmp_path / "tasks")
    events = RuntimeEventStore(tmp_path / "events")
    memory = MemoryStore(tmp_path / "workspace")
    metrics = MetricsStore(tmp_path / "metrics")
    alerts = AlertStore(tmp_path / "alerts")
    for store in (sessions, tasks, events, memory, metrics, alerts):
        store.write_backend = primary
        store.backup_sink = backup

    sessions.append_message("main", "s1", "user", "hi")
    tasks.create(TaskInstance.create(task_type="cron", source="cron"))
    events.record("test.event", status="ok", component="test", message="hello")
    memory.write_memory("remember", category="general")
    metrics.record(runtime={"uptime_seconds": 1})
    rule = type("Rule", (), {"id": "r1", "title": "t", "severity": "warning", "description": "d", "threshold": 1, "sustain_intervals": 1, "cooldown_seconds": 10})()
    state = type("State", (), {"to_dict": lambda self=None: {"status": "active"}})()
    alerts.append(rule=rule, state=state, event="triggered", message="msg", value=1.0)

    kinds = [kind for kind, _ in primary.calls]
    assert "write_session_message" in kinds
    assert "write_task" in kinds
    assert "write_event" in kinds
    assert "write_memory" in kinds
    assert "write_metric" in kinds
    assert "write_alert" in kinds
    assert backup.calls == []


def test_postgres_read_repository_uses_table_specific_keys_and_ordering() -> None:
    repo = PostgresReadRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    _, params = repo._build_list_query(
        "runtime_events",
        limit=10,
        filters={"component": "delivery", "correlation_id": "corr-1"},
    )

    assert repo._primary_key("runtime_events") == "event_id"
    assert repo._order_column("memory_entries") == "created_at"
    assert params["component"] == "delivery"
    assert params["correlation_id"] == "corr-1"


def test_postgres_read_repository_sessions_match_local_summary_shape() -> None:
    repo = PostgresReadRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    rows = repo._list_sessions(limit=5, filters={"agent_id": "main"})

    assert rows == []


def test_postgres_read_repository_error_and_memory_shapes_align() -> None:
    repo = PostgresReadRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    assert repo._list_errors(limit=5, filters={"component": "delivery"}) == []
    assert repo._list_memory_entries(limit=5, filters={"agent_id": "main"}) == []
    assert repo._list_tasks(limit=5, filters={"statuses": ["pending"]}) == []


def test_postgres_memory_entries_expose_formatted_time(monkeypatch) -> None:
    repo = PostgresReadRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    monkeypatch.setattr(
        repo,
        "query",
        lambda table, *, sql, params=None: [
            {
                "row": {
                    "created_at": 1782615325.1191509,
                    "created_at_time": "2026年06月28日 10时55分",
                    "category": "note",
                    "content": "hello",
                    "source_file": "daily.jsonl",
                }
            }
        ],
    )

    rows = repo._list_memory_entries(limit=5, filters={})

    assert rows[0]["ts_time"] == "2026年06月28日 10时55分"


def test_postgres_write_repository_is_available_as_scaffold() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    assert repo.url.startswith("postgresql://")
    assert repo.enabled is False


def test_postgres_write_repository_has_session_specific_paths() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    session_row = {
        "id": "session-1",
        "agent_id": "main",
        "session_key": "agent:main:direct:u-1",
        "message_count": 2,
    }

    assert repo._append_session(session_row) == session_row
    assert repo._upsert_session(session_row) == session_row
    assert repo._delete_session("session-1") is True


def test_postgres_write_repository_has_task_specific_paths() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    task_row = {
        "id": "task-1",
        "task_type": "cron",
        "source": "scheduler",
        "status": "pending",
        "created_at": 1.0,
        "updated_at": 1.0,
    }

    assert repo._append_task(task_row) == task_row
    assert repo._upsert_task(task_row) == task_row
    assert repo._delete_task("task-1") is True


def test_postgres_write_repository_has_runtime_event_specific_paths() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    event_row = {
        "event_id": "evt-1",
        "timestamp": 1.0,
        "type": "inbound.received",
        "status": "ok",
        "component": "dispatcher",
        "message": "received",
    }

    assert repo._append_runtime_event(event_row) == event_row
    assert repo._upsert_runtime_event(event_row) == event_row
    assert repo._delete_runtime_event("evt-1") is True


def test_postgres_write_repository_has_memory_entry_specific_paths() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    memory_row = {
        "id": "mem-1",
        "agent_id": "main",
        "category": "general",
        "content": "remember this",
        "created_at": 1.0,
        "updated_at": 1.0,
    }

    assert repo._append_memory_entry(memory_row) == memory_row
    assert repo._upsert_memory_entry(memory_row) == memory_row
    assert repo._delete_memory_entry("mem-1") is True


def test_postgres_write_repository_has_config_audit_specific_paths() -> None:
    repo = PostgresWriteRepository(
        url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        enabled=False,
    )

    audit_row = {
        "id": "audit-1",
        "entity_type": "agent",
        "entity_id": "main",
        "action": "update",
        "created_at": 1.0,
    }

    assert repo._append_config_audit(audit_row) == audit_row
    assert repo._upsert_config_audit(audit_row) == audit_row
    assert repo._delete_config_audit("audit-1") is True
