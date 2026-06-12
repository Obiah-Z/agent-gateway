from __future__ import annotations

from dataclasses import replace

from .ids import normalize_agent_id
from .models import AgentConfig


class AgentManager:
    def __init__(self) -> None:
        self._agents: dict[str, AgentConfig] = {}

    def register(self, config: AgentConfig) -> AgentConfig:
        normalized = replace(config, id=normalize_agent_id(config.id))
        self._agents[normalized.id] = normalized
        return normalized

    def get(self, agent_id: str) -> AgentConfig | None:
        return self._agents.get(normalize_agent_id(agent_id))

    def list(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def replace_all(self, configs: list[AgentConfig]) -> list[AgentConfig]:
        self._agents.clear()
        return [self.register(config) for config in configs]
