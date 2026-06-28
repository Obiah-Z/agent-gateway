"""RabbitMQ infrastructure for distributed reliable delivery and inbound tasks."""

from __future__ import annotations

import json
import hashlib
import time
from typing import Any

from agent_gateway.runtime.state.queue import QueuedDelivery
from agent_gateway.runtime.tasks.models import TaskInstance

try:  # pragma: no cover - optional dependency branch is covered by behavior tests.
    import pika
except Exception:  # pragma: no cover
    pika = None  # type: ignore[assignment]


class RabbitMQDeliveryBroker:
    """RabbitMQ-backed delivery broker.

    PostgreSQL remains the source of truth. RabbitMQ messages intentionally carry only
    lightweight references so the broker does not retain full outbound message bodies.
    """

    def __init__(
        self,
        *,
        url: str,
        exchange: str,
        queue: str,
        dead_letter_exchange: str,
        dead_letter_queue: str,
        connect_timeout_seconds: float = 2.0,
        enabled: bool = False,
    ) -> None:
        self.url = url
        self.exchange = exchange
        self.queue = queue
        self.dead_letter_exchange = dead_letter_exchange
        self.dead_letter_queue = dead_letter_queue
        self.connect_timeout_seconds = connect_timeout_seconds
        self.enabled = enabled
        self._connection: Any | None = None
        self._channel: Any | None = None

    def publish(self, entry: QueuedDelivery) -> None:
        """Publish a lightweight delivery reference to RabbitMQ."""

        if not self.enabled:
            return
        channel = self._ensure_channel()
        payload = {
            "delivery_id": entry.id,
            "channel": entry.channel,
            "account_id": str(entry.metadata.get("account_id", "")),
            "correlation_id": str(entry.metadata.get("correlation_id", "")),
            "idempotency_key": str(entry.metadata.get("idempotency_key", "")),
            "published_at": time.time(),
        }
        channel.basic_publish(
            exchange=self.exchange,
            routing_key=self.queue,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(  # type: ignore[union-attr]
                content_type="application/json",
                delivery_mode=2,
                message_id=entry.id,
                headers={
                    "delivery_id": entry.id,
                    "idempotency_key": str(entry.metadata.get("idempotency_key", "")),
                },
            ),
            mandatory=False,
        )

    def ack(self, delivery_id: str) -> None:
        """No-op for producer-side ack.

        Consumer message acknowledgements will be handled by the RabbitMQ worker in the
        next phase. This hook exists so DeliveryQueue state transitions stay stable.
        """

        return None

    def retry(self, entry: QueuedDelivery) -> None:
        """Retry scheduling is driven by PostgreSQL next_retry_at for now."""

        return None

    def dead_letter(self, entry: QueuedDelivery) -> None:
        """Publish a lightweight failed reference to the DLQ."""

        if not self.enabled:
            return
        channel = self._ensure_channel()
        payload = {
            "delivery_id": entry.id,
            "channel": entry.channel,
            "account_id": str(entry.metadata.get("account_id", "")),
            "idempotency_key": str(entry.metadata.get("idempotency_key", "")),
            "last_error": entry.last_error or "",
            "dead_lettered_at": time.time(),
        }
        channel.basic_publish(
            exchange=self.dead_letter_exchange,
            routing_key=self.dead_letter_queue,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(  # type: ignore[union-attr]
                content_type="application/json",
                delivery_mode=2,
                message_id=entry.id,
                headers={
                    "delivery_id": entry.id,
                    "idempotency_key": str(entry.metadata.get("idempotency_key", "")),
                },
            ),
            mandatory=False,
        )

    def discard(self, delivery_id: str) -> None:
        """RabbitMQ cannot remove already queued messages by id; consumers skip missing DB rows."""

        return None

    def stats(self) -> dict[str, Any]:
        """Return basic broker configuration and connection state."""

        stats = {
            "backend": "rabbitmq",
            "enabled": self.enabled,
            "exchange": self.exchange,
            "queue": self.queue,
            "dead_letter_exchange": self.dead_letter_exchange,
            "dead_letter_queue": self.dead_letter_queue,
            "connected": self._connection is not None and bool(getattr(self._connection, "is_open", False)),
        }
        if not self.enabled:
            return stats
        try:
            channel = self._ensure_channel()
            queue_state = channel.queue_declare(queue=self.queue, passive=True)
            dlq_state = channel.queue_declare(queue=self.dead_letter_queue, passive=True)
            stats["messages"] = int(queue_state.method.message_count)
            stats["consumers"] = int(queue_state.method.consumer_count)
            stats["dead_letter_messages"] = int(dlq_state.method.message_count)
            stats["dead_letter_consumers"] = int(dlq_state.method.consumer_count)
        except Exception as exc:
            stats["error"] = str(exc)
        return stats

    def consume_once(self, handler) -> bool:
        """Consume at most one RabbitMQ message.

        The handler receives the decoded lightweight reference and returns True when the
        message can be acknowledged. A False return keeps the message available for a
        later consumer.
        """

        if not self.enabled:
            return False
        channel = self._ensure_channel()
        method, _properties, body = channel.basic_get(queue=self.queue, auto_ack=False)
        if method is None:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return True
        try:
            should_ack = bool(handler(payload))
        except Exception:
            should_ack = False
        if should_ack:
            channel.basic_ack(delivery_tag=method.delivery_tag)
        else:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return True

    def purge(self) -> dict[str, int]:
        """Purge the configured main and dead-letter queues.

        This is intentionally not part of the production DeliveryBroker protocol.
        It is used by isolated load-test queues to avoid measuring stale broker
        messages from interrupted runs.
        """

        if not self.enabled:
            return {"messages": 0, "dead_letter_messages": 0}
        channel = self._ensure_channel()
        queue_result = channel.queue_purge(queue=self.queue)
        dlq_result = channel.queue_purge(queue=self.dead_letter_queue)
        return {
            "messages": int(getattr(queue_result.method, "message_count", 0)),
            "dead_letter_messages": int(getattr(dlq_result.method, "message_count", 0)),
        }

    def close(self) -> None:
        """Close the cached RabbitMQ connection."""

        connection = self._connection
        self._channel = None
        self._connection = None
        if connection is not None and getattr(connection, "is_open", False):
            connection.close()

    def _ensure_channel(self) -> Any:
        if pika is None:
            raise RuntimeError("RabbitMQ delivery broker requires pika")
        if self._channel is not None and getattr(self._channel, "is_open", False):
            return self._channel
        parameters = pika.URLParameters(self.url)
        parameters.socket_timeout = self.connect_timeout_seconds
        parameters.blocked_connection_timeout = self.connect_timeout_seconds
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        self._declare_topology(self._channel)
        return self._channel

    def _declare_topology(self, channel: Any) -> None:
        channel.exchange_declare(exchange=self.exchange, exchange_type="direct", durable=True)
        channel.exchange_declare(
            exchange=self.dead_letter_exchange,
            exchange_type="direct",
            durable=True,
        )
        channel.queue_declare(
            queue=self.queue,
            durable=True,
            arguments={"x-dead-letter-exchange": self.dead_letter_exchange},
        )
        channel.queue_bind(exchange=self.exchange, queue=self.queue, routing_key=self.queue)
        channel.queue_declare(queue=self.dead_letter_queue, durable=True)
        channel.queue_bind(
            exchange=self.dead_letter_exchange,
            queue=self.dead_letter_queue,
            routing_key=self.dead_letter_queue,
        )


