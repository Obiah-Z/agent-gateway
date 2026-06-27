from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.repository import StateReadRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.tasks.store import LocalTaskStore


@dataclass(slots=True)
class StateRepositoryBundle:
    """统一状态仓储装配结果。"""

    read: StateReadRepository


def build_state_repository(
    settings: GatewaySettings,
    *,
    sessions: SessionStore,
    tasks: LocalTaskStore,
    events: RuntimeEventStore,
    metrics: MetricsStore,
    alerts: AlertStore,
    memory: MemoryStore,
) -> StateRepositoryBundle:
    """根据当前配置装配状态仓储。

    目前先返回本地只读仓储，后续 PostgreSQL backend 接入后在这里切换实现，
    保持上层控制面和 Dashboard 不改调用方式。
    """

    del settings
    return StateRepositoryBundle(
        read=LocalStateReadRepository(
            sessions=sessions,
            tasks=tasks,
            events=events,
            metrics=metrics,
            alerts=alerts,
            memory=memory,
        )
    )
