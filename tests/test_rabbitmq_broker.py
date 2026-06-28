import json
from types import SimpleNamespace

from agent_gateway.runtime.infra import rabbitmq
from agent_gateway.runtime.infra.rabbitmq import RabbitMQDeliveryBroker, RabbitMQInboundTaskBroker
from agent_gateway.runtime.state.queue import QueuedDelivery
from agent_gateway.runtime.tasks.models import TaskInstance


class FakeChannel:
    def __init__(self) -> None:
        self.is_open = True
        self.exchanges: list[tuple[str, str, bool]] = []
        self.queues: list[tuple[str, bool, dict | None]] = []
        self.bindings: list[tuple[str, str, str]] = []
        self.published: list[dict] = []
        self.get_messages: list[tuple[object, object, bytes]] = []
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []
        self.queue_message_counts: dict[str, int] = {}
        self.qos: list[int] = []
        self.purged: list[str] = []

    def exchange_declare(self, *, exchange: str, exchange_type: str, durable: bool) -> None:
        self.exchanges.append((exchange, exchange_type, durable))

    def queue_declare(self, *, queue: str, durable: bool = True, arguments=None, passive: bool = False):
        self.queues.append((queue, durable, arguments))
        if passive:
            return SimpleNamespace(
                method=SimpleNamespace(
                    message_count=self.queue_message_counts.get(queue, 0),
                    consumer_count=0,
                )
            )
        return SimpleNamespace(method=SimpleNamespace(message_count=0, consumer_count=0))

    def queue_bind(self, *, exchange: str, queue: str, routing_key: str) -> None:
        self.bindings.append((exchange, queue, routing_key))

    def basic_qos(self, *, prefetch_count: int) -> None:
        self.qos.append(prefetch_count)

    def basic_publish(self, *, exchange: str, routing_key: str, body: bytes, properties, mandatory: bool) -> None:
        self.published.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": json.loads(body.decode("utf-8")),
                "properties": properties,
                "mandatory": mandatory,
            }
        )

    def basic_get(self, *, queue: str, auto_ack: bool):
        if not self.get_messages:
            return None, None, None
        return self.get_messages.pop(0)

    def basic_ack(self, *, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)

    def basic_nack(self, *, delivery_tag: int, requeue: bool) -> None:
        self.nacked.append((delivery_tag, requeue))

    def queue_purge(self, *, queue: str):
        self.purged.append(queue)
        count = self.queue_message_counts.get(queue, 0)
        self.queue_message_counts[queue] = 0
        return SimpleNamespace(method=SimpleNamespace(message_count=count))


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self.is_open = True
        self._channel = channel
        self.closed = False

    def channel(self) -> FakeChannel:
        return self._channel

    def close(self) -> None:
        self.closed = True
        self.is_open = False


class FakePika:
    class BasicProperties:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class URLParameters:
        def __init__(self, url: str) -> None:
            self.url = url
            self.socket_timeout = None
            self.blocked_connection_timeout = None

    def __init__(self) -> None:
        self.channel = FakeChannel()
        self.connections: list[FakeConnection] = []

    def BlockingConnection(self, parameters) -> FakeConnection:
        connection = FakeConnection(self.channel)
        self.connections.append(connection)
        return connection


def test_rabbitmq_delivery_broker_publishes_lightweight_reference(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        connect_timeout_seconds=0.5,
        enabled=True,
    )
    entry = QueuedDelivery(
        id="del-1",
        channel="feishu",
        to="ou_1",
        text="secret body must not be published",
        metadata={"account_id": "feishu-main", "correlation_id": "corr-1", "idempotency_key": "idem-1"},
    )

    broker.publish(entry)

    assert fake_pika.channel.exchanges == [
        ("gateway.delivery", "direct", True),
        ("gateway.delivery.dlx", "direct", True),
    ]
    assert fake_pika.channel.queues[0] == (
        "gateway.delivery.outbound",
        True,
        {"x-dead-letter-exchange": "gateway.delivery.dlx"},
    )
    published = fake_pika.channel.published[0]
    assert published["exchange"] == "gateway.delivery"
    assert published["routing_key"] == "gateway.delivery.outbound"
    assert published["body"]["delivery_id"] == "del-1"
    assert published["body"]["channel"] == "feishu"
    assert published["body"]["account_id"] == "feishu-main"
    assert published["body"]["idempotency_key"] == "idem-1"
    assert "text" not in published["body"]
    assert "secret body" not in json.dumps(published["body"])
    assert published["properties"].kwargs["delivery_mode"] == 2


def test_rabbitmq_delivery_broker_dead_letters_lightweight_reference(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        enabled=True,
    )
    entry = QueuedDelivery(
        id="del-2",
        channel="cli",
        to="peer-1",
        text="failed body must not be published",
        last_error="send failed",
    )

    broker.dead_letter(entry)

    published = fake_pika.channel.published[0]
    assert published["exchange"] == "gateway.delivery.dlx"
    assert published["routing_key"] == "gateway.delivery.dead"
    assert published["body"]["delivery_id"] == "del-2"
    assert published["body"]["last_error"] == "send failed"
    assert "text" not in published["body"]


def test_rabbitmq_delivery_broker_noops_when_disabled(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        enabled=False,
    )

    broker.publish(QueuedDelivery(id="del-3", channel="cli", to="peer", text="hello"))

    assert fake_pika.connections == []
    assert broker.stats()["backend"] == "rabbitmq"
    assert broker.stats()["enabled"] is False


