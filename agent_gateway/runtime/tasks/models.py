from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "retrying", "done", "failed", "cancelled"]


@dataclass(slots=True)
class TaskInstance:
    """一条可后台执行、可追踪状态的任务实例。"""

    id: str
    task_type: str
    source: str
    status: TaskStatus = "pending"
    agent_id: str = ""
    session_key: str = ""
    priority: int = 100
    idempotency_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    error: str = ""
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        task_type: str,
        source: str,
        agent_id: str = "",
        session_key: str = "",
        priority: int = 100,
        idempotency_key: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TaskInstance":
        """创建一条新的 pending 任务。"""

        return cls(
            id=uuid.uuid4().hex[:16],
            task_type=task_type,
            source=source,
            agent_id=agent_id,
            session_key=session_key,
            priority=priority,
            idempotency_key=idempotency_key,
            payload=payload or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化任务实例。"""

        return {
            "id": self.id,
            "task_type": self.task_type,
            "source": self.source,
            "status": self.status,
            "agent_id": self.agent_id,
            "session_key": self.session_key,
            "priority": self.priority,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "result_preview": self.result_preview,
            "error": self.error,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskInstance":
        """从持久化字典恢复任务实例。"""

        return cls(
            id=str(data["id"]),
            task_type=str(data["task_type"]),
            source=str(data["source"]),
            status=data.get("status", "pending"),
            agent_id=str(data.get("agent_id", "")),
            session_key=str(data.get("session_key", "")),
            priority=int(data.get("priority", 100)),
            idempotency_key=str(data.get("idempotency_key", "")),
            payload=dict(data.get("payload", {}) or {}),
            result_preview=str(data.get("result_preview", "")),
            error=str(data.get("error", "")),
            retry_count=int(data.get("retry_count", 0)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            started_at=float(data.get("started_at", 0.0) or 0.0),
            finished_at=float(data.get("finished_at", 0.0) or 0.0),
            metadata=dict(data.get("metadata", {}) or {}),
        )
