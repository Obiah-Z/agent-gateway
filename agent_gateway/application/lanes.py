from __future__ import annotations

import concurrent.futures
import threading
import time
from collections import deque
from typing import Any, Callable


class LaneQueue:
    def __init__(self, name: str, max_concurrency: int = 1) -> None:
        self.name = name
        self.max_concurrency = max(1, max_concurrency)
        self._deque: deque[tuple[Callable[[], Any], concurrent.futures.Future, int]] = deque()
        self._condition = threading.Condition()
        self._active_count = 0
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    @generation.setter
    def generation(self, value: int) -> None:
        with self._condition:
            self._generation = value
            self._condition.notify_all()

    def enqueue(
        self,
        fn: Callable[[], Any],
        generation: int | None = None,
    ) -> concurrent.futures.Future:
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._condition:
            gen = generation if generation is not None else self._generation
            self._deque.append((fn, future, gen))
            self._pump_locked()
        return future

    def wait_for_idle(self, timeout: float | None = None) -> bool:
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
        with self._condition:
            return {
                "name": self.name,
                "queue_depth": len(self._deque),
                "active": self._active_count,
                "max_concurrency": self.max_concurrency,
                "generation": self._generation,
            }

    def _pump_locked(self) -> None:
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
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)
        finally:
            self._task_done(generation)

    def _task_done(self, generation: int) -> None:
        with self._condition:
            self._active_count -= 1
            if generation == self._generation:
                self._pump_locked()
            self._condition.notify_all()


class CommandQueue:
    def __init__(self) -> None:
        self._lanes: dict[str, LaneQueue] = {}
        self._lock = threading.Lock()

    def lane(self, name: str, max_concurrency: int = 1) -> LaneQueue:
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
        lane = self.lane(lane_name, max_concurrency=max_concurrency)
        return lane.enqueue(fn)

    def reset_all(self) -> None:
        with self._lock:
            for lane in self._lanes.values():
                lane.generation = lane.generation + 1

    def stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: lane.stats() for name, lane in self._lanes.items()}