class RabbitMQInboundTaskBroker:
    """RabbitMQ-backed inbound task broker.

    Task storage remains in PostgreSQL/local TaskStore. RabbitMQ carries only a
    lightweight task reference so it can distribute work without owning business
    state or retaining user message bodies.
    """

    def __init__(
        self,
        *,
        url: str,
        exchange: str,
        queue_prefix: str,
        dead_letter_exchange: str,
        dead_letter_queue: str,
        partitions: int = 8,
        prefetch: int = 1,
        connect_timeout_seconds: float = 2.0,
        enabled: bool = False,
    ) -> None:
        self.url = url
        self.exchange = exchange
        self.queue_prefix = queue_prefix
        self.dead_letter_exchange = dead_letter_exchange
        self.dead_letter_queue = dead_letter_queue
        self.partitions = max(1, partitions)
        self.prefetch = max(1, prefetch)
        self.connect_timeout_seconds = connect_timeout_seconds
        self.enabled = enabled
        self._connection: Any | None = None
        self._channel: Any | None = None

    def publish(self, task: TaskInstance) -> None:
        """Publish a lightweight inbound task reference to a session partition."""

        if not self.enabled:
            return
        channel = self._ensure_channel()
        partition = self.partition_for(task.session_key or task.id)
        queue = self.queue_name(partition)
        payload = {
            "task_id": task.id,
            "task_type": task.task_type,
            "session_key": task.session_key,
            "partition": partition,
            "idempotency_key": task.idempotency_key,
            "published_at": time.time(),
        }
        channel.basic_publish(
            exchange=self.exchange,
            routing_key=queue,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(  # type: ignore[union-attr]
                content_type="application/json",
                delivery_mode=2,
                message_id=task.id,
                headers={
                    "task_id": task.id,
                    "task_type": task.task_type,
                    "session_key": task.session_key,
                    "partition": partition,
                    "idempotency_key": task.idempotency_key,
                },
            ),
            mandatory=False,
        )

    def consume_once(self, partition: int, handler) -> bool:
        """Consume at most one message from one partition queue."""

        if not self.enabled:
            return False
        channel = self._ensure_channel()
        queue = self.queue_name(partition)
        method, _properties, body = channel.basic_get(queue=queue, auto_ack=False)
        if method is None:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return True
        try:
            should_ack = bool(handler(payload))
        except Exception:
            should_ack = False
        if should_ack:
            channel.basic_ack(delivery_tag=method.delivery_tag)
        else:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return True

    def stats(self) -> dict[str, Any]:
        """Return broker topology and per-partition queue depth."""

        stats: dict[str, Any] = {
            "backend": "rabbitmq",
            "enabled": self.enabled,
            "exchange": self.exchange,
            "queue_prefix": self.queue_prefix,
            "partitions": self.partitions,
            "prefetch": self.prefetch,
            "dead_letter_exchange": self.dead_letter_exchange,
            "dead_letter_queue": self.dead_letter_queue,
            "connected": self._connection is not None and bool(getattr(self._connection, "is_open", False)),
        }
        if not self.enabled:
            return stats
        queues: list[dict[str, int | str]] = []
        try:
            channel = self._ensure_channel()
            total_messages = 0
            total_consumers = 0
            for partition in range(self.partitions):
                queue = self.queue_name(partition)
                queue_state = channel.queue_declare(queue=queue, passive=True)
                messages = int(queue_state.method.message_count)
                consumers = int(queue_state.method.consumer_count)
                total_messages += messages
                total_consumers += consumers
                queues.append(
                    {
                        "partition": partition,
                        "queue": queue,
                        "messages": messages,
                        "consumers": consumers,
                    }
                )
            dlq_state = channel.queue_declare(queue=self.dead_letter_queue, passive=True)
            stats["messages"] = total_messages
            stats["consumers"] = total_consumers
            stats["queues"] = queues
            stats["dead_letter_messages"] = int(dlq_state.method.message_count)
            stats["dead_letter_consumers"] = int(dlq_state.method.consumer_count)
        except Exception as exc:
            stats["error"] = str(exc)
        return stats

    def purge(self) -> dict[str, int]:
        """Purge all inbound partition queues and the shared dead-letter queue."""

        if not self.enabled:
            return {"messages": 0, "dead_letter_messages": 0}
        channel = self._ensure_channel()
        purged = 0
        for partition in range(self.partitions):
            result = channel.queue_purge(queue=self.queue_name(partition))
            purged += int(getattr(result.method, "message_count", 0))
        dlq_result = channel.queue_purge(queue=self.dead_letter_queue)
        return {
            "messages": purged,
            "dead_letter_messages": int(getattr(dlq_result.method, "message_count", 0)),
        }

    def close(self) -> None:
        """Close the cached RabbitMQ connection."""

        connection = self._connection
        self._channel = None
        self._connection = None
        if connection is not None and getattr(connection, "is_open", False):
            connection.close()

    def queue_name(self, partition: int) -> str:
        """Return the queue name for a partition index."""

        normalized = int(partition) % self.partitions
        return f"{self.queue_prefix}.{normalized}"

    def partition_for(self, session_key: str) -> int:
        """Map a session key to a stable RabbitMQ partition."""

        raw = (session_key or "").encode("utf-8")
        digest = hashlib.sha256(raw).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False) % self.partitions

    def _ensure_channel(self) -> Any:
        if pika is None:
            raise RuntimeError("RabbitMQ inbound task broker requires pika")
        if self._channel is not None and getattr(self._channel, "is_open", False):
            return self._channel
        parameters = pika.URLParameters(self.url)
        parameters.socket_timeout = self.connect_timeout_seconds
        parameters.blocked_connection_timeout = self.connect_timeout_seconds
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        try:
            self._channel.basic_qos(prefetch_count=self.prefetch)
        except Exception:
            pass
        self._declare_topology(self._channel)
        return self._channel

    def _declare_topology(self, channel: Any) -> None:
        channel.exchange_declare(exchange=self.exchange, exchange_type="direct", durable=True)
        channel.exchange_declare(
            exchange=self.dead_letter_exchange,
            exchange_type="direct",
            durable=True,
        )
        for partition in range(self.partitions):
            queue = self.queue_name(partition)
            channel.queue_declare(
                queue=queue,
                durable=True,
                arguments={"x-dead-letter-exchange": self.dead_letter_exchange},
            )
            channel.queue_bind(exchange=self.exchange, queue=queue, routing_key=queue)
        channel.queue_declare(queue=self.dead_letter_queue, durable=True)
        channel.queue_bind(
            exchange=self.dead_letter_exchange,
            queue=self.dead_letter_queue,
            routing_key=self.dead_letter_queue,
        )
