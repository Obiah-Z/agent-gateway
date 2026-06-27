from pathlib import Path

from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore
from agent_gateway.runtime.tasks.models import TaskInstance


def test_local_task_queue_enqueues_and_reserves_by_priority(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    slow = queue.enqueue(task_type="cron", source="scheduler", priority=100)
    fast = queue.enqueue(task_type="skill", source="feishu", priority=10)

    reserved = queue.reserve(worker_id="worker-1", now=100.0)

    assert reserved is not None
    assert reserved.id == fast.id
    assert reserved.status == "running"
    assert reserved.metadata["worker_id"] == "worker-1"
    assert queue.store.get(slow.id).status == "pending"


def test_local_task_queue_filters_reserve_by_task_type(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    queue.enqueue(task_type="cron", source="scheduler", priority=10)
    skill = queue.enqueue(task_type="skill", source="feishu", priority=100)

    reserved = queue.reserve(worker_id="worker-1", task_types=["skill"])

    assert reserved is not None
    assert reserved.id == skill.id
    assert reserved.task_type == "skill"


def test_local_task_queue_prefers_primary_atomic_reserve(tmp_path: Path) -> None:
    class FakeWriteBackend:
        def __init__(self, row: dict) -> None:
            self.row = row
            self.calls: list[dict] = []

        def reserve_task(self, *, worker_id: str, task_types: list[str], now: float | None = None):
            self.calls.append({"worker_id": worker_id, "task_types": task_types, "now": now})
            return self.row

    task = TaskInstance.create(task_type="cron", source="scheduler", priority=10)
    task.status = "running"
    task.started_at = 100.0
    task.updated_at = 100.0
    task.metadata = {"worker_id": "worker-db"}
    backend = FakeWriteBackend(task.to_dict())
    store = LocalTaskStore(tmp_path / "tasks")
    store.write_backend = backend
    queue = LocalTaskQueue(store)

    reserved = queue.reserve(worker_id="worker-db", task_types=["cron"], now=100.0)

    assert reserved is not None
    assert reserved.id == task.id
    assert reserved.status == "running"
    assert reserved.metadata["worker_id"] == "worker-db"
    assert backend.calls == [{"worker_id": "worker-db", "task_types": ["cron"], "now": 100.0}]
    assert store.get(task.id).status == "running"


def test_local_task_queue_falls_back_when_primary_reserve_fails(tmp_path: Path) -> None:
    class FailingWriteBackend:
        def reserve_task(self, *, worker_id: str, task_types: list[str], now: float | None = None):
            raise RuntimeError("database unavailable")

    store = LocalTaskStore(tmp_path / "tasks")
    store.write_backend = FailingWriteBackend()
    queue = LocalTaskQueue(store)
    task = queue.enqueue(task_type="cron", source="scheduler", priority=10)

    reserved = queue.reserve(worker_id="worker-local", now=200.0)

    assert reserved is not None
    assert reserved.id == task.id
    assert reserved.status == "running"
    assert reserved.metadata["worker_id"] == "worker-local"


def test_local_task_queue_ack_retry_fail_and_cancel(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    done = queue.enqueue(task_type="cron", source="scheduler")
    retry = queue.enqueue(task_type="skill", source="feishu")
    failed = queue.enqueue(task_type="github_analysis", source="feishu")
    cancelled = queue.enqueue(task_type="space_advisor", source="ops")

    queue.ack(done.id, result_preview="ok", now=100.0)
    queue.retry(retry.id, error="timeout", now=110.0)
    queue.fail(failed.id, error="fatal", now=120.0)
    queue.cancel(cancelled.id, now=130.0)

    assert queue.store.get(done.id).status == "done"
    assert queue.store.get(done.id).result_preview == "ok"
    assert queue.store.get(retry.id).status == "retrying"
    assert queue.store.get(retry.id).retry_count == 1
    assert queue.store.get(failed.id).status == "failed"
    assert queue.store.get(cancelled.id).status == "cancelled"


def test_local_task_queue_stats_counts_statuses(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    pending = queue.enqueue(task_type="cron", source="scheduler")
    done = queue.enqueue(task_type="skill", source="feishu")
    queue.ack(done.id)

    stats = queue.stats()

    assert stats["pending"] == 1
    assert stats["done"] == 1
    assert queue.store.get(pending.id).status == "pending"
