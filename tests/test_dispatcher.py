import asyncio

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.domain.models import (
    AgentConfig,
    AgentHandoffRequest,
    Binding,
    InboundMessage,
)
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.observability.events import RuntimeEventStore


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    async def run_turn(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        *,
        channel: str,
        correlation_id: str = "",
    ):
        self.calls.append((agent_id, session_key, user_text))
        self.correlation_id = correlation_id
        from agent_gateway.runtime.domain.models import AgentReply

        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=f"echo:{user_text}",
            stop_reason="end_turn",
            tool_calls=[],
        )


class HandoffRunner(FakeRunner):
    async def run_turn(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        *,
        channel: str,
        correlation_id: str = "",
    ):
        self.calls.append((agent_id, session_key, user_text))
        from agent_gateway.runtime.domain.models import AgentReply

        if agent_id == "personal-secretary-zhanghaibo":
            return AgentReply(
                agent_id=agent_id,
                session_key=session_key,
                text="正在转交饮食助手处理。",
                stop_reason="end_turn",
                tool_calls=["request_agent_handoff"],
                handoff_request=AgentHandoffRequest(
                    target_agent_id="diet-assistant-zhanghaibo",
                    handoff_prompt="请记录早餐：鸡蛋和牛奶。",
                    reason="饮食记录应由饮食助手处理。",
                    scope="one-shot",
                    source_agent_id=agent_id,
                    user_goal="记录早餐",
                ),
            )
        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=f"diet-result:{user_text}",
            stop_reason="end_turn",
            tool_calls=[],
        )


def test_dispatcher_routes_executes_and_enqueues_reply(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    dispatcher = GatewayDispatcher(agents, bindings, FakeRunner(), CommandQueue(), queue)

    inbound = InboundMessage(
        text="hello",
        sender_id="u1",
        channel="cli",
        account_id="cli-local",
        peer_id="u1",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))
    delivery_id = asyncio.run(dispatcher.deliver_reply(ChannelManager(), result))
    queued = queue.pending_entries()

    assert result.reply.text == "echo:hello"
    assert result.route.agent_id == "main"
    assert len(queued) == 1
    assert queued[0].id == delivery_id
    assert queued[0].channel == "cli"
    assert queued[0].to == "u1"
    assert queued[0].text == "echo:hello"
    assert queued[0].metadata["account_id"] == "cli-local"
    assert queued[0].metadata["session_key"] == result.reply.session_key


def test_dispatcher_executes_one_shot_agent_handoff(tmp_path) -> None:
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            dm_scope="per-account-channel-peer",
        )
    )
    agents.register(
        AgentConfig(
            id="diet-assistant-zhanghaibo",
            name="Diet",
            dm_scope="per-account-channel-peer",
        )
    )
    bindings = BindingTable()
    bindings.add(
        Binding(
            agent_id="personal-secretary-zhanghaibo",
            tier=1,
            match_key="peer_id",
            match_value="zhanghaibo",
            priority=100,
        )
    )
    queue = DeliveryQueue(tmp_path / "delivery")
    runner = HandoffRunner()
    dispatcher = GatewayDispatcher(agents, bindings, runner, CommandQueue(), queue)
    inbound = InboundMessage(
        text="帮我切换到饮食 Agent，记录早餐",
        sender_id="zhanghaibo",
        channel="wework",
        account_id="wework-main",
        peer_id="zhanghaibo",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))

    assert result.route.agent_id == "diet-assistant-zhanghaibo"
    assert result.reply.agent_id == "diet-assistant-zhanghaibo"
    assert result.reply.text == "diet-result:请记录早餐：鸡蛋和牛奶。"
    assert [call[0] for call in runner.calls] == [
        "personal-secretary-zhanghaibo",
        "diet-assistant-zhanghaibo",
    ]
    assert runner.calls[1][1].startswith(
        "agent:diet-assistant-zhanghaibo:wework:wework-main:direct:zhanghaibo"
    )


def test_dispatcher_propagates_correlation_id_to_events_and_delivery(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    runner = FakeRunner()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        CommandQueue(),
        queue,
        event_store=store,
    )
    inbound = InboundMessage(
        text="hello",
        sender_id="u1",
        channel="cli",
        account_id="cli-local",
        peer_id="u1",
        metadata={"correlation_id": "corr-test-1"},
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))
    asyncio.run(dispatcher.deliver_reply(ChannelManager(), result))

    events = store.tail(limit=10)
    queued = queue.pending_entries()

    assert runner.correlation_id == "corr-test-1"
    assert {event["correlation_id"] for event in events} == {"corr-test-1"}
    assert queued[0].metadata["correlation_id"] == "corr-test-1"
