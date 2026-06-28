import asyncio
from pathlib import Path

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.state.queue import DeliveryQueue, DeliveryRunner, PermanentDeliveryError
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.observability.events import RuntimeEventStore


class RecordingDeliveryBackend:
    """测试用内存 delivery_entries backend。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []
        self.fail_writes = False

    def list(self, table: str, *, limit: int = 50, filters=None):
        assert table == "delivery_entries"
        filters = filters or {}
        state = filters.get("state")
        rows = [
            row
            for row in self.rows.values()
            if not state or row.get("state") == state
        ]
        return rows[:limit]

    def get(self, table: str, key: str):
        assert table == "delivery_entries"
        return self.rows.get(key)

    def write_delivery_entry(self, entry, *, state: str = "pending"):
        if self.fail_writes:
            raise RuntimeError("db unavailable")
        row = entry.to_dict()
        row["state"] = state
        row["updated_at"] = 1.0
        self.rows[entry.id] = row
        return row

    def delete_delivery_entry(self, delivery_id: str) -> bool:
        self.deleted.append(delivery_id)
        return self.rows.pop(delivery_id, None) is not None

    def reserve_delivery(self, *, worker_id: str, now=None, delivery_id: str = ""):
        candidates = [
            row
            for row in self.rows.values()
            if row.get("state") in {"pending", "retrying"}
            and (not delivery_id or row.get("id") == delivery_id)
            and float(row.get("next_retry_at", 0.0) or 0.0) <= float(now or 0.0)
        ]
        candidates.sort(key=lambda row: float(row.get("enqueued_at", 0.0) or 0.0))
        if not candidates:
            return None
        row = candidates[0]
        row["state"] = "running"
        row["locked_by"] = worker_id
        row["locked_at"] = float(now or 0.0)
        return dict(row)


class RecordingDeliveryBroker:
    """测试用 broker，记录 DeliveryQueue 与分发层的边界调用。"""

    def __init__(self) -> None:
        self.published: list[str] = []
        self.acked: list[str] = []
        self.retried: list[str] = []
        self.dead_lettered: list[str] = []
        self.discarded: list[str] = []
        self.fail_publish = False
        self.messages: list[dict] = []
        self.consumed: list[str] = []
        self.fail_consume = False

    def publish(self, entry) -> None:
        if self.fail_publish:
            raise RuntimeError("broker unavailable")
        self.published.append(entry.id)

    def ack(self, delivery_id: str) -> None:
        self.acked.append(delivery_id)

    def retry(self, entry) -> None:
        self.retried.append(entry.id)

    def dead_letter(self, entry) -> None:
        self.dead_lettered.append(entry.id)

    def discard(self, delivery_id: str) -> None:
        self.discarded.append(delivery_id)

    def stats(self):
        return {"backend": "recording", "enabled": True, "published": len(self.published)}

    def consume_once(self, handler):
        if self.fail_consume:
            raise RuntimeError("broker down")
        if not self.messages:
            return False
        payload = self.messages.pop(0)
        if handler(payload):
            self.consumed.append(str(payload.get("delivery_id", "")))
        return True


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


def test_delivery_runtime_consumes_broker_message_by_delivery_id(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    broker = RecordingDeliveryBroker()
    queue.broker = broker
    manager = ChannelManager()
    channel = DummyChannel()
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    runtime = DeliveryRuntime(queue, manager)
    delivery_id = queue.enqueue(
        "cli",
        "peer-1",
        "hello from broker",
        {"account_id": "cli-local", "kind": "reply"},
    )
    broker.messages.append({"delivery_id": delivery_id})

    asyncio.run(runtime.flush_once())

    assert broker.consumed == [delivery_id]
    assert [message.text for message in channel.sent] == ["hello from broker"]
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
    retrying = queue.retrying_entries()

    assert channel.sent == []
    assert queue.pending_entries() == []
    assert len(retrying) == 1
    assert retrying[0].retry_count == 1
    assert retrying[0].last_error == "delivery failed"


def test_delivery_runtime_records_broker_failure_and_falls_back_to_polling(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    queue = DeliveryQueue(tmp_path / "queue")
    broker = RecordingDeliveryBroker()
    broker.fail_consume = True
    queue.broker = broker
    manager = ChannelManager()
    channel = DummyChannel()
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    runtime = DeliveryRuntime(queue, manager, event_store=store)
    queue.enqueue("cli", "peer-1", "fallback send", {"account_id": "cli-local"})

    asyncio.run(runtime.flush_once())

    assert [message.text for message in channel.sent] == ["fallback send"]
    events = store.tail(limit=5)
    assert events[0]["type"] == "delivery.broker.failed"
    assert events[0]["status"] == "warning"


def test_delivery_runners_do_not_duplicate_reserved_database_entries(tmp_path: Path) -> None:
    backend = RecordingDeliveryBackend()
    queue = DeliveryQueue(tmp_path / "queue")
    queue.read_backend = backend
    queue.write_backend = backend
    manager = ChannelManager()
    channel = DummyChannel()
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    runtime_a = DeliveryRuntime(queue, manager)
    runtime_b = DeliveryRuntime(queue, manager)
    queue.enqueue("cli", "peer-1", "send once", {"account_id": "cli-local"})

    asyncio.run(runtime_a.flush_once())
    asyncio.run(runtime_b.flush_once())

    assert [message.text for message in channel.sent] == ["send once"]


def test_delivery_runtime_records_success_and_failure_events(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    queue, _channel, runtime = _build_runtime(tmp_path / "success")
    runtime.event_store = store
    queue.enqueue(
        "cli",
        "peer-1",
        "hello",
        {
            "account_id": "cli-local",
            "kind": "reply",
            "agent_id": "main",
            "correlation_id": "corr-delivery-ok",
        },
    )

    asyncio.run(runtime.flush_once())

    failing_queue, _failing_channel, failing_runtime = _build_runtime(
        tmp_path / "failure",
        fail_times=1,
        max_retries=3,
    )
    failing_runtime.event_store = store
    failing_queue.enqueue(
        "cli",
        "peer-2",
        "retry me",
        {
            "account_id": "cli-local",
            "kind": "reply",
            "agent_id": "main",
            "correlation_id": "corr-delivery-failed",
        },
    )

    asyncio.run(failing_runtime.flush_once())

    events = store.tail(limit=10)
    assert [event["type"] for event in events] == ["delivery.sent", "delivery.failed"]
    assert events[0]["agent_id"] == "main"
    assert events[0]["correlation_id"] == "corr-delivery-ok"
    assert events[1]["correlation_id"] == "corr-delivery-failed"
    assert events[1]["status"] == "failed"


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


def test_delivery_queue_deduplicates_by_idempotency_key(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")

    first_id = queue.enqueue(
        "cli",
        "peer-1",
        "same message",
        {"account_id": "cli-local", "idempotency_key": "idem-1"},
    )
    second_id = queue.enqueue(
        "cli",
        "peer-1",
        "same message",
        {"account_id": "cli-local", "idempotency_key": "idem-1"},
    )

    assert second_id == first_id
    assert len(queue.pending_entries()) == 1
    assert queue.pending_entries()[0].metadata["idempotency_key"] == "idem-1"


def test_delivery_queue_allows_forced_duplicate_delivery(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")

    first_id = queue.enqueue("cli", "peer-1", "same message", {"idempotency_key": "idem-2"})
    second_id = queue.enqueue(
        "cli",
        "peer-1",
        "same message",
        {"idempotency_key": "idem-2", "force_delivery": True},
    )

    assert second_id != first_id
    assert len(queue.pending_entries()) == 2


def test_delivery_queue_derives_stable_idempotency_key(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")

    first_id = queue.enqueue("cli", "peer-1", "same message", {"kind": "reply"})
    second_id = queue.enqueue("cli", "peer-1", "same message", {"kind": "reply"})

    assert second_id == first_id
    assert queue.pending_entries()[0].metadata["idempotency_key"]


def test_delivery_queue_uses_database_backend_for_state_transitions(tmp_path: Path) -> None:
    backend = RecordingDeliveryBackend()
    queue = DeliveryQueue(tmp_path / "queue")
    queue.read_backend = backend
    queue.write_backend = backend

    delivery_id = queue.enqueue("cli", "peer-1", "hello", {"account_id": "cli-local"})

    assert backend.rows[delivery_id]["state"] == "pending"
    assert [entry.id for entry in queue.pending_entries()] == [delivery_id]

    pending = queue.get_pending(delivery_id)
    assert pending is not None
    queue.move_to_failed(pending)
    assert queue.pending_entries() == []
    assert [entry.id for entry in queue.failed_entries()] == [delivery_id]
    assert backend.rows[delivery_id]["state"] == "failed"

    assert queue.retry_now(delivery_id) is True
    assert backend.rows[delivery_id]["state"] == "pending"
    assert backend.rows[delivery_id]["retry_count"] == 0

    queue.ack(delivery_id)
    assert delivery_id in backend.deleted
    assert queue.pending_entries() == []


def test_delivery_queue_uses_retrying_state_for_retryable_failures(tmp_path: Path) -> None:
    backend = RecordingDeliveryBackend()
    queue = DeliveryQueue(tmp_path / "queue")
    queue.read_backend = backend
    queue.write_backend = backend

    delivery_id = queue.enqueue("cli", "peer-1", "hello", {"account_id": "cli-local"})
    pending = queue.get_pending(delivery_id)
    assert pending is not None

    queue.fail(pending, "temporary failure")

    assert backend.rows[delivery_id]["state"] == "retrying"
    assert [entry.id for entry in queue.retrying_entries()] == [delivery_id]
    assert queue.pending_entries() == []


def test_delivery_queue_notifies_broker_for_state_transitions(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    broker = RecordingDeliveryBroker()
    queue.broker = broker

    delivery_id = queue.enqueue("cli", "peer-1", "hello", {"account_id": "cli-local"})
    assert broker.published == [delivery_id]
    assert queue.broker_stats()["backend"] == "recording"

    pending = queue.get_pending(delivery_id)
    assert pending is not None
    queue.fail(pending, "temporary failure")
    assert broker.retried == [delivery_id]

    retrying = queue.get_retrying(delivery_id)
    assert retrying is not None
    queue.move_to_failed(retrying)
    assert broker.dead_lettered == [delivery_id]

    assert queue.retry_now(delivery_id) is True
    assert broker.published == [delivery_id, delivery_id]

    queue.ack(delivery_id)
    assert broker.acked == [delivery_id]

    second_id = queue.enqueue("cli", "peer-2", "discard me", {})
    assert queue.discard(second_id, state="pending") is True
    assert broker.discarded == [second_id]


def test_delivery_queue_publishes_due_retries(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    broker = RecordingDeliveryBroker()
    queue.broker = broker
    delivery_id = queue.enqueue("cli", "peer-1", "retry later", {})
    pending = queue.get_pending(delivery_id)
    assert pending is not None

    queue.fail(pending, "temporary failure")
    retrying = queue.get_retrying(delivery_id)
    assert retrying is not None
    retrying.next_retry_at = 1.0
    queue._write_primary(retrying, state="retrying")
    queue._write_entry(retrying)

    published = queue.publish_due_retries(now=2.0)

    assert published == 1
    assert broker.published == [delivery_id, delivery_id]
    assert queue.get_pending(delivery_id) is not None
    assert queue.get_retrying(delivery_id) is None


def test_delivery_queue_keeps_state_when_broker_publish_fails(tmp_path: Path) -> None:
    queue = DeliveryQueue(tmp_path / "queue")
    broker = RecordingDeliveryBroker()
    broker.fail_publish = True
    queue.broker = broker

    delivery_id = queue.enqueue("cli", "peer-1", "hello", {})

    assert broker.published == []
    assert queue.get_pending(delivery_id) is not None


def test_delivery_queue_falls_back_to_local_files_when_backend_write_fails(tmp_path: Path) -> None:
    backend = RecordingDeliveryBackend()
    backend.fail_writes = True
    queue = DeliveryQueue(tmp_path / "queue")
    queue.read_backend = None
    queue.write_backend = backend

    delivery_id = queue.enqueue("cli", "peer-1", "hello", {})

    assert backend.rows == {}
    assert queue.get_pending(delivery_id) is not None


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
