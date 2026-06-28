from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import shutil
import subprocess
import time
from typing import Any, Protocol

from agent_gateway.runtime.state.repository import StateReadRepository
from agent_gateway.runtime.state.store import SessionStore


POSTGRES_TIME_COLUMNS = {
    "created_at",
    "collected_at",
    "enqueued_at",
    "expires_at",
    "finished_at",
    "last_good_at",
    "last_message_at",
    "next_retry_at",
    "received_at",
    "run_at",
    "seen_at",
    "started_at",
    "timestamp",
    "updated_at",
}


def _sql_literal(value: Any) -> str:
    """把 Python 值转换为可内联的 PostgreSQL SQL 字面量。"""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, tuple)):
        return "ARRAY[" + ", ".join(_sql_literal(item) for item in value) + "]"
    return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def _inline_sql_params(sql: str, params: dict[str, Any] | None) -> str:
    """把 `%(name)s` 形式参数展开为 PostgreSQL 字面量。"""

    rendered = sql
    for key, value in (params or {}).items():
        rendered = rendered.replace(f"%({key})s", _sql_literal(value))
    return rendered


def _json_sql_literal(value: Any) -> str:
    """把 Python 值转换为可作为 JSON 参数使用的 SQL 字面量。"""

    return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::json"


def _format_epoch_seconds(value: Any) -> str | None:
    """把 PostgreSQL 中的 epoch 秒格式化成中文展示时间。"""

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime(
        "%Y年%m月%d日 %H时%M分"
    )


def _with_formatted_time_fields(row: dict[str, Any]) -> dict[str, Any]:
    """给数据库读出的时间字段补充 `*_time` 展示字段。"""

    enriched = dict(row)
    for key, value in list(row.items()):
        if isinstance(value, dict):
            enriched[key] = _with_formatted_time_fields(value)
            continue
        if key not in POSTGRES_TIME_COLUMNS:
            continue
        formatted = _format_epoch_seconds(value)
        if formatted:
            enriched[f"{key}_time"] = formatted
    return enriched


@dataclass(slots=True)
class PostgresTableSpec:
    """PostgreSQL 状态表设计草案。"""

    name: str
    primary_key: str
    time_column: str
    columns: tuple[str, ...]
    indexes: tuple[tuple[str, ...], ...] = ()
    retention_days: int = 14


@dataclass(slots=True)
class PostgresSchemaCheckResult:
    """PostgreSQL 实库 schema 与当前代码规格的对比结果。"""

    ok: bool
    missing_tables: list[str]
    missing_columns: dict[str, list[str]]
    type_mismatches: dict[str, dict[str, dict[str, str]]]

    def to_dict(self) -> dict[str, Any]:
        """转换为 CLI 友好的字典结构。"""

        return {
            "ok": self.ok,
            "missing_tables": self.missing_tables,
            "missing_columns": self.missing_columns,
            "type_mismatches": self.type_mismatches,
        }


def build_postgres_schema_sql(
    tables: tuple[PostgresTableSpec, ...] = (),
) -> str:
    """根据表规格生成 PostgreSQL 初始化 SQL。

    该 SQL 同时承担两类职责：

    - 全新数据库：创建当前版本声明的状态表与索引。
    - 旧数据库：执行少量幂等迁移，补齐已上线表缺失的新列。

    旧库迁移保持保守，只补明确需要向后兼容的字段，避免对未知历史表结构做
    大范围自动 ALTER。
    """

    specs = tables or POSTGRES_STATE_TABLES
    table_statements: list[str] = []
    index_statements: list[str] = []
    for spec in specs:
        column_defs = [
            f"{_quote_ident(column)} {_column_type(spec.name, column)}"
            for column in spec.columns
        ]
        column_defs.append(f"PRIMARY KEY ({_quote_ident(spec.primary_key)})")
        table_statements.append(
            "CREATE TABLE IF NOT EXISTS "
            f"{_quote_ident(spec.name)} (\n  "
            + ",\n  ".join(column_defs)
            + "\n);"
        )
        for index_columns in spec.indexes:
            index_name = _index_name(spec.name, index_columns)
            columns_sql = ", ".join(_quote_ident(column) for column in index_columns)
            index_statements.append(
                "CREATE INDEX IF NOT EXISTS "
                f"{_quote_ident(index_name)} ON {_quote_ident(spec.name)} ({columns_sql});"
            )
    statements = [
        *table_statements,
        *_build_postgres_schema_migration_statements(specs),
        *index_statements,
    ]
    return "\n\n".join(statements) + "\n"


def _build_postgres_schema_migration_statements(
    specs: tuple[PostgresTableSpec, ...],
) -> list[str]:
    """生成旧库兼容迁移 SQL。

    `CREATE TABLE IF NOT EXISTS` 不会给已有表补列；这里专门收口那些已经进入
    运行路径、旧库缺失会导致启动或消费失败的字段。
    """

    spec_names = {spec.name for spec in specs}
    statements: list[str] = []
    if "delivery_entries" in spec_names:
        statements.extend(
            [
                (
                    'ALTER TABLE "delivery_entries" '
                    'ADD COLUMN IF NOT EXISTS "locked_by" TEXT NOT NULL DEFAULT \'\';'
                ),
                (
                    'ALTER TABLE "delivery_entries" '
                    'ADD COLUMN IF NOT EXISTS "locked_at" DOUBLE PRECISION NOT NULL DEFAULT 0;'
                ),
            ]
        )
    return statements


def initialize_postgres_schema(
    *,
    url: str,
    connect_timeout_seconds: float = 2.0,
    tables: tuple[PostgresTableSpec, ...] = (),
) -> str:
    """执行 PostgreSQL schema 初始化，并返回实际执行的 SQL。"""

    sql = build_postgres_schema_sql(tables)
    _run_psql_sql(url=url, sql=sql, connect_timeout_seconds=connect_timeout_seconds)
    return sql


def check_postgres_schema(
    *,
    url: str,
    connect_timeout_seconds: float = 2.0,
    tables: tuple[PostgresTableSpec, ...] = (),
) -> PostgresSchemaCheckResult:
    """检查实库表结构是否与当前代码声明的状态表规格一致。"""

    specs = tables or POSTGRES_STATE_TABLES
    if shutil.which("psql") is None:
        raise RuntimeError("psql is not installed")
    sql = (
        "SELECT json_build_object("
        "'table', table_name, "
        "'column', column_name, "
        "'type', data_type"
        ") AS row "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position;"
    )
    completed = subprocess.run(
        ["psql", url, "-X", "-q", "-t", "-A", "-F", "\t", "-c", sql],
        check=True,
        text=True,
        capture_output=True,
        timeout=connect_timeout_seconds,
    )
    actual: dict[str, dict[str, str]] = {}
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        table = str(row.get("table", ""))
        column = str(row.get("column", ""))
        data_type = str(row.get("type", ""))
        if table and column:
            actual.setdefault(table, {})[column] = data_type

    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    type_mismatches: dict[str, dict[str, dict[str, str]]] = {}
    for spec in specs:
        actual_columns = actual.get(spec.name)
        if actual_columns is None:
            missing_tables.append(spec.name)
            continue
        for column in spec.columns:
            actual_type = actual_columns.get(column)
            if actual_type is None:
                missing_columns.setdefault(spec.name, []).append(column)
                continue
            expected_type = _expected_information_schema_type(spec.name, column)
            if actual_type != expected_type:
                type_mismatches.setdefault(spec.name, {})[column] = {
                    "expected": expected_type,
                    "actual": actual_type,
                }

    return PostgresSchemaCheckResult(
        ok=not missing_tables and not missing_columns and not type_mismatches,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        type_mismatches=type_mismatches,
    )


