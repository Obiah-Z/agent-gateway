from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ConversationMessage = dict[str, Any]


@dataclass(slots=True)
class InboundMessage:
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
    channel: str
    to: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Binding:
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
    agent_id: str
    session_key: str
    matched_binding: Binding | None = None


@dataclass(slots=True)
class AgentConfig:
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
        return self.model or default_model

    def resolved_prompt_dir(self) -> str:
        return (self.prompt_dir or f"agents/{self.id}").strip("/")

    def allowed_tool_names(self, available_tool_names: list[str]) -> list[str]:
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
        parts = [f"You are {self.name}."]
        if self.personality:
            parts.append(f"Your personality: {self.personality}")
        if self.extra_system:
            parts.append(self.extra_system)
        parts.append("Answer helpfully, use tools when necessary, and stay consistent.")
        return " ".join(parts)


@dataclass(slots=True)
class AgentReply:
    agent_id: str
    session_key: str
    text: str
    stop_reason: str
    tool_calls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DispatchResult:
    inbound: InboundMessage
    route: RouteResolution
    reply: AgentReply


@dataclass(slots=True)
class ProactiveTarget:
    channel: str
    account_id: str
    peer_id: str
    agent_id: str = "main"
