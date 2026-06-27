import asyncio
from pathlib import Path

from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore, TaskWorkerRuntime


def test_task_worker_run_once_acknowledges_success(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="echo", source="test", payload={"text": "hello"})
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert stored.result_preview == "echo:hello"
    assert stored.metadata["worker_id"] == "worker-1"


def test_task_worker_run_once_returns_false_without_available_task(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    worker = TaskWorkerRuntime(queue)
    worker.register_handler("echo", lambda item: "ok")

    assert asyncio.run(worker.run_once()) is False


def test_task_worker_ignores_unregistered_task_type(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unknown", source="test")
    worker = TaskWorkerRuntime(queue)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is False
    assert stored.status == "pending"


def test_task_worker_retries_handler_exception_when_configured(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unstable", source="test")
    worker = TaskWorkerRuntime(queue, retry_exceptions=True)

    def fail(_task):
        raise RuntimeError("temporary")

    worker.register_handler("unstable", fail)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert stored.retry_count == 1
    assert stored.error == "temporary"


def test_task_worker_stats_include_registered_handlers_and_queue(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    queue.enqueue(task_type="echo", source="test")
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", concurrency=3)
    worker.register_handler("echo", lambda item: "ok")

    stats = worker.stats()

    assert stats["running"] is False
    assert stats["worker_id"] == "worker-1"
    assert stats["concurrency"] == 3
    assert stats["registered_task_types"] == ["echo"]
    assert stats["queue"]["pending"] == 1
