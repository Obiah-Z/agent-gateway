from __future__ import annotations

from pathlib import Path

from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.execution.metrics_runtime import MetricsRuntime
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore


class FakeTaskWorker:
    def stats(self) -> dict[str, object]:
        return {
            "queue": {
                "pending": 3,
                "running": 1,
                "retrying": 2,
                "failed": 1,
            },
            "broker": {
                "enabled": True,
                "messages": 9,
                "dead_letter_messages": 1,
                "partitions": 4,
                "prefetch": 1,
                "queues": [
                    {"partition": 0, "messages": 2},
                    {"partition": 1, "messages": 7},
                ],
            },
        }


class FakeCron:
    def list_jobs(self) -> list[dict[str, object]]:
        return [
            {"id": "ok", "enabled": True, "errors": 0},
            {"id": "bad", "enabled": True, "errors": 2},
            {"id": "off", "enabled": False, "errors": 0},
        ]


class FakeAutonomy:
    def __init__(self) -> None:
        self.cron = FakeCron()


def test_metrics_runtime_collects_runtime_snapshot(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "delivery")
    queue.enqueue("cli", "peer-1", "pending", {"account_id": "cli-local"})
    failed_id = queue.enqueue("cli", "peer-2", "failed", {"account_id": "cli-local"})
    failed = queue.get_pending(failed_id)
    assert failed is not None
    failed.retry_count = 5
    failed.last_error = "permanent"
    queue.move_to_failed(failed)

    command_queue = CommandQueue()
    command_queue.lane("active-lane").enqueue(lambda: "done")
    profiles = ProfileManager(
        [
            AuthProfile(name="primary", provider="anthropic", api_key="k"),
            AuthProfile(
                name="cooldown",
                provider="anthropic",
                api_key="k",
                cooldown_until=9_999_999_999.0,
            ),
        ]
    )
    event_store = RuntimeEventStore(tmp_path / "events")
    event_store.record(
        "delivery.failed",
        status="failed",
        component="delivery",
        message="failed",
        error="channel unavailable",
    )
    event_store.record(
        "feishu.event.rejected",
        status="rejected",
        component="feishu",
        message="rejected",
        error="method not allowed",
    )
    metrics = MetricsStore(tmp_path / "metrics")
    runtime = MetricsRuntime(
        metrics_store=metrics,
        delivery_queue=queue,
        command_queue=command_queue,
        profiles=profiles,
        autonomy=FakeAutonomy(),  # type: ignore[arg-type]
        event_store=event_store,
        task_worker=FakeTaskWorker(),
        interval_seconds=60,
    )

    row = runtime.snapshot_once()

    assert row["delivery"]["pending"] == 1
    assert row["delivery"]["failed"] == 1
    assert row["cron"]["count"] == 3
    assert row["cron"]["enabled"] == 2
    assert row["cron"]["errored"] == 1
    assert row["profiles"]["count"] == 2
    assert row["profiles"]["available"] == 1
    assert row["profiles"]["cooling_down"] == 1
    assert row["events"]["delivery_failed_5m"] == 1
    assert row["events"]["rejected_5m"] == 1
    assert row["tasks"]["pending"] == 3
    assert row["tasks"]["running"] == 1
    assert row["tasks"]["broker_enabled"] is True
    assert row["tasks"]["broker_messages"] == 9
    assert row["tasks"]["broker_dead_letter_messages"] == 1
    assert row["tasks"]["broker_partitions"] == 4
    assert row["tasks"]["broker_max_partition_messages"] == 7
    assert metrics.latest() == row


def test_metrics_runtime_handles_optional_event_and_autonomy_sources(tmp_path: Path) -> None:
    runtime = MetricsRuntime(
        metrics_store=MetricsStore(tmp_path / "metrics"),
        delivery_queue=DeliveryQueue(tmp_path / "delivery"),
        command_queue=CommandQueue(),
        profiles=ProfileManager([]),
        autonomy=None,
        event_store=None,
    )

    row = runtime.snapshot_once()

    assert row["cron"] == {"configured": False}
    assert row["events"] == {"configured": False}
    assert row["tasks"] == {"configured": False}
    assert row["delivery"]["pending"] == 0
