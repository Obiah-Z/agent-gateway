"""网关领域模型。

这里的对象不依赖具体通道、模型 SDK 或存储实现，是各层之间传递数据的稳定契约。
后续新增通道或 Agent 能力时，优先复用这些模型，避免把外部平台字段泄漏到应用层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ConversationMessage = dict[str, Any]

__all__ = [
    "AgentConfig",
    "AgentReply",
    "Binding",
    "ConversationMessage",
    "DispatchResult",
    "InboundMessage",
    "OutboundMessage",
    "ProactiveTarget",
    "RouteResolution",
]


@dataclass(slots=True)
class InboundMessage:
    """统一入站消息。

    不同入口（CLI、飞书、Telegram、WebSocket）会先被适配成这个结构，再进入路由和
    Agent 执行链路。`raw` 保留原始事件，`metadata` 保存网关内部需要透传的附加信息。
    """

    text: str
    sender_id: str
    channel: str = ""
    account_id: str = ""
    peer_id: str = ""
    guild_id: str = ""
    is_group: bool = False
    media: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutboundMessage:
    """统一出站消息。

    dispatcher 只负责把回复写入可靠投递队列，真正发送时再由通道适配器消费这个结构。
    """

    channel: str
    to: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Binding:
    """消息路由规则。

    `tier` 表示匹配粒度，数值越小优先级越高；同一层级内再按 `priority` 降序排序。
    """

    agent_id: str
    tier: int
    match_key: str
    match_value: str
    priority: int = 0

    def display(self) -> str:
        names = {1: "peer", 2: "guild", 3: "account", 4: "channel", 5: "default"}
        label = names.get(self.tier, f"tier-{self.tier}")
        return (
            f"[{label}] {self.match_key}={self.match_value} "
            f"-> agent:{self.agent_id} (pri={self.priority})"
        )


@dataclass(slots=True)
class RouteResolution:
    """一次路由决策的结果，包含目标 Agent 和对应会话键。"""

    agent_id: str
    session_key: str
    matched_binding: Binding | None = None


@dataclass(slots=True)
class AgentConfig:
    """Agent 运行配置。

    该配置由 `workspace/agents/*/agent.yaml` 或旧版兼容配置加载，也可由控制面动态创建。
    它只描述 Agent 的能力、prompt 策略、记忆策略和工具策略，不保存任何会话状态。
    """

    id: str
    name: str
    personality: str = ""
    model: str = ""
    dm_scope: str = "per-peer"
    extra_system: str = ""
    tool_policy_mode: str = "all"
    tool_names: tuple[str, ...] = ()
    memory_enabled: bool = True
    memory_auto_recall: bool = True
    memory_top_k: int = 3
    prompt_dir: str = ""
    use_global_prompt_files: bool = True
    skills_enabled: bool = True

    def effective_model(self, default_model: str) -> str:
        """返回该 Agent 实际使用的模型，未单独配置时继承全局默认模型。"""

        return self.model or default_model

    def resolved_prompt_dir(self) -> str:
        """返回该 Agent 的局部 prompt 目录，默认位于 `workspace/agents/<id>`。"""

        return (self.prompt_dir or f"agents/{self.id}").strip("/")

    def allowed_tool_names(self, available_tool_names: list[str]) -> list[str]:
        """根据工具策略从已注册工具中筛选本 Agent 可见的工具。"""

        configured = {name for name in self.tool_names if name}
        if self.tool_policy_mode == "allowlist":
            return [name for name in available_tool_names if name in configured]
        if self.tool_policy_mode == "denylist":
            return [name for name in available_tool_names if name not in configured]
        return list(available_tool_names)

    def uses_memory(self, mode: str) -> bool:
        return mode == "full" and self.memory_enabled

    def should_auto_recall_memory(self) -> bool:
        return self.memory_enabled and self.memory_auto_recall

    def manifest_row(self) -> dict[str, Any]:
        """转换成可写回 `config/agents.json` 的 manifest 行。"""

        return {
            "id": self.id,
            "name": self.name,
            "personality": self.personality,
            "model": self.model,
            "dm_scope": self.dm_scope,
            "extra_system": self.extra_system,
            "tool_policy": {
                "mode": self.tool_policy_mode,
                "tool_names": list(self.tool_names),
            },
            "memory_policy": {
                "enabled": self.memory_enabled,
                "auto_recall": self.memory_auto_recall,
                "top_k": self.memory_top_k,
            },
            "prompt_policy": {
                "prompt_dir": self.prompt_dir,
                "use_global_files": self.use_global_prompt_files,
                "skills_enabled": self.skills_enabled,
            },
        }

    def basic_system_prompt(self) -> str:
        """构造最低限度的系统提示词，作为 workspace prompt 缺失时的兜底。"""

        parts = [f"You are {self.name}."]
        if self.personality:
            parts.append(f"Your personality: {self.personality}")
        if self.extra_system:
            parts.append(self.extra_system)
        parts.append("Answer helpfully, use tools when necessary, and stay consistent.")
        return " ".join(parts)


@dataclass(slots=True)
class AgentReply:
    """Agent 单轮执行后的标准回复。"""

    agent_id: str
    session_key: str
    text: str
    stop_reason: str
    tool_calls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DispatchResult:
    """一次入站消息处理的完整结果，包含原始入站消息、路由结果和 Agent 回复。"""

    inbound: InboundMessage
    route: RouteResolution
    reply: AgentReply


@dataclass(slots=True)
class ProactiveTarget:
    """主动任务投递目标，用于 heartbeat、cron 等非用户触发的消息。"""

    channel: str
    account_id: str
    peer_id: str
    agent_id: str = "main"
