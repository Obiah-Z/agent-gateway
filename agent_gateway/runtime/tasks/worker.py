from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Any

from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.queue import LocalTaskQueue


TaskHandler = Callable[[TaskInstance], Awaitable[str | None] | str | None]


class RetryableTaskError(RuntimeError):
    """任务遇到临时条件失败，应进入 retrying 而不是 failed。"""


class DuplicateRunningTaskError(RuntimeError):
    """同一任务已经被其他 worker 持锁执行，当前副本应确认丢弃。"""


class TaskWorkerRuntime:
    """本地后台任务 worker 运行时。

    当前只负责从 `LocalTaskQueue` 预占任务、调用已注册 handler，并把结果写回任务状态。
    Cron、Skill 等具体任务会在后续阶段逐步迁入。
    """

    def __init__(
        self,
        queue: LocalTaskQueue,
        *,
        worker_id: str = "local-worker",
        concurrency: int = 2,
        poll_interval: float = 1.0,
        retry_exceptions: bool = False,
        event_store: Any | None = None,
    ) -> None:
        self.queue = queue
        self.worker_id = worker_id
        self.concurrency = max(1, concurrency)
        self.poll_interval = max(0.05, poll_interval)
        self.retry_exceptions = retry_exceptions
        self.event_store = event_store
        self.handlers: dict[str, TaskHandler] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = False
        self._running = False
        self._session_lock_skip_count = 0
        self._last_blocked_sessions: list[dict[str, Any]] = []
        self._last_recorded_blocked_signature = ""
        self._next_broker_partition = 0
        self._session_claim_renew_interval = 0.0

    def register_handler(self, task_type: str, handler: TaskHandler) -> None:
        """注册某类任务的执行函数。"""

        if not task_type:
            raise ValueError("task_type is required")
        self.handlers[task_type] = handler

    async def start(self) -> None:
        """启动 worker 循环。"""

        if self._running:
            return
        self._running = True
        self._stopped = False
        self._tasks = [
            asyncio.create_task(self._loop(index), name=f"task-worker-{index}")
            for index in range(self.concurrency)
        ]

    async def stop(self) -> None:
        """停止 worker 循环。"""

        self._stopped = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        self._running = False

    async def run_once(self) -> bool:
        """执行一条可用任务；没有任务时返回 False。"""

        if not self.handlers:
            return False
        scheduler_handled = await self._run_once_from_scheduler()
        if scheduler_handled:
            return True
        broker_handled = await self._run_once_from_broker()
        if broker_handled:
            return True
        task_types = self.handlers.keys()
        scheduler = getattr(self.queue, "session_scheduler", None)
        if scheduler is not None and getattr(scheduler, "enabled", False):
            task_types = [name for name in self.handlers if name != "agent_inbound"]
            if not task_types:
                return False
        blocked_session_keys = self._blocked_session_keys()
        task = self.queue.reserve(
            worker_id=self.worker_id,
            task_types=task_types,
            blocked_session_keys=blocked_session_keys,
        )
        if task is None:
            return False
        await self._execute(task)
        return True

    def stats(self) -> dict[str, Any]:
        """返回 worker 与队列状态。"""

        queue_stats = self.queue.stats()
        broker_stats = dict(queue_stats.get("broker", {}) or {})
        return {
            "running": self._running,
            "worker_id": self.worker_id,
            "concurrency": self.concurrency,
            "registered_task_types": sorted(self.handlers),
            "queue": queue_stats,
            "broker": broker_stats,
            "session_locks": {
                "blocked_session_count": len(self._last_blocked_sessions),
                "skip_count": self._session_lock_skip_count,
                "last_blocked_sessions": list(self._last_blocked_sessions),
            },
        }

    async def _run_once_from_broker(self) -> bool:
        """优先从 RabbitMQ 等外部分发队列消费一条任务引用。"""

        broker = getattr(self.queue, "broker", None)
        if broker is None or not getattr(broker, "enabled", False):
            return False
        partitions = max(1, int(getattr(broker, "partitions", 1)))
        start = self._next_broker_partition % partitions
        for offset in range(partitions):
            partition = (start + offset) % partitions
            consumed = await asyncio.to_thread(
                broker.consume_once,
                partition,
                self._handle_broker_payload_sync,
            )
            self._next_broker_partition = (partition + 1) % partitions
            if consumed:
                return True
        return False

    async def _run_once_from_scheduler(self) -> bool:
        """通过 Redis session 调度器声明一个可执行 session 队首任务。"""

        scheduler = getattr(self.queue, "session_scheduler", None)
        if scheduler is None or not getattr(scheduler, "enabled", False):
            return False
        claimed = self.queue.reserve_session_claim(
            worker_id=self.worker_id,
            task_types=self.handlers.keys(),
        )
        if claimed is None:
            return False
        task, claim = claimed
        await self._execute_claimed_task(task, claim)
        return True

    def _handle_broker_payload_sync(self, payload: dict[str, Any]) -> bool:
        """同步 broker handler：预占指定任务并在线程内执行异步 handler。"""

        task_id = str(payload.get("task_id", ""))
        task_type = str(payload.get("task_type", ""))
        partition = int(payload.get("partition", -1) or -1)
        session_key = str(payload.get("session_key", ""))
        if not task_id:
            self._record_broker_event(
                "task.broker.discarded",
                payload=payload,
                status="warning",
                message="入站 broker 消息缺少 task_id，已丢弃",
                reason="missing task_id",
            )
            return True
        if task_type and task_type not in self.handlers:
            self._record_broker_event(
                "task.broker.requeued",
                payload=payload,
                status="warning",
                message="当前 worker 未注册该任务类型，broker 消息重新入队",
                reason="handler not registered",
            )
            return False
        scheduler = getattr(self.queue, "session_scheduler", None)
        if scheduler is not None and getattr(scheduler, "enabled", False):
            claimed = self.queue.reserve_session_claim(
                worker_id=self.worker_id,
                task_types=self.handlers.keys(),
            )
            if claimed is None:
                self._record_broker_event(
                    "task.broker.acked",
                    payload=payload,
                    status="ok",
                    message="入站 broker 唤醒消息已确认，当前没有可声明的 session 队首任务",
                    reason="no schedulable session",
                )
                return True
            task, claim = claimed
            asyncio.run(self._execute_claimed_task(task, claim))
            stored = self.queue.store.get(task.id)
            self._record_broker_event(
                "task.broker.acked",
                payload={
                    **payload,
                    "task_id": task.id,
                    "partition": partition,
                    "session_key": session_key or task.session_key,
                },
                status="ok" if stored is not None and stored.status == "done" else "warning",
                message="入站 broker 唤醒消息已通过 session 调度器执行并确认",
                reason=f"task status: {getattr(stored, 'status', 'unknown')}",
            )
            return True
        blocked_session_keys = self._blocked_session_keys()
        task = self.queue.reserve_task_id(
            task_id,
            worker_id=self.worker_id,
            task_types=self.handlers.keys(),
            blocked_session_keys=blocked_session_keys,
        )
        if task is None:
            stored = self.queue.store.get(task_id)
            if stored is None or stored.status in {"done", "failed", "cancelled", "running"}:
                self._record_broker_event(
                    "task.broker.discarded",
                    payload=payload,
                    status="ok",
                    message="入站 broker 消息对应任务已不可执行，已确认丢弃",
                    reason=f"task status: {getattr(stored, 'status', 'missing')}",
                )
                return True
            self._record_broker_event(
                "task.broker.requeued",
                payload=payload,
                status="warning",
                message="入站 broker 消息暂无法预占，已重新入队",
                reason=f"task status: {stored.status}",
            )
            return False
        asyncio.run(self._execute(task))
        stored = self.queue.store.get(task.id)
        self._record_broker_event(
            "task.broker.acked",
            payload={
                **payload,
                "partition": partition,
                "session_key": session_key or task.session_key,
            },
            status="ok" if stored is not None and stored.status == "done" else "warning",
            message="入站 broker 消息已执行并确认",
            reason=f"task status: {getattr(stored, 'status', 'unknown')}",
        )
        return True

    def _record_broker_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        status: str,
        message: str,
        reason: str,
    ) -> None:
        """记录 broker 消费决策，便于排查 ack/nack/requeue。"""

        if self.event_store is None:
            return
        task_id = str(payload.get("task_id", ""))
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="task_worker",
                message=message,
                correlation_id=task_id,
                session_key=str(payload.get("session_key", "")),
                metadata={
                    "worker_id": self.worker_id,
                    "task_id": task_id,
                    "task_type": str(payload.get("task_type", "")),
                    "partition": payload.get("partition", -1),
                    "idempotency_key": str(payload.get("idempotency_key", "")),
                    "reason": reason,
                },
            )
        except Exception:
            return

    async def _loop(self, index: int) -> None:
        """单个 worker 协程循环。"""

        del index
        while not self._stopped:
            try:
                handled = await self.run_once()
            except Exception:
                handled = False
            if not handled:
                await asyncio.sleep(self.poll_interval)

    async def _execute(self, task: TaskInstance) -> None:
        """执行任务并更新状态。"""

        handler = self.handlers.get(task.task_type)
        if handler is None:
            self.queue.fail(task.id, error=f"no handler for task_type: {task.task_type}")
            self._record_task_event(
                "task.worker.failed",
                task,
                status="error",
                message="后台任务未找到已注册 handler，已标记失败",
                reason=f"no handler for task_type: {task.task_type}",
            )
            return
        started_at = time.monotonic()
        self._record_task_event(
            "task.worker.started",
            task,
            status="ok",
            message="后台任务开始执行",
        )
        try:
            result = handler(task)
            if asyncio.iscoroutine(result):
                result = await result
            self.queue.ack(task.id, result_preview=str(result or ""))
            self._record_task_event(
                "task.worker.completed",
                task,
                status="ok",
                message="后台任务执行完成",
                duration_seconds=time.monotonic() - started_at,
            )
        except DuplicateRunningTaskError as exc:
            self._record_task_event(
                "task.worker.duplicate_discarded",
                task,
                status="warning",
                message="后台任务重复执行副本已丢弃",
                reason=str(exc),
                duration_seconds=time.monotonic() - started_at,
            )
        except Exception as exc:
            if self.retry_exceptions or isinstance(exc, RetryableTaskError):
                self.queue.retry(task.id, error=str(exc))
                self._record_task_event(
                    "task.worker.retrying",
                    task,
                    status="warning",
                    message="后台任务执行遇到可重试错误，已进入 retrying",
                    reason=str(exc),
                    duration_seconds=time.monotonic() - started_at,
                )
            else:
                self.queue.fail(task.id, error=str(exc))
                self._record_task_event(
                    "task.worker.failed",
                    task,
                    status="error",
                    message="后台任务执行失败，已标记 failed",
                    reason=str(exc),
                    duration_seconds=time.monotonic() - started_at,
                )

    async def _execute_claimed_task(self, task: TaskInstance, claim: Any) -> None:
        """执行 scheduler claim 任务，并在执行期间续租 busy owner。"""

        renew_task = asyncio.create_task(
            self._renew_session_claim_until_cancelled(task, claim),
            name=f"task-session-claim-renew:{task.id}",
        )
        try:
            await self._execute(task)
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            released = self.queue.release_session_claim(claim)
            if not released:
                self._record_task_event(
                    "task.scheduler.release_failed",
                    task,
                    status="error",
                    message="Redis session claim 释放失败，可能存在 owner 过期或被接管",
                    reason="release failed",
                )

    async def _renew_session_claim_until_cancelled(self, task: TaskInstance, claim: Any) -> None:
        """定期续租 scheduler busy owner，覆盖长模型调用和慢工具执行。"""

        interval = self._resolve_session_claim_renew_interval(
            int(getattr(claim, "ttl_seconds", 0) or 0)
        )
        while True:
            await asyncio.sleep(interval)
            renewed = self.queue.renew_session_claim(claim)
            if renewed:
                self._record_task_event(
                    "task.scheduler.renewed",
                    task,
                    status="ok",
                    message="Redis session claim 已续租",
                )
                continue
            self._record_task_event(
                "task.scheduler.renew_failed",
                task,
                status="warning",
                message="Redis session claim 续租失败，当前任务继续执行但后续可能触发接管",
                reason="renew failed",
            )
            return

    @staticmethod
    def _resolve_session_claim_renew_interval(ttl_seconds: int) -> float:
        """计算 scheduler claim 续租间隔，默认取 TTL 三分之一。"""

        if ttl_seconds <= 0:
            return 1.0
        return max(0.1, min(60.0, ttl_seconds / 3.0))

    def _record_task_event(
        self,
        event_type: str,
        task: TaskInstance,
        *,
        status: str,
        message: str,
        reason: str = "",
        duration_seconds: float | None = None,
    ) -> None:
        """记录 worker 执行生命周期事件，不影响任务主路径。"""

        if self.event_store is None:
            return
        handler = self.handlers.get(task.task_type)
        lane_owner: dict[str, Any] = {}
        inspector = getattr(handler, "inspect_session_lane", None)
        if inspector is not None:
            try:
                lane_owner = dict(inspector(task) or {})
            except Exception:
                lane_owner = {}
        metadata: dict[str, Any] = {
            "worker_id": self.worker_id,
            "task_id": task.id,
            "task_type": task.task_type,
            "source": task.source,
            "task_status": task.status,
            "retry_count": task.retry_count,
            "idempotency_key": task.idempotency_key,
            "priority": task.priority,
            "lane_owner": lane_owner,
        }
        if reason:
            metadata["reason"] = reason
        if duration_seconds is not None:
            metadata["duration_seconds"] = round(max(0.0, float(duration_seconds)), 6)
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="task_worker",
                message=message,
                correlation_id=task.id,
                agent_id=task.agent_id,
                session_key=task.session_key,
                metadata=metadata,
            )
        except Exception:
            return

    def _blocked_session_keys(self) -> set[str]:
        """收集 handler 当前要求跳过的 session key。"""

        blocked: set[str] = set()
        samples: list[dict[str, Any]] = []
        for task in self.queue.store.list(statuses=["pending", "retrying"], limit=500):
            if not task.session_key:
                continue
            handler = self.handlers.get(task.task_type)
            checker = getattr(handler, "is_session_locked", None)
            if checker is None:
                continue
            try:
                if checker(task):
                    blocked.add(task.session_key)
                    if len(samples) < 6:
                        inspector = getattr(handler, "inspect_session_lane", None)
                        lane_owner = {}
                        if inspector is not None:
                            lane_owner = dict(inspector(task) or {})
                        samples.append(
                            {
                                "task_id": task.id,
                                "task_type": task.task_type,
                                "source": task.source,
                                "agent_id": task.agent_id,
                                "session_key": task.session_key,
                                "status": task.status,
                                "retry_count": task.retry_count,
                                "lane_owner": lane_owner,
                            }
                        )
            except Exception:
                continue
        if blocked:
            self._session_lock_skip_count += len(blocked)
            signature = "|".join(f"{item['task_id']}:{item['session_key']}" for item in samples)
            if signature != self._last_recorded_blocked_signature:
                self._record_session_lock_skip(samples)
                self._last_recorded_blocked_signature = signature
        else:
            self._last_recorded_blocked_signature = ""
        self._last_blocked_sessions = samples
        return blocked

    def _record_session_lock_skip(self, samples: list[dict[str, Any]]) -> None:
        """把 reserve 阶段跳过的被锁 session 写入运行事件。"""

        if self.event_store is None:
            return
        for sample in samples:
            try:
                self.event_store.record(
                    "agent_inbound.session_locked_skipped",
                    status="warning",
                    component="task_worker",
                    message="入站任务 session 已被其他 worker 持锁，本轮 reserve 跳过",
                    correlation_id=str(sample.get("task_id", "")),
                    agent_id=str(sample.get("agent_id", "")),
                    session_key=str(sample.get("session_key", "")),
                    metadata={
                        "worker_id": self.worker_id,
                        "task_id": sample.get("task_id", ""),
                        "task_type": sample.get("task_type", ""),
                        "source": sample.get("source", ""),
                        "task_status": sample.get("status", ""),
                        "retry_count": sample.get("retry_count", 0),
                        "lane_owner": sample.get("lane_owner", {}),
                    },
                )
            except Exception:
                continue
