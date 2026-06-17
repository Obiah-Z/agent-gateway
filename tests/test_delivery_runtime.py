import asyncio
from pathlib import Path

from agent_gateway.channels.base import Channel, ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue, DeliveryRunner, PermanentDeliveryError
from agent_gateway.models import InboundMessage, OutboundMessage
from agent_gateway.application.delivery_runtime import DeliveryRuntime


class DummyChannel(Channel):
    name = "cli"

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.sent: list[OutboundMessage] = []

    def receive(self) -> InboundMessage | None:
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        if self.fail_times > 0:
            self.fail_times -= 1
            return False
        self.sent.append(outbound)
        return True


def _build_runtime(tmp_path: Path, *, fail_times: int = 0, max_retries: int = 5):
    queue = DeliveryQueue(tmp_path / "queue")
    manager = ChannelManager()
    channel = DummyChannel(fail_times=fail_times)
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    runtime = DeliveryRuntime(queue, manager, max_retries=max_retries)
    return queue, channel, runtime


def test_delivery_runtime_flushes_queued_message(tmp_path: Path) -> None:
    queue, channel, runtime = _build_runtime(tmp_path)
    delivery_id = queue.enqueue(
        "cli",
        "peer-1",
        "hello from queue",
        {"account_id": "cli-local", "kind": "reply"},
    )

    asyncio.run(runtime.flush_once())

    assert [message.text for message in channel.sent] == ["hello from queue"]
    assert queue.pending_entries() == []
    assert delivery_id


def test_delivery_runtime_calls_success_hook_after_ack(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    manager = ChannelManager()
    channel = DummyChannel()
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    succeeded: list[str] = []
    runtime = DeliveryRuntime(
        queue,
        manager,
        on_success=lambda entry: succeeded.append(entry.id),
    )
    delivery_id = queue.enqueue(
        "cli",
        "peer-1",
        "hello from queue",
        {"account_id": "cli-local", "kind": "reply"},
    )

    asyncio.run(runtime.flush_once())

    assert succeeded == [delivery_id]
    assert queue.pending_entries() == []


def test_delivery_runtime_retries_failed_message(tmp_path: Path) -> None:
    queue, channel, runtime = _build_runtime(tmp_path, fail_times=1, max_retries=3)
    queue.enqueue(
        "cli",
        "peer-1",
        "retry me",
        {"account_id": "cli-local", "kind": "reply"},
    )

    asyncio.run(runtime.flush_once())
    pending = queue.pending_entries()

    assert channel.sent == []
    assert len(pending) == 1
    assert pending[0].retry_count == 1
    assert pending[0].last_error == "delivery failed"


def test_delivery_queue_can_retry_failed_and_discard_entries(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    pending_id = queue.enqueue(
        "cli",
        "peer-1",
        "pending message",
        {"account_id": "cli-local"},
    )
    failed_id = queue.enqueue(
        "cli",
        "peer-2",
        "failed message",
        {"account_id": "cli-local"},
    )
    failed = queue.get_pending(failed_id)
    assert failed is not None
    failed.retry_count = 5
    failed.last_error = "permanent failure"
    queue.move_to_failed(failed)

    assert len(queue.pending_entries()) == 1
    assert len(queue.failed_entries()) == 1

    assert queue.retry_now(failed_id) is True

    pending = {entry.id: entry for entry in queue.pending_entries()}
    assert pending[failed_id].retry_count == 0
    assert pending[failed_id].next_retry_at == 0.0
    assert queue.failed_entries() == []

    assert queue.discard(pending_id, state="pending") is True
    assert queue.get_pending(pending_id) is None


def test_delivery_runner_moves_permanent_failure_to_failed_without_retry(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    delivery_id = queue.enqueue(
        "feishu",
        "ou_missing_user",
        "bad target",
        {"account_id": "feishu-main"},
    )
    runner = DeliveryRunner(
        queue,
        lambda entry: (_ for _ in ()).throw(PermanentDeliveryError("invalid open_id")),
        max_retries=5,
    )

    runner.run_once()

    assert queue.pending_entries() == []
    failed = queue.failed_entries()
    assert len(failed) == 1
    assert failed[0].id == delivery_id
    assert failed[0].retry_count == 0
    assert failed[0].last_error == "invalid open_id"
