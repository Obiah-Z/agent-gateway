from pathlib import Path

import pytest

from agent_gateway.runtime.tasks import LocalTaskStore, TaskInstance


def test_local_task_store_creates_and_reads_task(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    task = TaskInstance.create(
        task_type="cron",
        source="scheduler",
        agent_id="research",
        session_key="system:cron:daily",
        idempotency_key="cron:daily:1",
        payload={"job_id": "daily"},
    )

    store.create(task)
    restored = store.get(task.id)

    assert restored is not None
    assert restored.task_type == "cron"
    assert restored.status == "pending"
    assert restored.payload == {"job_id": "daily"}


def test_local_task_store_tracks_lifecycle(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create(TaskInstance.create(task_type="skill", source="feishu"))

    running = store.mark_running(task.id, now=100.0)
    done = store.mark_done(task.id, result_preview="finished" * 100, now=120.0)

    assert running.status == "running"
    assert running.started_at == 100.0
    assert done.status == "done"
    assert done.finished_at == 120.0
    assert len(done.result_preview) == 500


def test_local_task_store_marks_retrying_and_failed(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create(TaskInstance.create(task_type="github_analysis", source="feishu"))

    retrying = store.mark_failed(task.id, error="timeout", retryable=True, now=100.0)
    failed = store.mark_failed(task.id, error="fatal", retryable=False, now=200.0)

    assert retrying.status == "retrying"
    assert retrying.retry_count == 1
    assert failed.status == "failed"
    assert failed.retry_count == 1
    assert failed.finished_at == 200.0


def test_local_task_store_lists_recent_tasks_by_status(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    older = store.create(TaskInstance.create(task_type="cron", source="scheduler"))
    newer = store.create(TaskInstance.create(task_type="skill", source="feishu"))
    store.mark_done(older.id, now=100.0)
    store.mark_failed(newer.id, error="boom", now=200.0)

    failed = store.list(statuses=["failed"])
    all_rows = store.list()

    assert [task.id for task in failed] == [newer.id]
    assert [task.id for task in all_rows] == [newer.id, older.id]


def test_local_task_store_rejects_invalid_task_id(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")

    with pytest.raises(ValueError, match="invalid task id"):
        store.get("../bad")