def test_rabbitmq_delivery_broker_stats_include_queue_depth(monkeypatch) -> None:
    fake_pika = FakePika()
    fake_pika.channel.queue_message_counts = {
        "gateway.delivery.outbound": 3,
        "gateway.delivery.dead": 1,
    }
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        enabled=True,
    )

    stats = broker.stats()

    assert stats["messages"] == 3
    assert stats["dead_letter_messages"] == 1


def test_rabbitmq_delivery_broker_consumes_and_acks(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    fake_pika.channel.get_messages.append(
        (
            SimpleMethod(delivery_tag=7),
            None,
            json.dumps({"delivery_id": "del-4"}).encode("utf-8"),
        )
    )
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        enabled=True,
    )
    seen = []

    consumed = broker.consume_once(lambda payload: seen.append(payload["delivery_id"]) or True)

    assert consumed is True
    assert seen == ["del-4"]
    assert fake_pika.channel.acked == [7]
    assert fake_pika.channel.nacked == []


def test_rabbitmq_delivery_broker_nacks_when_handler_returns_false(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    fake_pika.channel.get_messages.append(
        (SimpleMethod(delivery_tag=8), None, json.dumps({"delivery_id": "del-5"}).encode("utf-8"))
    )
    broker = RabbitMQDeliveryBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.delivery",
        queue="gateway.delivery.outbound",
        dead_letter_exchange="gateway.delivery.dlx",
        dead_letter_queue="gateway.delivery.dead",
        enabled=True,
    )

    consumed = broker.consume_once(lambda _payload: False)

    assert consumed is True
    assert fake_pika.channel.acked == []
    assert fake_pika.channel.nacked == [(8, True)]


class SimpleMethod:
    def __init__(self, *, delivery_tag: int) -> None:
        self.delivery_tag = delivery_tag


def test_rabbitmq_inbound_task_broker_publishes_lightweight_partitioned_reference(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQInboundTaskBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.inbound",
        queue_prefix="gateway.inbound.partition",
        dead_letter_exchange="gateway.inbound.dlx",
        dead_letter_queue="gateway.inbound.dead",
        partitions=4,
        prefetch=1,
        enabled=True,
    )
    task = TaskInstance.create(
        task_type="agent_inbound",
        source="feishu",
        session_key="agent:feishu:user-1",
        idempotency_key="idem-1",
        payload={"text": "secret user body must not be published"},
    )

    broker.publish(task)

    partition = broker.partition_for(task.session_key)
    published = fake_pika.channel.published[0]
    assert fake_pika.channel.qos == [1]
    assert ("gateway.inbound", "direct", True) in fake_pika.channel.exchanges
    assert (
        f"gateway.inbound.partition.{partition}",
        True,
        {"x-dead-letter-exchange": "gateway.inbound.dlx"},
    ) in fake_pika.channel.queues
    assert published["exchange"] == "gateway.inbound"
    assert published["routing_key"] == f"gateway.inbound.partition.{partition}"
    assert published["body"]["task_id"] == task.id
    assert published["body"]["task_type"] == "agent_inbound"
    assert published["body"]["partition"] == partition
    assert "payload" not in published["body"]
    assert "secret user body" not in json.dumps(published["body"])
    assert published["properties"].kwargs["headers"]["session_key"] == task.session_key


def test_rabbitmq_inbound_task_broker_partitions_are_stable(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    broker = RabbitMQInboundTaskBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.inbound",
        queue_prefix="gateway.inbound.partition",
        dead_letter_exchange="gateway.inbound.dlx",
        dead_letter_queue="gateway.inbound.dead",
        partitions=8,
        enabled=True,
    )

    first = broker.partition_for("same-session")
    second = broker.partition_for("same-session")
    other = broker.partition_for("other-session")

    assert first == second
    assert 0 <= first < 8
    assert 0 <= other < 8
    assert broker.queue_name(first) == f"gateway.inbound.partition.{first}"


def test_rabbitmq_inbound_task_broker_consumes_and_acks_partition(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    fake_pika.channel.get_messages.append(
        (
            SimpleMethod(delivery_tag=17),
            None,
            json.dumps({"task_id": "task-1", "partition": 2}).encode("utf-8"),
        )
    )
    broker = RabbitMQInboundTaskBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.inbound",
        queue_prefix="gateway.inbound.partition",
        dead_letter_exchange="gateway.inbound.dlx",
        dead_letter_queue="gateway.inbound.dead",
        partitions=4,
        enabled=True,
    )
    seen = []

    consumed = broker.consume_once(2, lambda payload: seen.append(payload["task_id"]) or True)

    assert consumed is True
    assert seen == ["task-1"]
    assert fake_pika.channel.acked == [17]
    assert fake_pika.channel.nacked == []


def test_rabbitmq_inbound_task_broker_nacks_requeue_when_handler_returns_false(monkeypatch) -> None:
    fake_pika = FakePika()
    monkeypatch.setattr(rabbitmq, "pika", fake_pika)
    fake_pika.channel.get_messages.append(
        (
            SimpleMethod(delivery_tag=18),
            None,
            json.dumps({"task_id": "task-2", "partition": 1}).encode("utf-8"),
        )
    )
    broker = RabbitMQInboundTaskBroker(
        url="amqp://admin:admin123@127.0.0.1:5672/",
        exchange="gateway.inbound",
        queue_prefix="gateway.inbound.partition",
        dead_letter_exchange="gateway.inbound.dlx",
        dead_letter_queue="gateway.inbound.dead",
        partitions=4,
        enabled=True,
    )

    consumed = broker.consume_once(1, lambda _payload: False)

    assert consumed is True
    assert fake_pika.channel.acked == []
    assert fake_pika.channel.nacked == [(18, True)]
