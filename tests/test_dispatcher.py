import asyncio

from agent_gateway.agents import AgentManager
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.models import AgentConfig, Binding, InboundMessage
from agent_gateway.router import BindingTable
from agent_gateway.runtime.dispatcher import GatewayDispatcher
from agent_gateway.runtime.lanes import CommandQueue


class FakeRunner:
    async def run_turn(self, agent_id: str, session_key: str, user_text: str, *, channel: str):
        from agent_gateway.models import AgentReply

        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=f"echo:{user_text}",
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