def _run_psql_sql(*, url: str, sql: str, connect_timeout_seconds: float) -> None:
    """通过本机 `psql` 执行一段 SQL。"""

    if shutil.which("psql") is None:
        raise RuntimeError("psql is not installed")
    subprocess.run(
        ["psql", url, "-X", "-v", "ON_ERROR_STOP=1", "-q", "-c", sql],
        check=True,
        text=True,
        capture_output=True,
        timeout=connect_timeout_seconds,
    )


def _quote_ident(name: str) -> str:
    """安全引用固定 schema 中的标识符。"""

    return '"' + name.replace('"', '""') + '"'


def _index_name(table: str, columns: tuple[str, ...]) -> str:
    return "idx_" + table + "_" + "_".join(columns)


def _column_type(table: str, column: str) -> str:
    json_columns = {
        "before",
        "after",
        "config",
        "labels",
        "memory_policy",
        "metadata",
        "payload",
        "prompt_policy",
        "actions",
        "blocks",
        "structured_blocks",
        "tags",
        "tool_policy",
    }
    float_columns = {
        "created_at",
        "collected_at",
        "enqueued_at",
        "finished_at",
        "last_good_at",
        "last_message_at",
        "locked_at",
        "next_retry_at",
        "received_at",
        "run_at",
        "seen_at",
        "started_at",
        "timestamp",
        "updated_at",
        "value",
        "expires_at",
    }
    int_columns = {
        "message_count",
        "offset_value",
        "priority",
        "page_index",
        "page_size",
        "retry_count",
        "tier",
        "window_seconds",
    }
    bool_columns = {"bound_is_group", "enabled", "expanded"}
    if column in json_columns:
        return "JSONB NOT NULL DEFAULT '{}'::jsonb"
    if column in float_columns:
        return "DOUBLE PRECISION NOT NULL DEFAULT 0"
    if column in int_columns:
        return "INTEGER NOT NULL DEFAULT 0"
    if column in bool_columns:
        return "BOOLEAN NOT NULL DEFAULT TRUE"
    if table == "sessions" and column == "id":
        return "TEXT NOT NULL"
    return "TEXT NOT NULL DEFAULT ''"


