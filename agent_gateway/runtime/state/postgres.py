from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class PostgresTableSpec:
    """PostgreSQL 状态表设计草案。"""

    name: str
    primary_key: str
    time_column: str
    columns: tuple[str, ...]
    indexes: tuple[tuple[str, ...], ...] = ()
    retention_days: int = 14


class PostgresStateStore(Protocol):
    """PostgreSQL 状态仓储抽象。

    先约束通用读写边界，具体驱动和 SQL 语句后续再接。
    """

    def list(self, table: str, *, limit: int = 50, cursor: str = "", filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """按条件列出记录。"""

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        """按主键读取单条记录。"""

    def append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """追加一条记录。"""

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """写入或更新一条记录。"""

    def delete(self, table: str, key: str) -> bool:
        """删除一条记录。"""

    def query(self, table: str, *, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """执行受控查询。"""


POSTGRES_STATE_TABLES: tuple[PostgresTableSpec, ...] = (
    PostgresTableSpec(
        name="sessions",
        primary_key="id",
        time_column="updated_at",
        columns=(
            "id",
            "agent_id",
            "session_key",
            "channel",
            "account_id",
            "peer_id",
            "title",
            "summary",
            "created_at",
            "updated_at",
            "last_message_at",
            "message_count",
            "metadata",
        ),
        indexes=(("agent_id", "updated_at"), ("session_key",)),
    ),
    PostgresTableSpec(
        name="tasks",
        primary_key="id",
        time_column="updated_at",
        columns=(
            "id",
            "task_type",
            "source",
            "status",
            "agent_id",
            "session_key",
            "priority",
            "idempotency_key",
            "payload",
            "result_preview",
            "error",
            "retry_count",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "metadata",
        ),
        indexes=(("status", "updated_at"), ("agent_id", "updated_at"), ("idempotency_key",)),
    ),
    PostgresTableSpec(
        name="runtime_events",
        primary_key="event_id",
        time_column="timestamp",
        columns=(
            "event_id",
            "timestamp",
            "type",
            "status",
            "component",
            "message",
            "correlation_id",
            "agent_id",
            "session_key",
            "channel",
            "account_id",
            "peer_id",
            "delivery_id",
            "job_id",
            "error",
            "metadata",
        ),
        indexes=(("timestamp",), ("correlation_id", "timestamp"), ("component", "status", "timestamp")),
    ),
    PostgresTableSpec(
        name="errors",
        primary_key="id",
        time_column="timestamp",
        columns=(
            "id",
            "event_id",
            "timestamp",
            "component",
            "category",
            "severity",
            "message",
            "error",
            "correlation_id",
            "agent_id",
            "session_key",
            "metadata",
        ),
        indexes=(("component", "timestamp"), ("correlation_id", "timestamp")),
    ),
    PostgresTableSpec(
        name="metrics",
        primary_key="id",
        time_column="timestamp",
        columns=(
            "id",
            "timestamp",
            "kind",
            "name",
            "value",
            "labels",
            "window_seconds",
            "metadata",
        ),
        indexes=(("kind", "name", "timestamp"),),
    ),
    PostgresTableSpec(
        name="memory_entries",
        primary_key="id",
        time_column="created_at",
        columns=("id", "agent_id", "category", "content", "source_file", "created_at", "updated_at", "metadata"),
        indexes=(("agent_id", "created_at"),),
    ),
    PostgresTableSpec(
        name="config_audits",
        primary_key="id",
        time_column="created_at",
        columns=("id", "entity_type", "entity_id", "action", "before", "after", "actor", "created_at", "metadata"),
        indexes=(("entity_type", "entity_id", "created_at"),),
    ),
)
