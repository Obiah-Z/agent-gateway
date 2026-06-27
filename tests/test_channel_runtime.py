import asyncio
from threading import Event

from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import (
    AgentReply,
    Binding,
    InboundMessage,
    OutboundMessage,
    RouteResolution,
)
from agent_gateway.runtime.execution.channel_runtime import ChannelRuntime, PendingInbound


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
    name = "dummy"

    def receive(self) -> InboundMessage | None:
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        del outbound
        return True

    def close(self) -> None:
        pass


class BlockingChannel(FakeChannel):
    def __init__(self) -> None:
        self.closed = Event()
        self.receive_entered = Event()

    def receive_batch(self) -> list[InboundMessage]:
        self.receive_entered.set()
        self.closed.wait(timeout=2.0)
        return []

    def close(self) -> None:
        self.closed.set()


class SlowDispatcher(FakeDispatcher):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.dispatched: list[str] = []

    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        self.started.set()
        await self.release.wait()
        self.dispatched.append(inbound.text)
        return await super().dispatch_inbound(inbound, forced_agent_id=forced_agent_id)


class LaneAwareDispatcher(FakeDispatcher):
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.release_by_peer: dict[str, asyncio.Event] = {}

    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        self.started.append(inbound.peer_id)
        release = self.release_by_peer.get(inbound.peer_id)
        if release is not None:
            await release.wait()
        self.finished.append(inbound.peer_id)
        return await super().dispatch_inbound(inbound, forced_agent_id=forced_agent_id)


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


def test_pending_inbound_exposes_preroute_lane_key() -> None:
    pending = PendingInbound(
        message=InboundMessage(
            text="hi",
            sender_id="user-1",
            channel="feishu",
            account_id="bot-a",
            peer_id="chat-1",
        )
    )

    assert pending.preroute_lane_key == "inbound:feishu:bot-a:chat-1"


def test_channel_runtime_restart_drains_queued_messages_before_swap() -> None:
    first = ChannelManager()
    second = ChannelManager()
    dispatcher = SlowDispatcher()
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=first,
        delivery_runtime=FakeDeliveryRuntime(),
    )

    async def _run() -> None:
        await runtime.start()
        await runtime.ingest_external(
            InboundMessage(
                text="queued before restart",
                sender_id="cli-user",
                channel="cli",
                account_id="cli-local",
                peer_id="cli-user",
            )
        )
        await dispatcher.started.wait()
        restart_task = asyncio.create_task(runtime.restart(second))
        await asyncio.sleep(0)
        assert not restart_task.done()
        dispatcher.release.set()
        await restart_task

    asyncio.run(_run())

    assert dispatcher.dispatched == ["queued before restart"]
    assert runtime.channels is second


def test_channel_runtime_restart_releases_cli_completion_event() -> None:
    dispatcher = SlowDispatcher()
    first = ChannelManager()
    second = ChannelManager()
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=first,
        delivery_runtime=FakeDeliveryRuntime(),
    )

    async def _run() -> None:
        await runtime.start()
        event = Event()
        await runtime._queue.put(
            PendingInbound(
                message=InboundMessage(
                    text="cli before restart",
                    sender_id="cli-user",
                    channel="cli",
                    account_id="cli-local",
                    peer_id="cli-user",
                ),
                completion_event=event,
            )
        )
        await dispatcher.started.wait()
        restart_task = asyncio.create_task(runtime.restart(second))
        await asyncio.sleep(0)
        assert not event.is_set()
        dispatcher.release.set()
        await restart_task
        assert event.is_set()

    asyncio.run(_run())

    assert dispatcher.dispatched == ["cli before restart"]


def test_channel_runtime_restart_closes_and_joins_old_channel_threads() -> None:
    first = ChannelManager()
    blocking_channel = BlockingChannel()
    first.register(blocking_channel, ChannelAccount(channel="dummy", account_id="old"))
    second = ChannelManager()
    runtime = ChannelRuntime(
        dispatcher=FakeDispatcher(),
        channels=first,
        delivery_runtime=FakeDeliveryRuntime(),
        shutdown_timeout_seconds=1.0,
    )

    async def _run() -> None:
        await runtime.start()
        assert blocking_channel.receive_entered.wait(timeout=1.0)
        old_threads = list(runtime._threads)
        await runtime.restart(second)
        assert blocking_channel.closed.is_set()
        assert all(not thread.is_alive() for thread in old_threads)

    asyncio.run(_run())

    assert runtime.channels is second


def test_channel_runtime_processes_different_preroute_lanes_concurrently() -> None:
    dispatcher = LaneAwareDispatcher()
    slow_release = asyncio.Event()
    dispatcher.release_by_peer["slow"] = slow_release
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=ChannelManager(),
        delivery_runtime=FakeDeliveryRuntime(),
    )

    async def _run() -> None:
        await runtime.start()
        await runtime.ingest_external(
            InboundMessage(
                text="slow",
                sender_id="slow",
                channel="feishu",
                account_id="bot-a",
                peer_id="slow",
            )
        )
        await runtime.ingest_external(
            InboundMessage(
                text="fast",
                sender_id="fast",
                channel="feishu",
                account_id="bot-a",
                peer_id="fast",
            )
        )
        while "fast" not in dispatcher.finished:
            await asyncio.sleep(0)
        assert "slow" in dispatcher.started
        assert "slow" not in dispatcher.finished
        slow_release.set()
        await runtime.stop()

    asyncio.run(_run())

    assert dispatcher.finished == ["fast", "slow"]


def test_channel_runtime_keeps_same_preroute_lane_serial() -> None:
    dispatcher = LaneAwareDispatcher()
    first_release = asyncio.Event()
    dispatcher.release_by_peer["same"] = first_release
    runtime = ChannelRuntime(
        dispatcher=dispatcher,
        channels=ChannelManager(),
        delivery_runtime=FakeDeliveryRuntime(),
    )

    async def _run() -> None:
        await runtime.start()
        await runtime.ingest_external(
            InboundMessage(
                text="first",
                sender_id="same",
                channel="feishu",
                account_id="bot-a",
                peer_id="same",
            )
        )
        await runtime.ingest_external(
            InboundMessage(
                text="second",
                sender_id="same",
                channel="feishu",
                account_id="bot-a",
                peer_id="same",
            )
        )
        while dispatcher.started != ["same"]:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert dispatcher.finished == []
        dispatcher.release_by_peer.pop("same").set()
        while dispatcher.finished != ["same", "same"]:
            await asyncio.sleep(0)
        await runtime.stop()

    asyncio.run(_run())

    assert dispatcher.started == ["same", "same"]


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