def _expected_information_schema_type(table: str, column: str) -> str:
    """返回 `_column_type()` 在 information_schema 中对应的标准类型名。"""

    column_type = _column_type(table, column)
    if column_type.startswith("JSONB"):
        return "jsonb"
    if column_type.startswith("DOUBLE PRECISION"):
        return "double precision"
    if column_type.startswith("INTEGER"):
        return "integer"
    if column_type.startswith("BOOLEAN"):
        return "boolean"
    return "text"


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
        if table == "sessions":
            if filters.get("full") or filters.get("session_key"):
                return self._list_session_rows(limit=limit, filters=filters)
            return self._list_sessions(limit=limit, filters=filters)
        if table == "errors":
            return self._list_errors(limit=limit, filters=filters)
        if table == "memory_entries":
            return self._list_memory_entries(limit=limit, filters=filters)
        if table == "tasks":
            return self._list_tasks(limit=limit, filters=filters)
        if table == "agents":
            return self._list_agents(limit=limit, filters=filters)
        if table == "bindings":
            return self._list_bindings(limit=limit, filters=filters)
        if table == "profiles":
            return self._list_profiles(limit=limit, filters=filters)
        if table == "channels":
            return self._list_channels(limit=limit, filters=filters)
        if table == "delivery_entries":
            return self._list_delivery_entries(limit=limit, filters=filters)
        if table in {"feishu_dedup_entries", "feishu_webhook_events"}:
            return self._list_generic_rows(table, limit=limit, filters=filters)
        if table == "feishu_onboarding_sessions":
            return self._list_generic_rows(table, limit=limit, filters=filters)
        if table == "channel_offsets":
            return self._list_generic_rows(table, limit=limit, filters=filters)
        if table == "cron_runs":
            return self._list_generic_rows(table, limit=limit, filters=filters)
        if table == "news_items":
            return self._list_generic_rows(table, limit=limit, filters=filters)
        if table == "feishu_card_states":
            return self._list_generic_rows(table, limit=limit, filters=filters)
        sql, params = self._build_list_query(table, limit=limit, filters=filters)
        return self.query(table, sql=sql, params=params)

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        primary_key = self._primary_key(table)
        sql = (
            f"SELECT row_to_json(t) AS row FROM {self._table_name(table)} t "
            f"WHERE {_quote_ident(primary_key)} = %(id)s LIMIT 1"
        )
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
        sql = _inline_sql_params(sql, params)
        command = [
            "psql",
            self.url,
            "-X",
            "-q",
            "-t",
            "-A",
            "-F",
            "\t",
            "-f",
            "-",
        ]
        completed = subprocess.run(
            command,
            input=sql,
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
                rows.append(_with_formatted_time_fields(payload))
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
        if table == "agents" and filters.get("id"):
            clauses.append("id = %(id)s")
            params["id"] = str(filters["id"])
        if table == "bindings":
            for key in ("key", "agent_id", "match_key", "match_value"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{_quote_ident(key)} = %({key})s")
                    params[key] = value
        if table == "profiles":
            for key in ("name", "provider"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{_quote_ident(key)} = %({key})s")
                    params[key] = value
        if table == "channels":
            for key in ("key", "channel", "account_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{_quote_ident(key)} = %({key})s")
                    params[key] = value
        if table == "tasks" and filters.get("statuses"):
            clauses.append("status = ANY(%(statuses)s)")
            params["statuses"] = list(filters["statuses"])
        if table == "runtime_events":
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
        if table == "delivery_entries" and filters.get("state"):
            clauses.append("state = %(state)s")
            params["state"] = str(filters["state"])
        if table == "feishu_dedup_entries":
            event_id = str(filters.get("event_id", ""))
            if event_id:
                clauses.append("event_id = %(event_id)s")
                params["event_id"] = event_id
            if filters.get("expires_after") is not None:
                clauses.append("expires_at > %(expires_after)s")
                params["expires_after"] = float(filters["expires_after"])
        if table == "feishu_webhook_events":
            for key in ("outcome", "channel_account", "event_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if table == "feishu_onboarding_sessions":
            for key in ("status", "binding_code", "account_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if table == "channel_offsets":
            for key in ("channel", "account_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if table == "cron_runs":
            for key in ("job_id", "config_id", "agent_id", "status"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if table == "news_items":
            for key in ("store_name", "state", "item_id", "source_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if table == "feishu_card_states":
            for key in ("card_id", "owner_account_id", "peer_id", "message_id"):
                value = str(filters.get(key, ""))
                if value:
                    clauses.append(f"{key} = %({key})s")
                    params[key] = value
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {_quote_ident(self._order_column(table))} DESC LIMIT %(limit)s"
        return sql, params

    def _list_sessions(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回与本地 SessionStore 一致的会话摘要。"""

        agent_id = str(filters.get("agent_id", ""))
        table_name = self._table_name("sessions")
        sql = (
            "SELECT json_build_object("
            "'session_key', session_key, "
            "'message_count', COUNT(*)"
            ") AS row "
            f"FROM {table_name}"
        )
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        if agent_id:
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = agent_id
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " GROUP BY session_key ORDER BY MAX(updated_at) DESC LIMIT %(limit)s"
        rows = self.query("sessions", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if not isinstance(payload, dict):
                continue
            session_key = str(payload.get("session_key", ""))
            if not session_key:
                continue
            result.append(
                {
                    "session_key": session_key,
                    "message_count": int(payload.get("message_count", 0) or 0),
                }
            )
        return result[: max(1, min(int(limit), 500))]

    def _list_session_rows(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回会话消息的完整行，供消息历史重建使用。"""

        table_name = self._table_name("sessions")
        safe_limit = max(1, min(int(limit), 2000))
        sql = f"SELECT row_to_json(t) AS row FROM {table_name} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": safe_limit}
        agent_id = str(filters.get("agent_id", ""))
        session_key = str(filters.get("session_key", ""))
        if agent_id:
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = agent_id
        if session_key:
            clauses.append("session_key = %(session_key)s")
            params["session_key"] = session_key
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at ASC LIMIT %(limit)s"
        rows = self.query("sessions", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result[:safe_limit]

    def read_session_messages(self, agent_id: str, session_key: str) -> list[dict[str, Any]]:
        """读取单个会话的完整消息历史。"""

        rows = self._list_session_rows(limit=2000, filters={"agent_id": agent_id, "session_key": session_key, "full": True})
        messages: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            kind = str(metadata.get("kind", ""))
            if kind == "snapshot":
                snapshot = metadata.get("messages", [])
                if isinstance(snapshot, list):
                    messages = [item for item in snapshot if isinstance(item, dict)]
                continue
            role = str(metadata.get("role", ""))
            content = metadata.get("content")
            if role and content is not None:
                messages.append({"role": role, "content": content})
        return SessionStore.sanitize_messages(messages)

    def _list_errors(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回与 RuntimeEventStore.recent_errors 一致的事件形态。"""

        table_name = self._table_name("runtime_events")
        safe_limit = max(1, min(int(limit), 200))
        sql = (
            "SELECT row_to_json(t) AS row "
            f"FROM {table_name} t WHERE status = ANY(%(statuses)s)"
        )
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": safe_limit,
            "statuses": ["error", "failed", "rejected", "critical"],
        }
        for key in ("component", "correlation_id"):
            value = str(filters.get(key, ""))
            if value:
                clauses.append(f"{key} = %({key})s")
                params[key] = value
        if clauses:
            sql += " AND " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT %(limit)s"
        rows = self.query("runtime_events", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result[:safe_limit]

    def _list_memory_entries(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回与 MemoryStore.recent_entries 一致的摘要形态。"""

        table_name = self._table_name("memory_entries")
        safe_limit = max(1, min(int(limit), 200))
        sql = f"SELECT row_to_json(t) AS row FROM {table_name} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": safe_limit}
        agent_id = str(filters.get("agent_id", ""))
        if agent_id:
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = agent_id
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT %(limit)s"
        rows = self.query("memory_entries", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if not isinstance(payload, dict):
                continue
            result.append(
                {
                    "ts": str(payload.get("created_at", "")),
                    "ts_time": str(payload.get("created_at_time", "")),
                    "category": str(payload.get("category", "")),
                    "content": str(payload.get("content", "")),
                    "file": str(payload.get("source_file", "")),
                }
            )
        return result[:safe_limit]

    def _list_tasks(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回与 LocalTaskStore.list 一致的任务结构。"""

        table_name = self._table_name("tasks")
        safe_limit = max(1, min(int(limit), 200))
        sql = f"SELECT row_to_json(t) AS row FROM {table_name} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": safe_limit}
        statuses = filters.get("statuses")
        if statuses:
            clauses.append("status = ANY(%(statuses)s)")
            params["statuses"] = list(statuses)
        agent_id = str(filters.get("agent_id", ""))
        if agent_id:
            clauses.append("agent_id = %(agent_id)s")
            params["agent_id"] = agent_id
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT %(limit)s"
        rows = self.query("tasks", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result[:safe_limit]

    def _list_agents(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        sql = f"SELECT row_to_json(t) AS row FROM {self._table_name('agents')} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        agent_id = str(filters.get("id", ""))
        if agent_id:
            clauses.append("id = %(id)s")
            params["id"] = agent_id
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT %(limit)s"
        rows = self.query("agents", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _list_bindings(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        sql = f"SELECT row_to_json(t) AS row FROM {self._table_name('bindings')} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        for key in ("key", "agent_id", "match_key", "match_value"):
            value = str(filters.get(key, ""))
            if value:
                clauses.append(f"{_quote_ident(key)} = %({key})s")
                params[key] = value
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY agent_id ASC, tier ASC, priority DESC LIMIT %(limit)s"
        rows = self.query("bindings", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _list_profiles(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        sql = f"SELECT row_to_json(t) AS row FROM {self._table_name('profiles')} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        for key in ("name", "provider"):
            value = str(filters.get(key, ""))
            if value:
                clauses.append(f"{_quote_ident(key)} = %({key})s")
                params[key] = value
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY name ASC LIMIT %(limit)s"
        rows = self.query("profiles", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _list_channels(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        sql = f"SELECT row_to_json(t) AS row FROM {self._table_name('channels')} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        for key in ("key", "channel", "account_id"):
            value = str(filters.get(key, ""))
            if value:
                clauses.append(f"{_quote_ident(key)} = %({key})s")
                params[key] = value
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY channel ASC, account_id ASC LIMIT %(limit)s"
        rows = self.query("channels", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _list_delivery_entries(self, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """返回可靠投递队列记录，供 DeliveryQueue 重建 pending/failed。"""

        table_name = self._table_name("delivery_entries")
        safe_limit = max(1, min(int(limit), 2000))
        sql = f"SELECT row_to_json(t) AS row FROM {table_name} t"
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": safe_limit}
        state = str(filters.get("state", ""))
        if state:
            clauses.append("state = %(state)s")
            params["state"] = state
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY enqueued_at ASC LIMIT %(limit)s"
        rows = self.query("delivery_entries", sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result[:safe_limit]

    def _list_generic_rows(self, table: str, *, limit: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """按通用 list 查询返回 PostgreSQL 行。"""

        safe_limit = max(1, min(int(limit), 500))
        sql, params = self._build_list_query(table, limit=safe_limit, filters=filters)
        rows = self.query(table, sql=sql, params=params)
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("row", row)
            if isinstance(payload, dict):
                result.append(payload)
        return result[:safe_limit]

    @staticmethod
    def _table_name(table: str) -> str:
        allowed = {
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
            "feishu_dedup_entries",
            "feishu_webhook_events",
            "feishu_onboarding_sessions",
            "channel_offsets",
            "cron_runs",
            "news_items",
            "feishu_card_states",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        return table

    @staticmethod
    def _primary_key(table: str) -> str:
        mapping = {
            "agents": "id",
            "bindings": "key",
            "profiles": "name",
            "channels": "key",
            "delivery_entries": "id",
            "sessions": "id",
            "tasks": "id",
            "runtime_events": "event_id",
            "errors": "id",
            "metrics": "id",
            "memory_entries": "id",
            "config_audits": "id",
            "feishu_dedup_entries": "event_id",
            "feishu_webhook_events": "id",
            "feishu_onboarding_sessions": "session_id",
            "channel_offsets": "key",
            "cron_runs": "id",
            "news_items": "key",
            "feishu_card_states": "card_id",
        }
        if table not in mapping:
            raise ValueError(f"unsupported table: {table}")
        return mapping[table]

    @staticmethod
    def _order_column(table: str) -> str:
        mapping = {
            "agents": "updated_at",
            "bindings": "updated_at",
            "profiles": "updated_at",
            "channels": "updated_at",
            "sessions": "updated_at",
            "delivery_entries": "enqueued_at",
            "tasks": "updated_at",
            "runtime_events": "timestamp",
            "errors": "timestamp",
            "metrics": "timestamp",
            "memory_entries": "created_at",
            "config_audits": "created_at",
            "feishu_dedup_entries": "seen_at",
            "feishu_webhook_events": "received_at",
            "feishu_onboarding_sessions": "updated_at",
            "channel_offsets": "updated_at",
            "cron_runs": "run_at",
            "news_items": "updated_at",
            "feishu_card_states": "updated_at",
        }
        if table not in mapping:
            raise ValueError(f"unsupported table: {table}")
        return mapping[table]


@dataclass(slots=True)
class PostgresWriteRepository:
    """PostgreSQL 状态写入骨架。

    当前阶段只固定写入入口和表形态，不直接连接数据库驱动；后续可替换为 psycopg
    或 SQLAlchemy 实现。
    """

    url: str
    enabled: bool = False
    connect_timeout_seconds: float = 2.0

    def append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table == "agents":
            return self._append_agents_row(row)
        if table == "bindings":
            return self._append_bindings_row(row)
        if table == "profiles":
            return self._append_profiles_row(row)
        if table == "channels":
            return self._append_channels_row(row)
        if table == "delivery_entries":
            return self._append_delivery_entry(row)
        if table == "sessions":
            return self._append_session(row)
        if table == "tasks":
            return self._append_task(row)
        if table == "runtime_events":
            return self._append_runtime_event(row)
        if table == "memory_entries":
            return self._append_memory_entry(row)
        if table == "feishu_dedup_entries":
            return self._append_feishu_dedup_entry(row)
        if table == "feishu_webhook_events":
            return self._append_feishu_webhook_event(row)
        if table == "feishu_onboarding_sessions":
            return self._append_feishu_onboarding_session(row)
        if table == "channel_offsets":
            return self._append_channel_offset(row)
        if table == "cron_runs":
            return self._append_cron_run(row)
        if table == "news_items":
            return self._append_news_item(row)
        if table == "feishu_card_states":
            return self._append_feishu_card_state(row)
        payload = dict(row)
        primary_key = self._primary_key(table)
        if primary_key not in payload:
            raise ValueError(f"missing primary key for {table}: {primary_key}")
        sql = self._build_insert_sql(table)
        rows = self.query(table, sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def bulk_upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        batch_size: int = 500,
    ) -> int:
        """批量 upsert 多条记录，主要用于本地历史数据回填。"""

        if not rows:
            return 0
        if not self.enabled:
            return 0
        total = 0
        safe_batch_size = max(1, min(int(batch_size), 1000))
        primary_key = self._primary_key(table)
        for start in range(0, len(rows), safe_batch_size):
            batch = [dict(row) for row in rows[start : start + safe_batch_size]]
            for row in batch:
                if primary_key not in row:
                    raise ValueError(f"missing primary key for {table}: {primary_key}")
            sql = self._build_bulk_upsert_sql(table, batch)
            self.query(table, sql=sql)
            total += len(batch)
        return total

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table == "agents":
            return self._upsert_agents_row(row)
        if table == "bindings":
            return self._upsert_bindings_row(row)
        if table == "profiles":
            return self._upsert_profiles_row(row)
        if table == "channels":
            return self._upsert_channels_row(row)
        if table == "delivery_entries":
            return self._upsert_delivery_entry(row)
        if table == "sessions":
            return self._upsert_session(row)
        if table == "tasks":
            return self._upsert_task(row)
        if table == "runtime_events":
            return self._upsert_runtime_event(row)
        if table == "memory_entries":
            return self._upsert_memory_entry(row)
        if table == "feishu_dedup_entries":
            return self._upsert_feishu_dedup_entry(row)
        if table == "feishu_webhook_events":
            return self._upsert_feishu_webhook_event(row)
        if table == "feishu_onboarding_sessions":
            return self._upsert_feishu_onboarding_session(row)
        if table == "channel_offsets":
            return self._upsert_channel_offset(row)
        if table == "cron_runs":
            return self._upsert_cron_run(row)
        if table == "news_items":
            return self._upsert_news_item(row)
        if table == "feishu_card_states":
            return self._upsert_feishu_card_state(row)
        payload = dict(row)
        primary_key = self._primary_key(table)
        if primary_key not in payload:
            raise ValueError(f"missing primary key for {table}: {primary_key}")
        sql = self._build_upsert_sql(table)
        rows = self.query(table, sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def delete(self, table: str, key: str) -> bool:
        if table == "agents":
            return self._delete_agents_row(key)
        if table == "bindings":
            return self._delete_bindings_row(key)
        if table == "profiles":
            return self._delete_profiles_row(key)
        if table == "channels":
            return self._delete_channels_row(key)
        if table == "delivery_entries":
            return self._delete_delivery_entry(key)
        if table == "sessions":
            return self._delete_session(key)
        if table == "tasks":
            return self._delete_task(key)
        if table == "runtime_events":
            return self._delete_runtime_event(key)
        if table == "memory_entries":
            return self._delete_memory_entry(key)
        if table == "feishu_dedup_entries":
            return self._delete_feishu_dedup_entry(key)
        if table == "feishu_webhook_events":
            return self._delete_feishu_webhook_event(key)
        if table == "feishu_onboarding_sessions":
            return self._delete_feishu_onboarding_session(key)
        if table == "channel_offsets":
            return self._delete_channel_offset(key)
        if table == "cron_runs":
            return self._delete_cron_run(key)
        if table == "news_items":
            return self._delete_news_item(key)
        if table == "feishu_card_states":
            return self._delete_feishu_card_state(key)
        sql = f"DELETE FROM {self._table_name(table)} WHERE {self._primary_key(table)} = %(id)s"
        self.query(table, sql=sql, params={"id": key})
        return True

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> dict[str, Any]:
        """兼容本地 SessionStore 的单条消息镜像入口。"""

        messages = SessionStore.sanitize_messages([{"role": role, "content": content}])
        if not messages:
            return {}
        message = messages[0]
        payload = {
            "id": f"{agent_id}:{session_key}:{int(time.time() * 1000)}:{message['role']}",
            "agent_id": agent_id,
            "session_key": session_key,
            "channel": "",
            "account_id": "",
            "peer_id": "",
            "title": "",
            "summary": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "last_message_at": time.time(),
            "message_count": 1,
            "metadata": {
                "kind": "message",
                "role": message["role"],
                "content": message["content"],
            },
        }
        return self.append("sessions", payload)

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> dict[str, Any]:
        """兼容本地 SessionStore 的整段重写镜像入口。"""

        messages = SessionStore.sanitize_messages(messages)
        payload = {
            "id": f"{agent_id}:{session_key}:snapshot",
            "agent_id": agent_id,
            "session_key": session_key,
            "channel": "",
            "account_id": "",
            "peer_id": "",
            "title": "",
            "summary": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "last_message_at": time.time(),
            "message_count": len(messages),
            "metadata": {
                "kind": "snapshot",
                "messages": messages,
            },
        }
        return self.upsert("sessions", payload)

    def write_task(self, task: Any) -> dict[str, Any]:
        """兼容本地 TaskStore 的镜像入口。"""

        payload = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        return self.upsert("tasks", payload)

    def reserve_task(
        self,
        *,
        worker_id: str,
        task_types: list[str] | tuple[str, ...] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """原子预占一条 pending/retrying 任务。

        多 worker 共享 PostgreSQL 时，普通 list 后再 mark_running 会存在重复抢占窗口；
        这里用单条 UPDATE + FOR UPDATE SKIP LOCKED 完成选择和状态更新。
        """

        current = time.time() if now is None else float(now)
        clauses = ["status = ANY(%(statuses)s)"]
        params: dict[str, Any] = {
            "statuses": ["pending", "retrying"],
            "worker_metadata": {"worker_id": worker_id},
            "now": current,
        }
        normalized_types = [str(item) for item in (task_types or []) if str(item)]
        if normalized_types:
            clauses.append("task_type = ANY(%(task_types)s)")
            params["task_types"] = normalized_types
        sql = (
            "WITH candidate AS ("
            "SELECT id FROM tasks "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY priority ASC, created_at ASC "
            "FOR UPDATE SKIP LOCKED LIMIT 1"
            "), updated AS ("
            "UPDATE tasks SET "
            "status = 'running', "
            "started_at = %(now)s, "
            "updated_at = %(now)s, "
            "metadata = metadata || %(worker_metadata)s::jsonb "
            "FROM candidate WHERE tasks.id = candidate.id "
            "RETURNING tasks.*"
            ") SELECT row_to_json(updated) AS row FROM updated"
        )
        rows = self.query("tasks", sql=sql, params=params)
        if not rows:
            return None
        row = rows[0]
        payload = row.get("row", row)
        return payload if isinstance(payload, dict) else None

    def write_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """兼容本地 RuntimeEventStore 的镜像入口。"""

        payload = dict(event)
        return self.upsert("runtime_events", payload)

    def write_memory(self, content: str, category: str = "general") -> dict[str, Any]:
        """兼容本地 MemoryStore 的镜像入口。"""

        now = time.time()
        payload = {
            "id": f"mem_{int(now * 1000)}",
            "agent_id": "",
            "category": category,
            "content": content,
            "source_file": "",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
        }
        return self.upsert("memory_entries", payload)

    def write_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容本地 MetricsStore 的镜像入口。"""

        timestamp = float(row.get("timestamp", time.time()) or time.time())
        payload = {
            "id": str(row.get("id") or f"metric_{int(timestamp * 1000)}"),
            "timestamp": timestamp,
            "kind": str(row.get("kind", "snapshot")),
            "name": str(row.get("name", "runtime")),
            "value": float(row.get("value", 0.0) or 0.0),
            "labels": dict(row.get("labels", {}) or {}),
            "window_seconds": int(row.get("window_seconds", 0) or 0),
            "metadata": dict(row.get("metadata", row) or {}),
        }
        return self.upsert("metrics", payload)

    def write_alert(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容本地 AlertStore 的镜像入口。"""

        timestamp = float(row.get("timestamp", time.time()) or time.time())
        rule = row.get("rule", {}) if isinstance(row.get("rule"), dict) else {}
        payload = {
            "id": str(row.get("id") or f"alert_{int(timestamp * 1000)}"),
            "event_id": str(row.get("event_id", "")),
            "timestamp": timestamp,
            "component": str(row.get("component", "alerts")),
            "category": str(row.get("category") or row.get("event", "alert")),
            "severity": str(row.get("severity") or rule.get("severity", "warning")),
            "message": str(row.get("message", "")),
            "error": str(row.get("error", "")),
            "correlation_id": str(row.get("correlation_id", "")),
            "agent_id": str(row.get("agent_id", "")),
            "session_key": str(row.get("session_key", "")),
            "metadata": dict(row.get("metadata", row) or {}),
        }
        if not payload["event_id"]:
            payload["event_id"] = payload["id"]
        return self.upsert("errors", payload)

    def write_delivery_entry(self, entry: Any, *, state: str = "pending") -> dict[str, Any]:
        """兼容 DeliveryQueue 的可靠投递状态写入。"""

        payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        payload = self._normalize_delivery_entry(payload, state=state)
        return self.upsert("delivery_entries", payload)

    def delete_delivery_entry(self, delivery_id: str) -> bool:
        """删除一条可靠投递状态。"""

        return self.delete("delivery_entries", delivery_id)

    def reserve_delivery(
        self,
        *,
        worker_id: str,
        now: float | None = None,
        delivery_id: str = "",
    ) -> dict[str, Any] | None:
        """原子预占一条可发送的可靠投递记录。

        多 delivery worker 共享 PostgreSQL 时，普通 list 后再发送会重复抢占；
        这里使用 `FOR UPDATE SKIP LOCKED` 把选择和 running 标记放在同一事务语义内。
        """

        current = time.time() if now is None else float(now)
        clauses = [
            "state = ANY(%(states)s)",
            "(next_retry_at IS NULL OR next_retry_at <= %(now)s)",
        ]
        params: dict[str, Any] = {
            "states": ["pending", "retrying"],
            "worker_id": worker_id,
            "now": current,
        }
        if delivery_id:
            clauses.append("id = %(delivery_id)s")
            params["delivery_id"] = delivery_id
        sql = (
            "WITH candidate AS ("
            "SELECT id FROM delivery_entries "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY enqueued_at ASC "
            "FOR UPDATE SKIP LOCKED LIMIT 1"
            "), updated AS ("
            "UPDATE delivery_entries SET "
            "state = 'running', "
            "locked_by = %(worker_id)s, "
            "locked_at = %(now)s, "
            "updated_at = %(now)s "
            "FROM candidate WHERE delivery_entries.id = candidate.id "
            "RETURNING delivery_entries.*"
            ") SELECT row_to_json(updated) AS row FROM updated"
        )
        rows = self.query(
            "delivery_entries",
            sql=sql,
            params=params,
        )
        if not rows:
            return None
        row = rows[0]
        payload = row.get("row", row)
        return payload if isinstance(payload, dict) else None

    def mark_feishu_event_if_new(
        self,
        event_id: str,
        *,
        seen_at: float,
        expires_at: float,
    ) -> bool:
        """原子写入飞书事件去重键，首次写入返回 True。"""

        if not event_id:
            return True
        payload = {
            "event_id": event_id,
            "seen_at": seen_at,
            "expires_at": expires_at,
            "metadata": {},
        }
        sql = (
            "WITH inserted AS ("
            "INSERT INTO feishu_dedup_entries (event_id, seen_at, expires_at, metadata) "
            "SELECT event_id, seen_at, expires_at, metadata "
            "FROM json_populate_record(NULL::feishu_dedup_entries, %(row)s::json) "
            "ON CONFLICT (event_id) DO NOTHING "
            "RETURNING event_id"
            ") SELECT json_build_object('inserted', COUNT(*) > 0) AS row FROM inserted"
        )
        rows = self.query("feishu_dedup_entries", sql=sql, params={"row": payload})
        if not rows:
            return False
        row = rows[0].get("row", rows[0])
        return bool(row.get("inserted")) if isinstance(row, dict) else False

    def write_feishu_webhook_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """写入飞书 Webhook 审计事件。"""

        return self.append("feishu_webhook_events", row)

    def write_feishu_onboarding_session(self, row: dict[str, Any]) -> dict[str, Any]:
        """写入或更新飞书 onboarding 会话。"""

        return self.upsert("feishu_onboarding_sessions", row)

    def read_channel_offset(self, channel: str, account_id: str) -> int | None:
        """读取指定通道账号的消费 offset。"""

        key = f"{channel}\x1f{account_id}"
        row = PostgresReadRepository(
            url=self.url,
            enabled=self.enabled,
            connect_timeout_seconds=self.connect_timeout_seconds,
        ).get("channel_offsets", key)
        if not row:
            return None
        try:
            return int(row.get("offset_value", 0) or 0)
        except (TypeError, ValueError):
            return None

    def write_channel_offset(self, channel: str, account_id: str, offset: int) -> dict[str, Any]:
        """写入指定通道账号的消费 offset。"""

        now = time.time()
        return self.upsert(
            "channel_offsets",
            {
                "key": f"{channel}\x1f{account_id}",
                "channel": channel,
                "account_id": account_id,
                "offset_value": int(offset),
                "updated_at": now,
                "metadata": {},
            },
        )

    def write_cron_run(self, row: dict[str, Any]) -> dict[str, Any]:
        """写入 Cron 运行记录。"""

        return self.upsert("cron_runs", row)

    def list_news_items(self, store_name: str, *, state: str, limit: int = 5000) -> list[dict[str, Any]]:
        """读取指定简报仓库的新闻状态。"""

        return PostgresReadRepository(
            url=self.url,
            enabled=self.enabled,
            connect_timeout_seconds=self.connect_timeout_seconds,
        ).list(
            "news_items",
            limit=limit,
            filters={"store_name": store_name, "state": state},
        )

    def write_news_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """写入新闻简报状态。"""

        return self.upsert("news_items", row)

    def write_feishu_card_state(self, row: dict[str, Any]) -> dict[str, Any]:
        """写入或更新飞书有状态卡片状态。"""

        return self.upsert("feishu_card_states", row)

    def write_agent(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容控制面 Agent 配置写入。"""

        payload = dict(row)
        payload.setdefault("id", "")
        if not payload["id"]:
            raise ValueError("missing primary key for agents: id")
        return self.append("agents", payload)

    def write_binding(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容控制面 binding 配置写入。"""

        payload = dict(row)
        payload.setdefault("agent_id", "")
        payload.setdefault("match_key", "")
        payload.setdefault("match_value", "")
        if not payload["agent_id"] or not payload["match_key"]:
            raise ValueError("missing primary key for bindings")
        return self.append("bindings", payload)

    def write_profile(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容控制面 profile 配置写入。"""

        payload = dict(row)
        payload.setdefault("name", "")
        if not payload["name"]:
            raise ValueError("missing primary key for profiles: name")
        return self.append("profiles", payload)

    def write_channel_account(self, row: dict[str, Any]) -> dict[str, Any]:
        """兼容控制面 channel 配置写入。"""

        payload = dict(row)
        payload.setdefault("channel", "")
        payload.setdefault("account_id", "")
        if not payload["channel"] or not payload["account_id"]:
            raise ValueError("missing primary key for channels")
        return self.append("channels", payload)

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
        sql = _inline_sql_params(sql, params)
        command = [
            "psql",
            self.url,
            "-X",
            "-q",
            "-t",
            "-A",
            "-F",
            "\t",
            "-f",
            "-",
        ]
        completed = subprocess.run(
            command,
            input=sql,
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

    def _build_insert_sql(self, table: str) -> str:
        table_name = self._table_name(table)
        columns = self._columns_for(table)
        columns_sql = ", ".join(_quote_ident(column) for column in columns)
        return (
            f"WITH inserted AS ("
            f"INSERT INTO {table_name} ({columns_sql}) "
            "SELECT * FROM json_populate_record(NULL::"
            f"{table_name}, %(row)s::json)"
            " RETURNING *) "
            "SELECT row_to_json(inserted) AS row FROM inserted"
        )

    def _build_upsert_sql(self, table: str) -> str:
        table_name = self._table_name(table)
        primary_key = self._primary_key(table)
        columns = self._columns_for(table)
        assignments = ", ".join(
            f"{_quote_ident(column)} = EXCLUDED.{_quote_ident(column)}"
            for column in columns
            if column != primary_key
        )
        if not assignments:
            assignments = f"{_quote_ident(primary_key)} = EXCLUDED.{_quote_ident(primary_key)}"
        columns_sql = ", ".join(_quote_ident(column) for column in columns)
        return (
            f"WITH upserted AS ("
            f"INSERT INTO {table_name} ({columns_sql}) "
            "SELECT * FROM json_populate_record(NULL::"
            f"{table_name}, %(row)s::json) "
            f"ON CONFLICT ({_quote_ident(primary_key)}) DO UPDATE SET {assignments} "
            "RETURNING *) "
            "SELECT row_to_json(upserted) AS row FROM upserted"
        )

    def _build_bulk_upsert_sql(self, table: str, rows: list[dict[str, Any]]) -> str:
        table_name = self._table_name(table)
        primary_key = self._primary_key(table)
        columns = self._columns_for(table)
        assignments = ", ".join(
            f"{_quote_ident(column)} = EXCLUDED.{_quote_ident(column)}"
            for column in columns
            if column != primary_key
        )
        if not assignments:
            assignments = f"{_quote_ident(primary_key)} = EXCLUDED.{_quote_ident(primary_key)}"
        columns_sql = ", ".join(_quote_ident(column) for column in columns)
        return (
            f"INSERT INTO {table_name} ({columns_sql}) "
            f"SELECT {columns_sql} FROM json_populate_recordset(NULL::{table_name}, {_json_sql_literal(rows)}) "
            f"ON CONFLICT ({_quote_ident(primary_key)}) DO UPDATE SET {assignments};"
        )

    def _append_session(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for sessions: id")
        sql = self._build_insert_sql("sessions")
        rows = self.query("sessions", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _append_delivery_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_delivery_entry(row, state=str(row.get("state", "pending")))
        if not payload.get("id"):
            raise ValueError("missing primary key for delivery_entries: id")
        sql = self._build_upsert_sql("delivery_entries")
        rows = self.query("delivery_entries", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_delivery_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_delivery_entry(row)

    @staticmethod
    def _normalize_delivery_entry(row: dict[str, Any], *, state: str) -> dict[str, Any]:
        now = time.time()
        metadata = row.get("metadata", {})
        return {
            "id": str(row.get("id", "")),
            "state": state or "pending",
            "channel": str(row.get("channel", "")),
            "to": str(row.get("to", "")),
            "text": str(row.get("text", "")),
            "retry_count": int(row.get("retry_count", 0) or 0),
            "last_error": str(row.get("last_error") or ""),
            "metadata": dict(metadata if isinstance(metadata, dict) else {}),
            "enqueued_at": float(row.get("enqueued_at", now) or now),
            "next_retry_at": float(row.get("next_retry_at", 0.0) or 0.0),
            "locked_by": str(row.get("locked_by") or ""),
            "locked_at": float(row.get("locked_at", 0.0) or 0.0),
            "updated_at": float(row.get("updated_at", now) or now),
        }

    def _delete_delivery_entry(self, key: str) -> bool:
        sql = "DELETE FROM delivery_entries WHERE id = %(id)s"
        self.query("delivery_entries", sql=sql, params={"id": key})
        return True

    def _upsert_session(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for sessions: id")
        if "message_count" not in payload:
            payload["message_count"] = 0
        sql = self._build_upsert_sql("sessions")
        rows = self.query("sessions", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_session(self, key: str) -> bool:
        sql = "DELETE FROM sessions WHERE id = %(id)s"
        self.query("sessions", sql=sql, params={"id": key})
        return True

    def _append_task(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for tasks: id")
        sql = self._build_insert_sql("tasks")
        rows = self.query("tasks", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_task(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for tasks: id")
        sql = self._build_upsert_sql("tasks")
        rows = self.query("tasks", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_task(self, key: str) -> bool:
        sql = "DELETE FROM tasks WHERE id = %(id)s"
        self.query("tasks", sql=sql, params={"id": key})
        return True

    def _append_runtime_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("event_id"):
            raise ValueError("missing primary key for runtime_events: event_id")
        sql = self._build_insert_sql("runtime_events")
        rows = self.query("runtime_events", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_runtime_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("event_id"):
            raise ValueError("missing primary key for runtime_events: event_id")
        sql = self._build_upsert_sql("runtime_events")
        rows = self.query("runtime_events", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_runtime_event(self, key: str) -> bool:
        sql = "DELETE FROM runtime_events WHERE event_id = %(id)s"
        self.query("runtime_events", sql=sql, params={"id": key})
        return True

    def _append_memory_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for memory_entries: id")
        sql = self._build_insert_sql("memory_entries")
        rows = self.query("memory_entries", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_memory_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for memory_entries: id")
        sql = self._build_upsert_sql("memory_entries")
        rows = self.query("memory_entries", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_memory_entry(self, key: str) -> bool:
        sql = "DELETE FROM memory_entries WHERE id = %(id)s"
        self.query("memory_entries", sql=sql, params={"id": key})
        return True

    def _append_feishu_dedup_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("event_id"):
            raise ValueError("missing primary key for feishu_dedup_entries: event_id")
        sql = self._build_upsert_sql("feishu_dedup_entries")
        rows = self.query("feishu_dedup_entries", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_feishu_dedup_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_feishu_dedup_entry(row)

    def _delete_feishu_dedup_entry(self, key: str) -> bool:
        self.query(
            "feishu_dedup_entries",
            sql="DELETE FROM feishu_dedup_entries WHERE event_id = %(id)s",
            params={"id": key},
        )
        return True

    def _append_feishu_webhook_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for feishu_webhook_events: id")
        sql = self._build_insert_sql("feishu_webhook_events")
        rows = self.query("feishu_webhook_events", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_feishu_webhook_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for feishu_webhook_events: id")
        sql = self._build_upsert_sql("feishu_webhook_events")
        rows = self.query("feishu_webhook_events", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_feishu_webhook_event(self, key: str) -> bool:
        self.query(
            "feishu_webhook_events",
            sql="DELETE FROM feishu_webhook_events WHERE id = %(id)s",
            params={"id": key},
        )
        return True

    def _append_feishu_onboarding_session(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("session_id"):
            raise ValueError("missing primary key for feishu_onboarding_sessions: session_id")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("feishu_onboarding_sessions")
        rows = self.query("feishu_onboarding_sessions", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_feishu_onboarding_session(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_feishu_onboarding_session(row)

    def _delete_feishu_onboarding_session(self, key: str) -> bool:
        self.query(
            "feishu_onboarding_sessions",
            sql="DELETE FROM feishu_onboarding_sessions WHERE session_id = %(id)s",
            params={"id": key},
        )
        return True

    def _append_channel_offset(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("key"):
            channel = str(payload.get("channel", ""))
            account_id = str(payload.get("account_id", ""))
            payload["key"] = f"{channel}\x1f{account_id}"
        if not payload.get("key"):
            raise ValueError("missing primary key for channel_offsets: key")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("channel_offsets")
        rows = self.query("channel_offsets", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_channel_offset(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_channel_offset(row)

    def _delete_channel_offset(self, key: str) -> bool:
        self.query("channel_offsets", sql="DELETE FROM channel_offsets WHERE key = %(id)s", params={"id": key})
        return True

    def _append_cron_run(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for cron_runs: id")
        sql = self._build_upsert_sql("cron_runs")
        rows = self.query("cron_runs", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_cron_run(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_cron_run(row)

    def _delete_cron_run(self, key: str) -> bool:
        self.query("cron_runs", sql="DELETE FROM cron_runs WHERE id = %(id)s", params={"id": key})
        return True

    def _append_news_item(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("key"):
            store_name = str(payload.get("store_name", ""))
            state = str(payload.get("state", ""))
            item_id = str(payload.get("item_id", ""))
            payload["key"] = f"{store_name}\x1f{state}\x1f{item_id}"
        if not payload.get("key"):
            raise ValueError("missing primary key for news_items: key")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("news_items")
        rows = self.query("news_items", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_news_item(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_news_item(row)

    def _delete_news_item(self, key: str) -> bool:
        self.query("news_items", sql="DELETE FROM news_items WHERE key = %(id)s", params={"id": key})
        return True

    def _append_feishu_card_state(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("card_id"):
            raise ValueError("missing primary key for feishu_card_states: card_id")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("feishu_card_states")
        rows = self.query("feishu_card_states", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_feishu_card_state(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_feishu_card_state(row)

    def _delete_feishu_card_state(self, key: str) -> bool:
        self.query(
            "feishu_card_states",
            sql="DELETE FROM feishu_card_states WHERE card_id = %(id)s",
            params={"id": key},
        )
        return True

    def _append_config_audit(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for config_audits: id")
        sql = self._build_insert_sql("config_audits")
        rows = self.query("config_audits", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_config_audit(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for config_audits: id")
        sql = self._build_upsert_sql("config_audits")
        rows = self.query("config_audits", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _delete_config_audit(self, key: str) -> bool:
        sql = "DELETE FROM config_audits WHERE id = %(id)s"
        self.query("config_audits", sql=sql, params={"id": key})
        return True

    def _append_agents_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("id"):
            raise ValueError("missing primary key for agents: id")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("agents")
        rows = self.query("agents", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_agents_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_agents_row(row)

    def _delete_agents_row(self, key: str) -> bool:
        self.query("agents", sql="DELETE FROM agents WHERE id = %(id)s", params={"id": key})
        return True

    def _append_bindings_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("agent_id") or not payload.get("match_key"):
            raise ValueError("missing primary key for bindings")
        payload["key"] = f"{payload['agent_id']}\x1f{payload['match_key']}\x1f{payload.get('match_value', '')}"
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("bindings")
        rows = self.query("bindings", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_bindings_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_bindings_row(row)

    def _delete_bindings_row(self, key: str) -> bool:
        agent_id, match_key, match_value = key.split("\x1f", 2) if "\x1f" in key else (key, "", "")
        sql = (
            "DELETE FROM bindings WHERE agent_id = %(agent_id)s "
            "AND match_key = %(match_key)s AND match_value = %(match_value)s"
        )
        self.query("bindings", sql=sql, params={"agent_id": agent_id, "match_key": match_key, "match_value": match_value})
        return True

    def _append_profiles_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("name"):
            raise ValueError("missing primary key for profiles: name")
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("profiles")
        rows = self.query("profiles", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_profiles_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_profiles_row(row)

    def _delete_profiles_row(self, key: str) -> bool:
        self.query("profiles", sql="DELETE FROM profiles WHERE name = %(id)s", params={"id": key})
        return True

    def _append_channels_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if not payload.get("channel") or not payload.get("account_id"):
            raise ValueError("missing primary key for channels")
        payload["key"] = f"{payload['channel']}\x1f{payload['account_id']}"
        payload.setdefault("updated_at", time.time())
        sql = self._build_upsert_sql("channels")
        rows = self.query("channels", sql=sql, params={"row": payload})
        return rows[0] if rows else payload

    def _upsert_channels_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._append_channels_row(row)

    def _delete_channels_row(self, key: str) -> bool:
        channel, account_id = key.split("\x1f", 1) if "\x1f" in key else (key, "")
        self.query("channels", sql="DELETE FROM channels WHERE channel = %(channel)s AND account_id = %(account_id)s", params={"channel": channel, "account_id": account_id})
        return True

    @staticmethod
    def _table_name(table: str) -> str:
        allowed = {
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
            "feishu_dedup_entries",
            "feishu_webhook_events",
            "feishu_onboarding_sessions",
            "channel_offsets",
            "cron_runs",
            "news_items",
            "feishu_card_states",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        return table

    @staticmethod
    def _primary_key(table: str) -> str:
        mapping = {
            "agents": "id",
            "bindings": "key",
            "profiles": "name",
            "channels": "key",
            "delivery_entries": "id",
            "sessions": "id",
            "tasks": "id",
            "runtime_events": "event_id",
            "errors": "id",
            "metrics": "id",
            "memory_entries": "id",
            "config_audits": "id",
            "feishu_dedup_entries": "event_id",
            "feishu_webhook_events": "id",
            "feishu_onboarding_sessions": "session_id",
            "channel_offsets": "key",
            "cron_runs": "id",
            "news_items": "key",
            "feishu_card_states": "card_id",
        }
        if table not in mapping:
            raise ValueError(f"unsupported table: {table}")
        return mapping[table]

    @staticmethod
    def _columns_for(table: str) -> tuple[str, ...]:
        for spec in POSTGRES_STATE_TABLES:
            if spec.name == table:
                return spec.columns
        raise ValueError(f"unsupported table: {table}")


POSTGRES_STATE_TABLES: tuple[PostgresTableSpec, ...] = (
    PostgresTableSpec(
        name="agents",
        primary_key="id",
        time_column="updated_at",
        columns=(
            "id",
            "name",
            "personality",
            "model",
            "dm_scope",
            "extra_system",
            "tool_policy",
            "memory_policy",
            "prompt_policy",
            "updated_at",
        ),
        indexes=(("updated_at",),),
    ),
    PostgresTableSpec(
        name="bindings",
        primary_key="key",
        time_column="updated_at",
        columns=(
            "key",
            "agent_id",
            "tier",
            "match_key",
            "match_value",
            "priority",
            "updated_at",
        ),
        indexes=(("agent_id",), ("match_key", "match_value"),),
    ),
    PostgresTableSpec(
        name="profiles",
        primary_key="name",
        time_column="updated_at",
        columns=(
            "name",
            "provider",
            "api_key",
            "api_key_env",
            "base_url",
            "base_url_env",
            "updated_at",
        ),
        indexes=(("provider",),),
    ),
    PostgresTableSpec(
        name="channels",
        primary_key="key",
        time_column="updated_at",
        columns=(
            "key",
            "channel",
            "account_id",
            "enabled",
            "label",
            "token",
            "token_env",
            "config",
            "updated_at",
        ),
        indexes=(("channel", "account_id"),),
    ),
    PostgresTableSpec(
        name="delivery_entries",
        primary_key="id",
        time_column="updated_at",
        columns=(
            "id",
            "state",
            "channel",
            "to",
            "text",
            "retry_count",
            "last_error",
            "metadata",
            "enqueued_at",
            "next_retry_at",
            "locked_by",
            "locked_at",
            "updated_at",
        ),
        indexes=(("state", "next_retry_at"), ("state", "enqueued_at"), ("channel", "state"), ("locked_by", "locked_at")),
    ),
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
    PostgresTableSpec(
        name="feishu_dedup_entries",
        primary_key="event_id",
        time_column="seen_at",
        columns=("event_id", "seen_at", "expires_at", "metadata"),
        indexes=(("expires_at",),),
    ),
    PostgresTableSpec(
        name="feishu_webhook_events",
        primary_key="id",
        time_column="received_at",
        columns=(
            "id",
            "received_at",
            "outcome",
            "reason",
            "http_status",
            "channel_account",
            "event_id",
            "message_id",
            "chat_id",
            "chat_type",
            "sender_open_id",
            "sender_user_id",
            "body_sha256",
            "metadata",
        ),
        indexes=(("received_at",), ("outcome", "received_at"), ("channel_account", "event_id")),
    ),
    PostgresTableSpec(
        name="feishu_onboarding_sessions",
        primary_key="session_id",
        time_column="updated_at",
        columns=(
            "session_id",
            "binding_code",
            "mode",
            "status",
            "account_id",
            "agent_id",
            "agent_name",
            "created_at",
            "expires_at",
            "bound_at",
            "bound_peer_id",
            "bound_sender_id",
            "bound_is_group",
            "last_error",
            "updated_at",
            "metadata",
        ),
        indexes=(("status", "updated_at"), ("binding_code",), ("account_id", "updated_at")),
    ),
    PostgresTableSpec(
        name="channel_offsets",
        primary_key="key",
        time_column="updated_at",
        columns=("key", "channel", "account_id", "offset_value", "updated_at", "metadata"),
        indexes=(("channel", "account_id"), ("updated_at",)),
    ),
    PostgresTableSpec(
        name="cron_runs",
        primary_key="id",
        time_column="run_at",
        columns=(
            "id",
            "job_id",
            "config_id",
            "agent_id",
            "scope",
            "run_at",
            "status",
            "output_preview",
            "error",
            "metadata",
        ),
        indexes=(("job_id", "run_at"), ("agent_id", "run_at"), ("status", "run_at")),
    ),
    PostgresTableSpec(
        name="news_items",
        primary_key="key",
        time_column="updated_at",
        columns=(
            "key",
            "store_name",
            "state",
            "item_id",
            "source_id",
            "source_type",
            "title",
            "url",
            "published_at",
            "summary",
            "tags",
            "seen_at",
            "collected_at",
            "updated_at",
            "metadata",
        ),
        indexes=(("store_name", "state"), ("source_id", "updated_at"), ("updated_at",)),
    ),
    PostgresTableSpec(
        name="feishu_card_states",
        primary_key="card_id",
        time_column="updated_at",
        columns=(
            "card_id",
            "owner_channel",
            "owner_account_id",
            "peer_id",
            "message_id",
            "title",
            "summary",
            "template",
            "card_link",
            "blocks",
            "structured_blocks",
            "actions",
            "page_size",
            "page_index",
            "expanded",
            "updated_at",
            "metadata",
        ),
        indexes=(("owner_account_id", "updated_at"), ("peer_id", "updated_at"), ("message_id",)),
    ),
)
