from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
from typing import Any, Protocol

from agent_gateway.runtime.state.repository import StateReadRepository


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


@dataclass(slots=True)
class PostgresReadRepository(StateReadRepository):
    """基于 `psql` 的 PostgreSQL 只读仓储。

    当前阶段先把读路径和 SQL 形态固定下来，不引入额外 Python 驱动；
    这样后续只需要把执行层替换为 psycopg 即可。
    """

    url: str
    enabled: bool = False
    connect_timeout_seconds: float = 2.0

    def list(
        self,
        table: str,
        *,
        limit: int = 50,
        cursor: str = "",
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del cursor
        filters = filters or {}
        sql, params = self._build_list_query(table, limit=limit, filters=filters)
        return self.query(table, sql=sql, params=params)

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        primary_key = self._primary_key(table)
        sql = f"SELECT row_to_json(t) AS row FROM {self._table_name(table)} t WHERE {primary_key} = %(id)s LIMIT 1"
        rows = self.query(table, sql=sql, params={"id": key})
        if not rows:
            return None
        row = rows[0]
        payload = row.get("row", row)
        return payload if isinstance(payload, dict) else None

    def append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("PostgresReadRepository is read-only")

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("PostgresReadRepository is read-only")

    def delete(self, table: str, key: str) -> bool:
        raise NotImplementedError("PostgresReadRepository is read-only")

    def query(
        self,
        table: str,
        *,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if shutil.which("psql") is None:
            raise RuntimeError("psql is not installed")
        command = [
            "psql",
            self.url,
            "-X",
            "-q",
            "-t",
            "-A",
            "-F",
            "\t",
            "-c",
            sql,
        ]
        if params:
            command.extend(["--set", f"params={json.dumps(params, ensure_ascii=False)}"])
        completed = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
            timeout=self.connect_timeout_seconds,
        )
        rows: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _build_list_query(
        self,
        table: str,
        *,
        limit: int,
        filters: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        table_name = self._table_name(table)
        safe_limit = max(1, min(int(limit), 500))
        sql = f"SELECT row_to_json(t) AS row FROM {table_name} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": safe_limit}
        if table == "sessions" and filters.get("agent_id"):
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = str(filters["agent_id"])
        if table == "tasks" and filters.get("statuses"):
            clauses.append("status = ANY(%(statuses)s)")
            params["statuses"] = list(filters["statuses"])
        if table in {"runtime_events", "errors"}:
            for key in ("event_type", "component", "status", "correlation_id", "agent_id", "channel", "job_id", "delivery_id"):
                value = str(filters.get(key, ""))
                if value:
                    column = "type" if key == "event_type" else key
                    clauses.append(f"{column} = %({key})s")
                    params[key] = value
        if table == "metrics" and filters.get("kind"):
            clauses.append("kind = %(kind)s")
            params["kind"] = str(filters["kind"])
        if table == "memory_entries" and filters.get("agent_id"):
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = str(filters["agent_id"])
        if table == "config_audits" and filters.get("entity_type"):
            clauses.append("entity_type = %(entity_type)s")
            params["entity_type"] = str(filters["entity_type"])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {self._order_column(table)} DESC LIMIT %(limit)s"
        return sql, params

    @staticmethod
    def _table_name(table: str) -> str:
        allowed = {
            "sessions",
            "tasks",
            "runtime_events",
            "errors",
            "metrics",
            "memory_entries",
            "config_audits",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        return table

    @staticmethod
    def _primary_key(table: str) -> str:
        mapping = {
            "sessions": "id",
            "tasks": "id",
            "runtime_events": "event_id",
            "errors": "id",
            "metrics": "id",
            "memory_entries": "id",
            "config_audits": "id",
        }
        if table not in mapping:
            raise ValueError(f"unsupported table: {table}")
        return mapping[table]

    @staticmethod
    def _order_column(table: str) -> str:
        mapping = {
            "sessions": "updated_at",
            "tasks": "updated_at",
            "runtime_events": "timestamp",
            "errors": "timestamp",
            "metrics": "timestamp",
            "memory_entries": "created_at",
            "config_audits": "created_at",
        }
        if table not in mapping:
            raise ValueError(f"unsupported table: {table}")
        return mapping[table]


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
