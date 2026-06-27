from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.repository import StateReadRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.store import LocalTaskStore


@dataclass(slots=True)
class LocalStateReadRepository(StateReadRepository):
    """把现有本地状态类归一到统一只读接口。"""

    sessions: SessionStore
    tasks: LocalTaskStore
    events: RuntimeEventStore
    metrics: MetricsStore
    alerts: AlertStore
    memory: MemoryStore

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
            agent_id = str(filters.get("agent_id", ""))
            return [
                {"session_key": key, "message_count": count}
                for key, count in self.sessions.list_sessions(agent_id=agent_id).items()
            ][: max(1, limit)]
        if table == "tasks":
            statuses = filters.get("statuses")
            return [task.to_dict() for task in self.tasks.list(statuses=statuses, limit=limit)]
        if table == "runtime_events":
            return self.events.tail(limit=limit, **self._event_filters(filters))
        if table == "errors":
            return self.events.recent_errors(
                limit=limit,
                component=str(filters.get("component", "")),
                correlation_id=str(filters.get("correlation_id", "")),
            )
        if table == "metrics":
            return self.metrics.tail(limit=limit)
        if table == "memory_entries":
            return self.memory.recent_entries(limit=limit)
        if table == "config_audits":
            return []
        return []

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        if table == "tasks":
            task = self.tasks.get(key)
            return task.to_dict() if task is not None else None
        if table == "sessions":
            items = self.sessions.list_sessions()
            return {"session_key": key, "message_count": items.get(key)} if key in items else None
        return None

    @staticmethod
    def _event_filters(filters: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "event_type": "event_type",
            "component": "component",
            "status": "status",
            "correlation_id": "correlation_id",
            "agent_id": "agent_id",
            "channel": "channel",
            "job_id": "job_id",
            "delivery_id": "delivery_id",
        }
        return {target: str(filters.get(source, "")) for source, target in keys.items()}
