"""Agent 注册表。

应用层通过这个内存注册表读取当前可用 Agent；当前权威配置来源是
`workspace/agents/*/agent.yaml`，旧版 `config/agents.json` 只保留兼容壳。
控制面 reload 后会整体替换这里的内容。
"""

from __future__ import annotations

from dataclasses import replace

from .ids import normalize_agent_id
from .models import AgentConfig

__all__ = ["AgentManager"]


class AgentManager:
    """管理运行时可见的 Agent 配置。"""

    def __init__(self) -> None:
        self._agents: dict[str, AgentConfig] = {}

    def register(self, config: AgentConfig) -> AgentConfig:
        """注册 Agent，并在进入内存表前统一规范化 ID。"""

        normalized = replace(config, id=normalize_agent_id(config.id))
        self._agents[normalized.id] = normalized
        return normalized

    def get(self, agent_id: str) -> AgentConfig | None:
        """按 ID 获取一个 Agent 配置。"""

        return self._agents.get(normalize_agent_id(agent_id))

    def list(self) -> list[AgentConfig]:
        """列出当前所有 Agent 配置。"""

        return list(self._agents.values())

    def replace_all(self, configs: list[AgentConfig]) -> list[AgentConfig]:
        """用于控制面 reload，一次性替换完整 Agent 列表。"""

        self._agents.clear()
        return [self.register(config) for config in configs]
