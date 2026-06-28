from __future__ import annotations

import asyncio

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.state.queue import DeliveryQueue, DeliveryRunner, QueuedDelivery
from agent_gateway.runtime.domain.models import OutboundMessage
from agent_gateway.runtime.observability.events import RuntimeEventStore


class DeliveryRuntime:
    """可靠投递后台运行时。

    周期性扫描 `DeliveryQueue`，把可发送消息交给对应通道，并把结果写入事件流。
    """

    def __init__(
        self,
        queue: DeliveryQueue,
        channels: ChannelManager,
        *,
        poll_interval: float = 1.0,
        max_retries: int = 5,
        on_success=None,
        event_store: RuntimeEventStore | None = None,
    ) -> None:
        self.queue = queue
        self.channels = channels
        self.poll_interval = poll_interval
        self.event_store = event_store
        self.runner = DeliveryRunner(
            queue,
            self._deliver_entry,
            max_retries=max_retries,
            on_success=self._on_success,
        )
        self.on_success = on_success
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        """启动后台投递轮询。"""

        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="delivery-runtime")

    async def stop(self) -> None:
        """停止后台投递轮询。"""

        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def flush_once(self) -> None:
        """手动执行一轮投递扫描。"""

        consumed = await asyncio.to_thread(self._flush_broker_once)
        if not consumed:
            await asyncio.to_thread(self.queue.publish_due_retries)
            consumed = await asyncio.to_thread(self._flush_broker_once)
        if not consumed:
            await asyncio.to_thread(self.runner.run_once)

    def pending_count(self) -> int:
        """返回当前 pending 消息数量。"""

        return len(self.queue.pending_entries())

    def _flush_broker_once(self) -> bool:
        """优先消费一条 broker 消息；未启用 broker 时返回 False。"""

        consume_once = getattr(self.queue.broker, "consume_once", None)
        if consume_once is None:
            return False

        def handle(payload: dict) -> bool:
            delivery_id = str(payload.get("delivery_id", ""))
            if not delivery_id:
                return True
            entry = self.queue.reserve(
                worker_id=self.runner.worker_id,
                delivery_id=delivery_id,
            )
            if entry is None:
                return True
            self.runner.run_entry(entry)
            return True

        try:
            return bool(consume_once(handle))
        except Exception as exc:
            self._record_broker_event(
                "delivery.broker.failed",
                status="warning",
                message="Delivery broker consume failed; falling back to polling",
                error=exc,
            )
            return False

    def _record_broker_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        error: str | Exception = "",
    ) -> None:
        """记录 broker 层事件。"""

        if self.event_store is None:
            return
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="delivery",
                message=message,
                error=error,
                metadata={"broker": self.queue.broker_stats()},
            )
        except Exception:
            pass

    async def _loop(self) -> None:
        """后台循环，按固定间隔推进投递。"""

        while not self._stopped:
            try:
                await self.flush_once()
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval)

    def _deliver_entry(self, entry: QueuedDelivery) -> bool:
        """把单条队列消息交给具体通道发送。"""

        channel = self.channels.get(entry.channel, str(entry.metadata.get("account_id", "")))
        if channel is None:
            error = f"channel unavailable: {entry.channel}/{entry.metadata.get('account_id', '')}"
            self._record_delivery_event(
                "delivery.failed",
                entry,
                status="failed",
                message="Delivery channel unavailable",
                error=error,
            )
            raise RuntimeError(error)
        outbound = OutboundMessage(
            channel=entry.channel,
            to=entry.to,
            text=entry.text,
            metadata=dict(entry.metadata),
        )
        try:
            success = channel.send(outbound)
        except Exception as exc:
            self._record_delivery_event(
                "delivery.failed",
                entry,
                status="failed",
                message="Delivery send raised an exception",
                error=exc,
            )
            raise
        if not success:
            self._record_delivery_event(
                "delivery.failed",
                entry,
                status="failed",
                message="Delivery send returned false",
                error="channel send returned false",
            )
        return success

    def _on_success(self, entry: QueuedDelivery) -> None:
        """处理投递成功后的事件记录和回调。"""

        self._record_delivery_event(
            "delivery.sent",
            entry,
            status="ok",
            message="Delivery sent",
        )
        if self.on_success is None:
            return
        self.on_success(entry)

    def _record_delivery_event(
        self,
        event_type: str,
        entry: QueuedDelivery,
        *,
        status: str,
        message: str,
        error: str | Exception = "",
    ) -> None:
        """把投递链路事件写入 runtime event store。"""

        if self.event_store is None:
            return
        metadata = dict(entry.metadata)
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="delivery",
                message=message,
                correlation_id=str(metadata.get("correlation_id", "")),
                agent_id=str(metadata.get("agent_id", "")),
                session_key=str(metadata.get("session_key", "")),
                channel=entry.channel,
                account_id=str(metadata.get("account_id", "")),
                peer_id=entry.to,
                delivery_id=entry.id,
                error=error,
                metadata={
                    "kind": metadata.get("kind", ""),
                    "retry_count": entry.retry_count,
                    "text_length": len(entry.text),
                },
            )
        except Exception:
            pass
