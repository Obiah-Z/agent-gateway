"""消息路由和会话键生成。

路由层只关心“这条消息应该交给哪个 Agent、写入哪个会话”，不执行模型调用，也不触碰
具体通道发送逻辑。
"""

from __future__ import annotations

from .agents import AgentManager
from .ids import DEFAULT_AGENT_ID, normalize_agent_id
from .models import Binding, InboundMessage, RouteResolution

__all__ = [
    "BindingTable",
    "build_inbound_lane_key",
    "build_preroute_lane_key",
    "build_session_key",
    "resolve_route",
]


class BindingTable:
    """按优先级维护路由绑定表。

    tier 越小匹配越具体：peer > guild > account > channel > default。
    """

    def __init__(self) -> None:
        self._bindings: list[Binding] = []

    def add(self, binding: Binding) -> None:
        self._bindings.append(binding)
        # tier 升序保证更具体的绑定先匹配，priority 降序用于同层级内手工调权。
        self._bindings.sort(key=lambda item: (item.tier, -item.priority))

    def remove(self, agent_id: str, match_key: str, match_value: str) -> bool:
        """删除一条精确匹配的绑定规则。"""

        before = len(self._bindings)
        self._bindings = [
            binding
            for binding in self._bindings
            if not (
                binding.agent_id == agent_id
                and binding.match_key == match_key
                and binding.match_value == match_value
            )
        ]
        return len(self._bindings) < before

    def list_all(self) -> list[Binding]:
        """返回当前绑定表快照。"""

        return list(self._bindings)

    def replace_all(self, bindings: list[Binding]) -> None:
        """整体替换绑定表，常用于控制面 reload。"""

        self._bindings.clear()
        for binding in bindings:
            self.add(binding)

    def resolve(
        self,
        channel: str = "",
        account_id: str = "",
        guild_id: str = "",
        peer_id: str = "",
    ) -> tuple[str | None, Binding | None]:
        """返回第一个命中的 Agent ID 和绑定规则。"""

        for binding in self._bindings:
            if binding.tier == 1 and binding.match_key == "peer_id":
                if ":" in binding.match_value:
                    if binding.match_value == f"{channel}:{peer_id}":
                        return binding.agent_id, binding
                elif binding.match_value == peer_id:
                    return binding.agent_id, binding
            elif (
                binding.tier == 2
                and binding.match_key == "guild_id"
                and binding.match_value == guild_id
            ):
                return binding.agent_id, binding
            elif (
                binding.tier == 3
                and binding.match_key == "account_id"
                and binding.match_value == account_id
            ):
                return binding.agent_id, binding
            elif (
                binding.tier == 4
                and binding.match_key == "channel"
                and binding.match_value == channel
            ):
                return binding.agent_id, binding
            elif binding.tier == 5 and binding.match_key == "default":
                return binding.agent_id, binding
        return None, None


def build_session_key(
    agent_id: str,
    channel: str = "",
    account_id: str = "",
    peer_id: str = "",
    dm_scope: str = "per-peer",
) -> str:
    """根据 Agent 的 dm_scope 生成会话隔离键。

    不同 dm_scope 控制同一个用户在不同通道、账号下是否共享上下文。
    """

    aid = normalize_agent_id(agent_id)
    normalized_channel = (channel or "unknown").strip().lower()
    normalized_account = (account_id or "default").strip().lower()
    normalized_peer = (peer_id or "").strip().lower()
    if dm_scope == "per-account-channel-peer" and normalized_peer:
        return f"agent:{aid}:{normalized_channel}:{normalized_account}:direct:{normalized_peer}"
    if dm_scope == "per-channel-peer" and normalized_peer:
        return f"agent:{aid}:{normalized_channel}:direct:{normalized_peer}"
    if dm_scope == "per-peer" and normalized_peer:
        return f"agent:{aid}:direct:{normalized_peer}"
    return f"agent:{aid}:main"


def build_preroute_lane_key(inbound: InboundMessage) -> str:
    """生成路由前入站 lane key。

    ChannelRuntime 在正式路由前还不知道目标 Agent 和最终 session_key，因此只能使用
    通道维度的稳定身份做临时分组。这个 key 用于后续 lane dispatcher 的粗分发阶段。
    """

    channel = (inbound.channel or "unknown").strip().lower()
    account_id = (inbound.account_id or "default").strip().lower()
    peer_id = (inbound.peer_id or inbound.sender_id or "anonymous").strip().lower()
    return f"inbound:{channel}:{account_id}:{peer_id}"


def build_inbound_lane_key(
    inbound: InboundMessage,
    route: RouteResolution | None = None,
) -> str:
    """生成入站处理 lane key。

    已完成路由时优先使用 `agent_id + session_key`，保证同一 Agent 会话严格串行。
    路由前或拦截器消息则退回到通道/account/peer 维度，避免不同来源互相阻塞。
    """

    if route is not None and route.agent_id and route.session_key:
        agent_id = normalize_agent_id(route.agent_id)
        return f"agent:{agent_id}:session:{route.session_key}"
    return build_preroute_lane_key(inbound)


def resolve_route(
    bindings: BindingTable,
    agents: AgentManager,
    inbound: InboundMessage,
    forced_agent_id: str = "",
) -> RouteResolution:
    """把入站消息解析成可执行的 Agent 路由结果。"""

    if forced_agent_id:
        agent_id = normalize_agent_id(forced_agent_id)
        matched = None
    else:
        agent_id, matched = bindings.resolve(
            channel=inbound.channel,
            account_id=inbound.account_id,
            guild_id=inbound.guild_id,
            peer_id=inbound.peer_id,
        )
        agent_id = normalize_agent_id(agent_id or DEFAULT_AGENT_ID)

    agent = agents.get(agent_id)
    dm_scope = agent.dm_scope if agent else "per-peer"
    session_key = build_session_key(
        agent_id=agent_id,
        channel=inbound.channel,
        account_id=inbound.account_id,
        peer_id=inbound.peer_id,
        dm_scope=dm_scope,
    )
    return RouteResolution(
        agent_id=agent_id,
        session_key=session_key,
        matched_binding=matched,
    )
