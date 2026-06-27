from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.runtime.domain.models import AgentConfig, Binding, InboundMessage, RouteResolution
from agent_gateway.runtime.domain.router import (
    BindingTable,
    build_inbound_lane_key,
    build_preroute_lane_key,
    build_session_key,
    resolve_route,
)


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


def test_preroute_lane_key_uses_channel_account_and_peer() -> None:
    inbound = InboundMessage(
        text="hi",
        sender_id="sender-1",
        channel="Feishu",
        account_id="Bot-A",
        peer_id="Chat-1",
    )

    assert build_preroute_lane_key(inbound) == "inbound:feishu:bot-a:chat-1"


def test_preroute_lane_key_falls_back_to_sender_and_defaults() -> None:
    inbound = InboundMessage(text="hi", sender_id="Sender-1")

    assert build_preroute_lane_key(inbound) == "inbound:unknown:default:sender-1"


def test_inbound_lane_key_prefers_routed_agent_session() -> None:
    inbound = InboundMessage(
        text="hi",
        sender_id="sender-1",
        channel="feishu",
        account_id="bot-a",
        peer_id="chat-1",
    )
    route = RouteResolution(agent_id="Research", session_key="agent:research:direct:chat-1")

    assert (
        build_inbound_lane_key(inbound, route)
        == "agent:research:session:agent:research:direct:chat-1"
    )


def test_inbound_lane_key_uses_preroute_key_without_route() -> None:
    inbound = InboundMessage(
        text="hi",
        sender_id="sender-1",
        channel="telegram",
        account_id="bot-a",
        peer_id="user-1",
    )

    assert build_inbound_lane_key(inbound) == "inbound:telegram:bot-a:user-1"
