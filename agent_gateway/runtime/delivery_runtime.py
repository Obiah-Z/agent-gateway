from __future__ import annotations

import asyncio

from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue, DeliveryRunner, QueuedDelivery
from agent_gateway.models import OutboundMessage


class DeliveryRuntime:
    def __init__(
        self,
        queue: DeliveryQueue,
        channels: ChannelManager,
        *,
        poll_interval: float = 1.0,
        max_retries: int = 5,
        on_success=None,
    ) -> None:
        self.queue = queue
        self.channels = channels
        self.poll_interval = poll_interval
        self.runner = DeliveryRunner(
            queue,
            self._deliver_entry,
            max_retries=max_retries,
            on_success=on_success,
        )
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
            raise RuntimeError(
                f"channel unavailable: {entry.channel}/{entry.metadata.get('account_id', '')}"
            )
        outbound = OutboundMessage(
            channel=entry.channel,
            to=entry.to,
            text=entry.text,
            metadata=dict(entry.metadata),
        )
        return channel.send(outbound)
