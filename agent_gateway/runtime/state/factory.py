from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.migration import CompositeMigrationSink, LocalMigrationSink, MigrationSink
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.repository import StateReadRepository
from agent_gateway.runtime.state.postgres import PostgresReadRepository, PostgresWriteRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.store import LocalTaskStore


@dataclass(slots=True)
class StateRepositoryBundle:
    """统一状态仓储装配结果。"""

    read: StateReadRepository
    write: PostgresWriteRepository | None = None
    config_write: PostgresWriteRepository | None = None
    backup: MigrationSink | None = None


def build_state_repository(
    settings: GatewaySettings,
    *,
    sessions: SessionStore,
    tasks: LocalTaskStore,
    events: RuntimeEventStore,
    metrics: MetricsStore,
    alerts: AlertStore,
    memory: MemoryStore,
) -> StateRepositoryBundle:
    """根据当前配置装配状态仓储。

    目前默认返回本地只读仓储；当 PostgreSQL 开关开启时，切换为 PostgreSQL
    只读仓储骨架，保持上层控制面和 Dashboard 不改调用方式。
    """

    migration_sessions = SessionStore(settings.data_dir / "migration" / "sessions")
    migration_tasks = LocalTaskStore(settings.data_dir / "migration" / "tasks")
    migration_events = RuntimeEventStore(settings.data_dir / "migration" / "events")
    migration_memory = MemoryStore(settings.data_dir / "migration" / "memory")
    local_backup = LocalMigrationSink(
        sessions=migration_sessions,
        tasks=migration_tasks,
        events=migration_events,
        memory=migration_memory,
    )
    write = PostgresWriteRepository(
        url=settings.postgres_url,
        enabled=settings.postgres_enabled,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    backup = local_backup if settings.postgres_enabled else CompositeMigrationSink((local_backup, write))
    sessions.backup_sink = backup
    tasks.backup_sink = backup
    events.backup_sink = backup
    memory.backup_sink = backup
    primary_write = write if settings.postgres_enabled else None
    sessions.write_backend = primary_write
    tasks.write_backend = primary_write
    events.write_backend = primary_write
    memory.write_backend = primary_write
    metrics.write_backend = primary_write
    alerts.write_backend = primary_write
    if settings.postgres_enabled:
        return StateRepositoryBundle(
            read=PostgresReadRepository(
                url=settings.postgres_url,
                enabled=True,
                connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
            ),
            write=write,
            config_write=write,
            backup=backup,
        )
    return StateRepositoryBundle(
        read=LocalStateReadRepository(
            sessions=sessions,
            tasks=tasks,
            events=events,
            metrics=metrics,
            alerts=alerts,
            memory=memory,
        ),
        write=write,
        config_write=write,
        backup=backup,
    )
