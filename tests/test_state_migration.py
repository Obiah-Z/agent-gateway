from pathlib import Path

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.state.factory import build_state_repository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.store import LocalTaskStore
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.metrics import MetricsStore


def test_state_migration_backup_writes_to_dedicated_journals(tmp_path: Path) -> None:
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

    build_state_repository(
        settings,
        sessions=sessions,
        tasks=tasks,
        events=events,
        metrics=MetricsStore(settings.metrics_dir),
        alerts=AlertStore(settings.alerts_dir),
        memory=memory,
    )

    sessions.append_message("main", "session-1", "user", "hello")
    sessions.rewrite_messages("main", "session-1", [{"role": "user", "content": "hello"}])
    task = tasks.create(TaskInstance.create(task_type="cron", source="scheduler"))
    events.record(
        "inbound.received",
        status="ok",
        component="dispatcher",
        message="received",
    )
    memory.write_memory("remember this", "general")

    migration_root = settings.data_dir / "migration"
    assert (migration_root / "sessions" / "agents" / "main").exists()
    assert (migration_root / "tasks" / f"{task.id}.json").exists()
    assert list((migration_root / "events").glob("runtime-events-*.jsonl"))
    assert list((migration_root / "memory" / "_migration" / "daily").glob("*.jsonl"))
