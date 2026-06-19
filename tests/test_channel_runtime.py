import asyncio
from threading import Event

from agent_gateway.channels.manager import ChannelManager
from agent_gateway.core.models import AgentReply, Binding, InboundMessage, RouteResolution
from agent_gateway.application.channel_runtime import ChannelRuntime, PendingInbound


class FakeDispatcher:
    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        del forced_agent_id
        return type(
            "DispatchResult",
            (),
            {
                "inbound": inbound,
                "route": RouteResolution(agent_id="main", session_key="main:cli-user"),
                "reply": AgentReply(
                    agent_id="main",
                    session_key="main:cli-user",
                    text="pong",
                    stop_reason="end_turn",
                    tool_calls=[],
                ),
            },
        )()

    async def deliver_reply(self, channels: ChannelManager, result) -> str:
        del channels, result
        return "delivery-1"


class FakeDeliveryRuntime:
    def __init__(self) -> None:
        self.flush_calls = 0
        self.channels = None

    async def flush_once(self) -> None:
        self.flush_calls += 1


class FakeChannel:
    def close(self) -> None:
        pass


class ConsumingInterceptor:
    def __init__(self) -> None:
        self.calls = 0

    async def try_consume_activation(self, inbound: InboundMessage) -> bool:
        self.calls += 1
        return True


class FlakyDispatcher(FakeDispatcher):
    def __init__(self) -> None:
        self.dispatch_calls = 0
        self.delivered_replies = 0
        self.delivered_errors: list[str] = []
        self.delivered_error_metadata: list[dict] = []

    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        self.dispatch_calls += 1
        if self.dispatch_calls == 1:
            raise RuntimeError("model unavailable")
        return await super().dispatch_inbound(inbound, forced_agent_id=forced_agent_id)

    async def deliver_reply(self, channels: ChannelManager, result) -> str:
        self.delivered_replies += 1
        return await super().deliver_reply(channels, result)

    async def deliver_text(self, channels: ChannelManager, target, text: str, *, metadata=None) -> str:
        del channels, target
        self.delivered_errors.append(text)
        self.delivered_error_metadata.append(dict(metadata or {}))
        return "error-delivery-1"


def test_channel_runtime_restart_swaps_channels() -> None:
    first = ChannelManager()
    second = ChannelManager()
    first.accounts = []
    second.accounts = []
    runtime = ChannelRuntime(
        dispatcher=FakeDispatcher(),
        channels=first,
        delivery_runtime=FakeDeliveryRuntime(),
    )

    asyncio.run(runtime.restart(second))

    assert runtime.channels is second
    assert runtime.delivery_runtime is not None
    assert runtime.delivery_runtime.channels is second


def test_channel_runtime_flushes_cli_delivery_before_next_prompt() -> None:
    runtime = ChannelRuntime(
        dispatcher=FakeDispatcher(),
        channels=ChannelManager(),
        delivery_runtime=FakeDeliveryRuntime(),
    )

    async def _run() -> None:
        runtime._consumer_task = asyncio.create_task(runtime._consume())
        event = Event()
        await runtime._queue.put(
            PendingInbound(
                message=InboundMessage(
                    text="hello",
                    sender_id="cli-user",
                    channel="cli",
                    account_id="cli-local",
                    peer_id="cli-user",
                ),
                completion_event=event,
            )
        )
        await runtime._queue.put(None)
        await runtime._consumer_task
        assert event.is_set()
        assert runtime.delivery_runtime is not None
        assert runtime.delivery_runtime.flush_calls == 1

    asyncio.run(_run())


def test_channel_runtime_continues_after_inbound_failure() -> None:
    dispatcher = FlakyDispatcher()
    delivery_runtime = FakeDeliveryRuntime()
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=ChannelManager(),
        delivery_runtime=delivery_runtime,
    )

    async def _run() -> None:
        runtime._consumer_task = asyncio.create_task(runtime._consume())
        first = Event()
        second = Event()
        await runtime._queue.put(
            PendingInbound(
                message=InboundMessage(
                    text="fail once",
                    sender_id="cli-user",
                    channel="cli",
                    account_id="cli-local",
                    peer_id="cli-user",
                    metadata={"receive_id_type": "open_id"},
                ),
                completion_event=first,
            )
        )
        await runtime._queue.put(
            PendingInbound(
                message=InboundMessage(
                    text="recover",
                    sender_id="cli-user",
                    channel="cli",
                    account_id="cli-local",
                    peer_id="cli-user",
                ),
                completion_event=second,
            )
        )
        await runtime._queue.put(None)
        await runtime._consumer_task

        assert first.is_set()
        assert second.is_set()
        assert dispatcher.dispatch_calls == 2
        assert dispatcher.delivered_replies == 1
        assert dispatcher.delivered_errors == [
            "本轮消息处理失败，网关已记录错误。请稍后重试，或检查模型/API 配置。"
        ]
        assert dispatcher.delivered_error_metadata[0]["receive_id_type"] == "open_id"
        assert dispatcher.delivered_error_metadata[0]["kind"] == "error"
        assert dispatcher.delivered_error_metadata[0]["error_type"] == "RuntimeError"
        assert delivery_runtime.flush_calls == 2

    asyncio.run(_run())


def test_channel_runtime_interceptor_can_consume_inbound_before_dispatch() -> None:
    dispatcher = FlakyDispatcher()
    interceptor = ConsumingInterceptor()
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=ChannelManager(),
        delivery_runtime=FakeDeliveryRuntime(),
        inbound_interceptors=[interceptor],
    )

    async def _run() -> None:
        runtime._consumer_task = asyncio.create_task(runtime._consume())
        event = Event()
        await runtime._queue.put(
            PendingInbound(
                message=InboundMessage(
                    text="绑定 GATEWAY-ABC123",
                    sender_id="ou_user",
                    channel="feishu",
                    account_id="feishu-long-local",
                    peer_id="ou_user",
                ),
                completion_event=event,
            )
        )
        await runtime._queue.put(None)
        await runtime._consumer_task
        assert event.is_set()

    asyncio.run(_run())

    assert interceptor.calls == 1
    assert dispatcher.dispatch_calls == 0
