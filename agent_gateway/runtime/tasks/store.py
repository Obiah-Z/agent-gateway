from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Iterable

from agent_gateway.runtime.tasks.models import TaskInstance, TaskStatus
from typing import Any


TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class LocalTaskStore:
    """基于本地 JSON 文件的任务实例存储。

    这是 Phase 20.3 的本地 backend，后续可以平滑替换为 PostgreSQL 或 Redis/RabbitMQ
    队列，但任务状态语义先在这里固化。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.backup_sink = None
        self.read_backend: Any | None = None
        self.write_backend: Any | None = None

    def create(self, task: TaskInstance) -> TaskInstance:
        """写入新任务。"""

        task.updated_at = time.time()
        self._write_primary(task)
        self.write_task_to_disk(task)
        return task

    def get(self, task_id: str) -> TaskInstance | None:
        """按 ID 读取任务。"""

        if self.read_backend is not None:
            try:
                row = self.read_backend.get("tasks", task_id)
                if row is not None:
                    return TaskInstance.from_dict(row)
            except Exception:
                pass
        return self._read(self._task_path(task_id))

    def list(
        self,
        *,
        statuses: Iterable[TaskStatus] | None = None,
        limit: int = 50,
    ) -> list[TaskInstance]:
        """按更新时间倒序列出任务。"""

        if self.read_backend is not None:
            try:
                rows = self.read_backend.list("tasks", limit=limit, filters={"statuses": list(statuses or [])})
                tasks = [TaskInstance.from_dict(row) for row in rows]
                if tasks:
                    return tasks[: max(1, limit)]
            except Exception:
                pass
        status_set = set(statuses or [])
        rows = []
        for path in self.root.glob("*.json"):
            task = self._read(path)
            if task is None:
                continue
            if status_set and task.status not in status_set:
                continue
            rows.append(task)
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        return rows[: max(1, limit)]

    def mark_running(self, task_id: str, *, now: float | None = None) -> TaskInstance:
        """把任务标记为 running。"""

        task = self._require(task_id)
        current = time.time() if now is None else now
        task.status = "running"
        task.started_at = current
        task.updated_at = current
        self._write_primary(task)
        self.write_task_to_disk(task)
        return task

    def mark_done(
        self,
        task_id: str,
        *,
        result_preview: str = "",
        now: float | None = None,
    ) -> TaskInstance:
        """把任务标记为 done。"""

        task = self._require(task_id)
        current = time.time() if now is None else now
        task.status = "done"
        task.result_preview = result_preview[:500]
        task.finished_at = current
        task.updated_at = current
        self._write_primary(task)
        self.write_task_to_disk(task)
        return task

    def mark_failed(
        self,
        task_id: str,
        *,
        error: str,
        retryable: bool = False,
        now: float | None = None,
    ) -> TaskInstance:
        """把任务标记为 failed 或 retrying。"""

        task = self._require(task_id)
        current = time.time() if now is None else now
        task.status = "retrying" if retryable else "failed"
        task.error = error
        task.retry_count += 1 if retryable else 0
        task.finished_at = 0.0 if retryable else current
        task.updated_at = current
        self._write_primary(task)
        self.write_task_to_disk(task)
        return task

    def cancel(self, task_id: str, *, now: float | None = None) -> TaskInstance:
        """把任务标记为 cancelled。"""

        task = self._require(task_id)
        current = time.time() if now is None else now
        task.status = "cancelled"
        task.finished_at = current
        task.updated_at = current
        self._write_primary(task)
        self.write_task_to_disk(task)
        return task

    def _require(self, task_id: str) -> TaskInstance:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def _write(self, task: TaskInstance) -> None:
        self.write_task_to_disk(task)

    def write_task_to_disk(self, task: TaskInstance) -> None:
        """仅写入本地 JSON 文件，不触发备份镜像。"""

        final_path = self._task_path(task.id)
        tmp_path = self.root / f".tmp.{task.id}.json"
        payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        with self._lock, tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
        tmp_path.replace(final_path)

    def _mirror(self, task: TaskInstance) -> None:
        """把任务状态镜像到备份 sink。"""

        sink = getattr(self, "backup_sink", None)
        if sink is None:
            return
        method = getattr(sink, "write_task", None)
        if method is None:
            return
        try:
            method(task)
        except Exception:
            pass

    def _write_primary(self, task: TaskInstance) -> None:
        """优先写入数据库主存储；不可用时退回备份 sink。"""

        backend = getattr(self, "write_backend", None)
        if backend is not None:
            method = getattr(backend, "write_task", None)
            if method is not None:
                try:
                    method(task)
                    return
                except Exception:
                    pass
        self._mirror(task)

    @staticmethod
    def _read(path: Path) -> TaskInstance | None:
        if not path.exists():
            return None
        try:
            return TaskInstance.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _task_path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError("invalid task id")
        return self.root / f"{task_id}.json"
