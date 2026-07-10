from __future__ import annotations

import time
from typing import Any, Iterable

from agent_gateway.runtime.tasks.models import TaskInstance, TaskStatus
from agent_gateway.runtime.tasks.session_scheduler import SessionTaskClaim
from agent_gateway.runtime.tasks.store import LocalTaskStore


class LocalTaskQueue:
    """本地任务队列接口。

    当前实现基于 `LocalTaskStore`，用于先固化 enqueue/reserve/ack/retry/fail 语义。
    后续 Redis Streams、RabbitMQ 或 PostgreSQL backend 应保持同样接口。
    """

    def __init__(
        self,
        store: LocalTaskStore,
        *,
        broker: Any | None = None,
        session_scheduler: Any | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self.session_scheduler = session_scheduler

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
        """创建 pending 任务并写入存储。

        如果调用方提供了幂等键，则同一任务只允许创建一次。重复入站时返回已有
        task_id，避免不同接入路径或 broker 重投造成同一用户消息被重复执行。
        """

        if idempotency_key:
            existing = self.store.find_by_idempotency_key(
                idempotency_key=idempotency_key,
                task_type=task_type,
                source=source,
            )
            if existing is not None:
                if existing.status in {"pending", "retrying"}:
                    self._publish_ready(existing)
                return existing

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
        created = self.store.create(task)
        self._publish_ready(created)
        return created

    def reserve_session_claim(
        self,
        *,
        worker_id: str,
        task_types: Iterable[str] | None = None,
        ttl_seconds: int | None = None,
        max_claim_attempts: int = 16,
        now: float | None = None,
    ) -> tuple[TaskInstance, SessionTaskClaim] | None:
        """通过 Redis session 调度器声明并预占一个 session 队首任务。"""

        scheduler = self.session_scheduler
        if scheduler is None or not getattr(scheduler, "enabled", False):
            return None
        attempts = max(1, int(max_claim_attempts))
        for _ in range(attempts):
            claim = scheduler.claim_next(
                worker_id=worker_id,
                task_types=task_types,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            if claim is None:
                return None
            task = self.reserve_task_id(
                claim.task_id,
                worker_id=worker_id,
                task_types=task_types,
                now=now,
            )
            if task is not None:
                return task, claim
            scheduler.release(claim)
        return None

    def release_session_claim(self, claim: SessionTaskClaim) -> bool:
        """释放 Redis session claim；未启用 scheduler 时视为成功。"""

        scheduler = self.session_scheduler
        if scheduler is None or not getattr(scheduler, "enabled", False):
            return True
        try:
            return bool(scheduler.release(claim))
        except Exception:
            return False

    def renew_session_claim(
        self,
        claim: SessionTaskClaim,
        *,
        ttl_seconds: int | None = None,
    ) -> bool:
        """续租 Redis session claim，用于长模型调用期间保持 busy owner。"""

        scheduler = self.session_scheduler
        if scheduler is None or not getattr(scheduler, "enabled", False):
            return True
        try:
            return bool(scheduler.renew(claim, ttl_seconds=ttl_seconds))
        except Exception:
            return False

    def _publish_ready(self, task: TaskInstance) -> None:
        """把任务写入调度索引并发布 broker 唤醒消息。"""

        scheduler = self.session_scheduler
        if scheduler is not None and getattr(scheduler, "enabled", False):
            try:
                scheduler.enqueue(task)
            except Exception:
                pass
        if self.broker is not None:
            try:
                self.broker.publish(task)
            except Exception:
                pass

    def reserve(
        self,
        *,
        worker_id: str,
        task_types: Iterable[str] | None = None,
        blocked_session_keys: Iterable[str] | None = None,
        now: float | None = None,
    ) -> TaskInstance | None:
        """预占一条可执行任务，并标记为 running。"""

        type_list = list(task_types or [])
        blocked_sessions = {str(item) for item in (blocked_session_keys or []) if str(item)}
        reserved = self._reserve_primary(
            worker_id=worker_id,
            task_types=type_list,
            blocked_session_keys=blocked_sessions,
            now=now,
        )
        if reserved is not None:
            self.store.persist_task_state(reserved)
            return reserved
        if self._has_enabled_primary_reserve("reserve_task"):
            return None
        candidates = self.store.list(statuses=["pending", "retrying"], limit=500)
        type_set = set(type_list)
        if type_set:
            candidates = [task for task in candidates if task.task_type in type_set]
        if blocked_sessions:
            candidates = [
                task
                for task in candidates
                if not task.session_key or task.session_key not in blocked_sessions
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.priority, item.created_at))
        task = candidates[0]
        task.metadata = {**task.metadata, "worker_id": worker_id}
        self.store.create(task)
        return self.store.mark_running(task.id, now=now)

    def reserve_task_id(
        self,
        task_id: str,
        *,
        worker_id: str,
        task_types: Iterable[str] | None = None,
        blocked_session_keys: Iterable[str] | None = None,
        now: float | None = None,
    ) -> TaskInstance | None:
        """按任务 ID 精确预占任务。

        RabbitMQ 入站消息只携带 task_id，消费端必须回到 TaskStore 做状态校验，
        防止过期 broker 消息重复执行已经完成或取消的任务。
        """

        type_list = list(task_types or [])
        blocked_sessions = {str(item) for item in (blocked_session_keys or []) if str(item)}
        reserved = self._reserve_task_id_primary(
            task_id,
            worker_id=worker_id,
            task_types=type_list,
            blocked_session_keys=blocked_sessions,
            now=now,
        )
        if reserved is not None:
            self.store.persist_task_state(reserved)
            return reserved
        if self._has_enabled_primary_reserve("reserve_task_id"):
            return None
        task = self.store.get(task_id)
        if task is None:
            return None
        if task.status not in {"pending", "retrying"}:
            return None
        type_set = set(type_list)
        if type_set and task.task_type not in type_set:
            return None
        if task.session_key and task.session_key in blocked_sessions:
            return None
        task.metadata = {**task.metadata, "worker_id": worker_id}
        self.store.create(task)
        return self.store.mark_running(task.id, now=now)

    def _has_enabled_primary_reserve(self, method_name: str) -> bool:
        """判断是否存在启用中的主存储抢占入口。

        多 worker 模式下，PostgreSQL 是任务状态的事实来源。主存储返回 None
        表示当前 worker 没抢到任务，不能继续降级读取共享 JSON 文件，否则会把
        已被其他 worker 抢占的任务重复执行。只有未配置或显式 disabled 的主存储
        才允许本地文件 fallback。
        """

        backend = getattr(self.store, "write_backend", None)
        method = getattr(backend, method_name, None) if backend is not None else None
        if method is None:
            return False
        return bool(getattr(backend, "enabled", True))

    def _reserve_primary(
        self,
        *,
        worker_id: str,
        task_types: list[str],
        blocked_session_keys: set[str],
        now: float | None,
    ) -> TaskInstance | None:
        """优先通过数据库原子预占任务，避免多 worker 重复消费。"""

        backend = getattr(self.store, "write_backend", None)
        method = getattr(backend, "reserve_task", None) if backend is not None else None
        if method is None:
            return None
        try:
            row = method(
                worker_id=worker_id,
                task_types=task_types,
                blocked_session_keys=sorted(blocked_session_keys),
                now=now,
            )
        except Exception:
            return None
        if not isinstance(row, dict):
            return None
        try:
            return TaskInstance.from_dict(row)
        except (KeyError, TypeError, ValueError):
            return None

    def _reserve_task_id_primary(
        self,
        task_id: str,
        *,
        worker_id: str,
        task_types: list[str],
        blocked_session_keys: set[str],
        now: float | None,
    ) -> TaskInstance | None:
        """优先通过数据库原子预占指定任务。"""

        backend = getattr(self.store, "write_backend", None)
        method = getattr(backend, "reserve_task_id", None) if backend is not None else None
        if method is None:
            return None
        try:
            row = method(
                task_id=task_id,
                worker_id=worker_id,
                task_types=task_types,
                blocked_session_keys=sorted(blocked_session_keys),
                now=now,
            )
        except Exception:
            return None
        if not isinstance(row, dict):
            return None
        try:
            return TaskInstance.from_dict(row)
        except (KeyError, TypeError, ValueError):
            return None

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

    def stats(self) -> dict[str, Any]:
        """返回任务队列状态计数。"""

        counts: dict[str, Any] = {
            "pending": 0,
            "running": 0,
            "retrying": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for task in self.store.list(limit=10_000):
            counts[task.status] = counts.get(task.status, 0) + 1
        broker = getattr(self, "broker", None)
        if broker is not None:
            stats_method = getattr(broker, "stats", None)
            if stats_method is not None:
                try:
                    counts["broker"] = stats_method()
                except Exception as exc:
                    counts["broker"] = {"error": str(exc)}
        return counts
