from __future__ import annotations

import asyncio

from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue, DeliveryRunner, QueuedDelivery
from agent_gateway.core.models import OutboundMessage
from agent_gateway.observability.events import RuntimeEventStore


class DeliveryRuntime:
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
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="delivery-runtime")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def flush_once(self) -> None:
        await asyncio.to_thread(self.runner.run_once)

    def pending_count(self) -> int:
        return len(self.queue.pending_entries())

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.flush_once()
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval)

    def _deliver_entry(self, entry: QueuedDelivery) -> bool:
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
