from pathlib import Path

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.factory import build_state_repository
from agent_gateway.runtime.state.postgres import PostgresReadRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.store import LocalTaskStore
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


def test_build_state_repository_switches_to_postgres_backend(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        postgres_enabled=True,
        postgres_url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
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

    assert isinstance(bundle.read, PostgresReadRepository)


def test_build_state_repository_enables_migration_backup_sink(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
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
    assert sessions.backup_sink is bundle.backup
    assert tasks.backup_sink is bundle.backup
    assert events.backup_sink is bundle.backup
    assert memory.backup_sink is bundle.backup


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
