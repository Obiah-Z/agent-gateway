from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


StateTableName = str


@runtime_checkable
class StateRepository(Protocol):
    """统一状态仓储抽象。

    先固化 list/get/append/upsert/query/delete 这些最常用能力，后续 PostgreSQL、
    JSONL、甚至内存实现都可以挂在同一接口下。
    """

    def list(
        self,
        table: StateTableName,
        *,
        limit: int = 50,
        cursor: str = "",
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按条件列出记录。"""

    def get(self, table: StateTableName, key: str) -> dict[str, Any] | None:
        """按主键读取单条记录。"""

    def append(self, table: StateTableName, row: dict[str, Any]) -> dict[str, Any]:
        """追加一条记录。"""

    def upsert(self, table: StateTableName, row: dict[str, Any]) -> dict[str, Any]:
        """写入或更新一条记录。"""

    def delete(self, table: StateTableName, key: str) -> bool:
        """删除一条记录。"""

    def query(
        self,
        table: StateTableName,
        *,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行受控查询。"""


@runtime_checkable
class StateReadRepository(Protocol):
    """只读状态仓储抽象。"""

    def list(self, table: StateTableName, *, limit: int = 50, cursor: str = "", filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """按条件列出记录。"""

    def get(self, table: StateTableName, key: str) -> dict[str, Any] | None:
        """按主键读取单条记录。"""


STATE_TABLES: tuple[StateTableName, ...] = (
    "sessions",
    "tasks",
    "agent_orchestration_runs",
    "agent_orchestration_steps",
    "runtime_events",
    "errors",
    "metrics",
    "memory_entries",
    "config_audits",
)
