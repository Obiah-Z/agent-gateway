from agent_gateway.agents import AgentManager
from agent_gateway.models import AgentConfig, Binding, InboundMessage
from agent_gateway.router import BindingTable, build_session_key, resolve_route


def test_binding_priority_prefers_more_specific_match() -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    agents.register(AgentConfig(id="telegram-agent", name="Telegram"))
    agents.register(AgentConfig(id="admin-agent", name="Admin"))

    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    bindings.add(Binding(agent_id="telegram-agent", tier=4, match_key="channel", match_value="telegram"))
    bindings.add(
        Binding(
            agent_id="admin-agent",
            tier=1,
            match_key="peer_id",
            match_value="telegram:admin-001",
            priority=10,
        )
    )

    inbound = InboundMessage(
        text="hi",
        sender_id="admin-001",
        channel="telegram",
        peer_id="admin-001",
    )
    route = resolve_route(bindings, agents, inbound)

    assert route.agent_id == "admin-agent"
    assert route.matched_binding is not None
    assert route.matched_binding.tier == 1


def test_session_key_respects_per_channel_scope() -> None:
    key = build_session_key(
        agent_id="main",
        channel="telegram",
        account_id="bot-a",
        peer_id="u-1",
        dm_scope="per-channel-peer",
    )
    assert key == "agent:main:telegram:direct:u-1"
