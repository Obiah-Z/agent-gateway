"""通道运行时。

负责把 CLI / Telegram / Feishu 等通道采集到的入站消息统一放进异步队列，再交给
dispatcher 顺序处理。CLI 的 completion_event 设计用于避免提示符抢跑。
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Protocol

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.messaging.telegram import TelegramChannel
from agent_gateway.runtime.domain.models import InboundMessage, ProactiveTarget
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher


@dataclass(slots=True)
class PendingInbound:
    """从具体通道进入网关主队列的待处理消息。"""

    message: InboundMessage
    completion_event: threading.Event | None = None


class InboundInterceptor(Protocol):
    async def try_consume_activation(self, inbound: InboundMessage) -> bool:
        ...


class ChannelRuntime:
    """协调多通道采集、顺序消费和错误回退。"""

    def __init__(
        self,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        delivery_runtime: DeliveryRuntime | None = None,
        inbound_interceptors: list[InboundInterceptor] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.channels = channels
        self.delivery_runtime = delivery_runtime
        self.inbound_interceptors = list(inbound_interceptors or [])
        self._queue: asyncio.Queue[PendingInbound | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._consumer_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """启动消费队列和每个已配置通道的采集线程。"""

        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        self._consumer_task = asyncio.create_task(self._consume())
        for account, channel in self.channels.iter_channels():
            thread = threading.Thread(
                target=self._worker_loop,
                args=(account.channel, account.account_id, channel),
                daemon=True,
                name=f"channel-{account.channel}-{account.account_id}",
            )
            thread.start()
            self._threads.append(thread)

    async def stop(self) -> None:
        """停止所有通道线程并结束消费循环。"""

        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        self.channels.close_all()
        await self._queue.put(None)
        if self._consumer_task is not None:
            await self._consumer_task

    async def wait_closed(self) -> None:
        if self._consumer_task is not None:
            await self._consumer_task

    async def ingest_external(self, inbound: InboundMessage) -> None:
        await self._queue.put(PendingInbound(message=inbound))

    async def restart(self, channels: ChannelManager) -> None:
        """在控制面 reload 通道配置后热重启通道运行时。"""

        was_running = self._running
        if was_running:
            await self.stop()
        self.channels = channels
        if self.delivery_runtime is not None:
            self.delivery_runtime.channels = channels
        self._threads = []
        if was_running:
            await self.start()

    def _worker_loop(self, channel_name: str, account_id: str, channel: Any) -> None:
        """在独立线程里轮询具体通道，避免阻塞主事件循环。"""

        while not self._stop_event.is_set():
            try:
                batch = channel.receive_batch()
            except Exception:
                time.sleep(1.0)
                continue

            if not batch:
                time.sleep(0.1)
                continue

            for inbound in batch:
                if self._loop is None or self._stop_event.is_set():
                    return
                completion_event = threading.Event() if channel_name == "cli" else None
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._queue.put(
                            PendingInbound(
                                message=inbound,
                                completion_event=completion_event,
                            )
                        ),
                        self._loop,
                    ).result()
                except Exception:
                    return
                if completion_event is not None:
                    while not self._stop_event.is_set():
                        if completion_event.wait(timeout=0.1):
                            break

    async def _consume(self) -> None:
        """消费统一入站队列，并保证异常会转成用户可见的错误回复。"""

        while True:
            pending = await self._queue.get()
            if pending is None:
                self._queue.task_done()
                break

            inbound = pending.message
            try:
                await self._handle_inbound(inbound)
            except Exception as exc:
                print(
                    "[channel_runtime] inbound processing failed:"
                    f" channel={inbound.channel}"
                    f" account={inbound.account_id}"
                    f" sender={inbound.sender_id}"
                    f" peer={inbound.peer_id}"
                    f" error={exc}"
                )
                traceback.print_exc()
                await self._deliver_error_reply(inbound, exc)
            finally:
                if pending.completion_event is not None:
                    pending.completion_event.set()
                self._queue.task_done()

    async def _handle_inbound(self, inbound: InboundMessage) -> None:
        """处理单条入站消息：typing -> onboarding interceptor -> dispatch -> delivery。"""

        print(
            "[channel_runtime] inbound dequeued:"
            f" channel={inbound.channel}"
            f" account={inbound.account_id}"
            f" sender={inbound.sender_id}"
            f" peer={inbound.peer_id}"
        )
        await self._send_typing_if_supported(inbound)
        for interceptor in self.inbound_interceptors:
            if await interceptor.try_consume_activation(inbound):
                await self._flush_cli_delivery_if_needed(inbound)
                return
        result = await self.dispatcher.dispatch_inbound(inbound)
        await self.dispatcher.deliver_reply(self.channels, result)
        await self._flush_cli_delivery_if_needed(inbound)

    async def _deliver_error_reply(self, inbound: InboundMessage, exc: Exception) -> None:
        """把处理异常转换成一条出站错误提示，避免用户无反馈。"""

        try:
            metadata = dict(inbound.metadata)
            metadata.update(
                {
                    "kind": "error",
                    "sender_id": inbound.sender_id,
                    "error_type": type(exc).__name__,
                }
            )
            await self.dispatcher.deliver_text(
                self.channels,
                ProactiveTarget(
                    channel=inbound.channel,
                    account_id=inbound.account_id,
                    peer_id=inbound.peer_id,
                    agent_id=str(inbound.metadata.get("agent_id", "main")),
                ),
                "本轮消息处理失败，网关已记录错误。请稍后重试，或检查模型/API 配置。",
                metadata=metadata,
            )
            await self._flush_cli_delivery_if_needed(inbound)
        except Exception as delivery_exc:
            print(
                "[channel_runtime] failed to enqueue error reply:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" peer={inbound.peer_id}"
                f" error={delivery_exc}"
            )
            traceback.print_exc()

    async def _flush_cli_delivery_if_needed(self, inbound: InboundMessage) -> None:
        """CLI 通道需要在打印提示后立即刷新投递，避免交互体验错位。"""

        if inbound.channel == "cli" and self.delivery_runtime is not None:
            await self.delivery_runtime.flush_once()

    async def _send_typing_if_supported(self, inbound: InboundMessage) -> None:
        """仅对支持 typing 的通道发送打字状态。"""

        if inbound.channel != "telegram":
            return
        channel = self.channels.get("telegram", inbound.account_id)
        if not isinstance(channel, TelegramChannel):
            return
        chat_id = inbound.peer_id.split(":topic:")[0]
        await asyncio.to_thread(channel.send_typing, chat_id)
