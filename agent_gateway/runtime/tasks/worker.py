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
    ) -> None:
        self.queue = queue
        self.worker_id = worker_id
        self.concurrency = max(1, concurrency)
        self.poll_interval = max(0.05, poll_interval)
        self.retry_exceptions = retry_exceptions
        self.handlers: dict[str, TaskHandler] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = False
        self._running = False

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
        task = self.queue.reserve(
            worker_id=self.worker_id,
            task_types=self.handlers.keys(),
        )
        if task is None:
            return False
        await self._execute(task)
        return True

    def stats(self) -> dict[str, Any]:
        """返回 worker 与队列状态。"""

        return {
            "running": self._running,
            "worker_id": self.worker_id,
            "concurrency": self.concurrency,
            "registered_task_types": sorted(self.handlers),
            "queue": self.queue.stats(),
        }

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
