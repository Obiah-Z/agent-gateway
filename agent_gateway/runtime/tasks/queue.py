from __future__ import annotations

import time
from typing import Iterable

from agent_gateway.runtime.tasks.models import TaskInstance, TaskStatus
from agent_gateway.runtime.tasks.store import LocalTaskStore


class LocalTaskQueue:
    """本地任务队列接口。

    当前实现基于 `LocalTaskStore`，用于先固化 enqueue/reserve/ack/retry/fail 语义。
    后续 Redis Streams、RabbitMQ 或 PostgreSQL backend 应保持同样接口。
    """

    def __init__(self, store: LocalTaskStore) -> None:
        self.store = store

    def enqueue(
        self,
        *,
        task_type: str,
        source: str,
        agent_id: str = "",
        session_key: str = "",
        priority: int = 100,
        idempotency_key: str = "",
        payload: dict | None = None,
        metadata: dict | None = None,
    ) -> TaskInstance:
        """创建 pending 任务并写入存储。"""

        task = TaskInstance.create(
            task_type=task_type,
            source=source,
            agent_id=agent_id,
            session_key=session_key,
            priority=priority,
            idempotency_key=idempotency_key,
            payload=payload or {},
            metadata=metadata or {},
        )
        return self.store.create(task)

    def reserve(
        self,
        *,
        worker_id: str,
        task_types: Iterable[str] | None = None,
        now: float | None = None,
    ) -> TaskInstance | None:
        """预占一条可执行任务，并标记为 running。"""

        candidates = self.store.list(statuses=["pending", "retrying"], limit=500)
        type_set = set(task_types or [])
        if type_set:
            candidates = [task for task in candidates if task.task_type in type_set]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.priority, item.created_at))
        task = candidates[0]
        task.metadata = {**task.metadata, "worker_id": worker_id}
        self.store.create(task)
        return self.store.mark_running(task.id, now=now)

    def ack(
        self,
        task_id: str,
        *,
        result_preview: str = "",
        now: float | None = None,
    ) -> TaskInstance:
        """确认任务执行成功。"""

        return self.store.mark_done(task_id, result_preview=result_preview, now=now)

    def retry(
        self,
        task_id: str,
        *,
        error: str,
        now: float | None = None,
    ) -> TaskInstance:
        """把任务标记为 retrying。"""

        return self.store.mark_failed(task_id, error=error, retryable=True, now=now)

    def fail(
        self,
        task_id: str,
        *,
        error: str,
        now: float | None = None,
    ) -> TaskInstance:
        """把任务标记为 failed。"""

        return self.store.mark_failed(task_id, error=error, retryable=False, now=now)

    def cancel(self, task_id: str, *, now: float | None = None) -> TaskInstance:
        """取消任务。"""

        return self.store.cancel(task_id, now=now)

    def stats(self) -> dict[str, int]:
        """返回任务队列状态计数。"""

        counts: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "retrying": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for task in self.store.list(limit=10_000):
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts
