from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.queue import LocalTaskQueue


TaskHandler = Callable[[TaskInstance], Awaitable[str | None] | str | None]


class RetryableTaskError(RuntimeError):
    """任务遇到临时条件失败，应进入 retrying 而不是 failed。"""


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
        broker_handled = await self._run_once_from_broker()
        if broker_handled:
            return True
        blocked_session_keys = self._blocked_session_keys()
        task = self.queue.reserve(
            worker_id=self.worker_id,
            task_types=self.handlers.keys(),
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

    def _handle_broker_payload_sync(self, payload: dict[str, Any]) -> bool:
        """同步 broker handler：预占指定任务并在线程内执行异步 handler。"""

        task_id = str(payload.get("task_id", ""))
        task_type = str(payload.get("task_type", ""))
        if not task_id:
            return True
        if task_type and task_type not in self.handlers:
            return False
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
                return True
            return False
        asyncio.run(self._execute(task))
        return True

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
            return
        try:
            result = handler(task)
            if asyncio.iscoroutine(result):
                result = await result
            self.queue.ack(task.id, result_preview=str(result or ""))
        except Exception as exc:
            if self.retry_exceptions or isinstance(exc, RetryableTaskError):
                self.queue.retry(task.id, error=str(exc))
            else:
                self.queue.fail(task.id, error=str(exc))

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
