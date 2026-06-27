from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.store import LocalTaskStore
from agent_gateway.runtime.state.store import SessionStore


class MigrationSink(Protocol):
    """迁移脚手架的备份写入接口。"""

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> None:
        """备份单条会话消息。"""

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> None:
        """备份一整段会话历史。"""

    def write_task(self, task: TaskInstance) -> None:
        """备份单条任务状态。"""

    def write_event(self, event: dict[str, Any]) -> None:
        """备份单条运行事件。"""

    def write_memory(self, content: str, category: str = "general") -> None:
        """备份单条记忆。"""


@dataclass(slots=True)
class LocalMigrationSink(MigrationSink):
    """把现有本地写入口包装成迁移时的备份写。"""

    sessions: SessionStore
    tasks: LocalTaskStore
    events: RuntimeEventStore
    memory: MemoryStore

    def write_session_message(self, agent_id: str, session_key: str, role: str, content: Any) -> None:
        self.sessions.append_message_to_disk(agent_id, session_key, role, content)

    def rewrite_session_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[Any],
    ) -> None:
        self.sessions.rewrite_messages_to_disk(agent_id, session_key, messages)

    def write_task(self, task: TaskInstance) -> None:
        self.tasks.write_task_to_disk(task)

    def write_event(self, event: dict[str, Any]) -> None:
        self.events.write_event_row(event)

    def write_memory(self, content: str, category: str = "general") -> None:
        self.memory.write_memory_migration(content, category=category)
