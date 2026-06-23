from __future__ import annotations

import concurrent.futures
import threading
import time
from collections import deque
from typing import Any, Callable


class LaneQueue:
    """单条并发车道。

    同一车道内的任务按 FIFO 排队执行，并受 `max_concurrency` 限制；不同车道之间互不影响。
    """

    def __init__(self, name: str, max_concurrency: int = 1) -> None:
        self.name = name
        self.max_concurrency = max(1, max_concurrency)
        self._deque: deque[tuple[Callable[[], Any], concurrent.futures.Future, int]] = deque()
        self._condition = threading.Condition()
        self._active_count = 0
        self._generation = 0

    @property
    def generation(self) -> int:
        """返回当前代际编号，用于 reset 后丢弃旧队列延续。"""

        with self._condition:
            return self._generation

    @generation.setter
    def generation(self, value: int) -> None:
        """更新代际编号，并唤醒等待中的线程。"""

        with self._condition:
            self._generation = value
            self._condition.notify_all()

    def enqueue(
        self,
        fn: Callable[[], Any],
        generation: int | None = None,
    ) -> concurrent.futures.Future:
        """把一个任务放入当前车道并返回 future。"""

        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._condition:
            gen = generation if generation is not None else self._generation
            self._deque.append((fn, future, gen))
            self._pump_locked()
        return future

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """等待当前车道清空，用于 CLI 等需要串行观感的场景。"""

        deadline = time.monotonic() + timeout if timeout is not None else None
        with self._condition:
            while self._active_count > 0 or self._deque:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                self._condition.wait(timeout=remaining)
            return True

    def stats(self) -> dict[str, Any]:
        """返回当前车道的运行指标。"""

        with self._condition:
            return {
                "name": self.name,
                "queue_depth": len(self._deque),
                "active": self._active_count,
                "max_concurrency": self.max_concurrency,
                "generation": self._generation,
            }

    def _pump_locked(self) -> None:
        """在锁内拉起新的工作线程，直到达到并发上限。"""

        while self._active_count < self.max_concurrency and self._deque:
            fn, future, generation = self._deque.popleft()
            self._active_count += 1
            thread = threading.Thread(
                target=self._run_task,
                args=(fn, future, generation),
                daemon=True,
                name=f"lane-{self.name}",
            )
            thread.start()

    def _run_task(
        self,
        fn: Callable[[], Any],
        future: concurrent.futures.Future,
        generation: int,
    ) -> None:
        """执行单个任务，并在结束后推动下一项。"""

        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)
        finally:
            self._task_done(generation)

    def _task_done(self, generation: int) -> None:
        """回收一个活动任务，并在同代情况下继续出队。"""

        with self._condition:
            self._active_count -= 1
            if generation == self._generation:
                self._pump_locked()
            self._condition.notify_all()


class CommandQueue:
    """命名车道管理器。

    负责按 lane 名称复用 `LaneQueue`，让不同会话、后台任务或系统任务拥有独立并发控制。
    """

    def __init__(self) -> None:
        self._lanes: dict[str, LaneQueue] = {}
        self._lock = threading.Lock()

    def lane(self, name: str, max_concurrency: int = 1) -> LaneQueue:
        """获取或创建指定名称的车道。"""

        with self._lock:
            if name not in self._lanes:
                self._lanes[name] = LaneQueue(name=name, max_concurrency=max_concurrency)
            return self._lanes[name]

    def enqueue(
        self,
        lane_name: str,
        fn: Callable[[], Any],
        *,
        max_concurrency: int = 1,
    ) -> concurrent.futures.Future:
        """把任务投递到命名车道。"""

        lane = self.lane(lane_name, max_concurrency=max_concurrency)
        return lane.enqueue(fn)

    def reset_all(self) -> None:
        """整体推进代际编号，阻止旧队列继续串行接力。"""

        with self._lock:
            for lane in self._lanes.values():
                lane.generation = lane.generation + 1

    def stats(self) -> dict[str, dict[str, Any]]:
        """汇总所有车道的运行指标。"""

        with self._lock:
            return {name: lane.stats() for name, lane in self._lanes.items()}
