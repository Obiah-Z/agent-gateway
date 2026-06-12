import asyncio
from pathlib import Path

from agent_gateway.channels.base import Channel, ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.models import InboundMessage, OutboundMessage
from agent_gateway.runtime.delivery_runtime import DeliveryRuntime


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
