"""Runtime domain models and routing primitives."""

from .agents import AgentManager
from .ids import DEFAULT_AGENT_ID, normalize_agent_id
from .models import (
    AgentConfig,
    AgentReply,
    Binding,
    ConversationMessage,
    DispatchResult,
    InboundMessage,
    OutboundMessage,
    ProactiveTarget,
    RouteResolution,
)
from .router import BindingTable, build_session_key, resolve_route

__all__ = [
    "AgentConfig",
    "AgentManager",
    "AgentReply",
    "Binding",
    "BindingTable",
    "ConversationMessage",
    "DEFAULT_AGENT_ID",
    "DispatchResult",
    "InboundMessage",
    "OutboundMessage",
    "ProactiveTarget",
    "RouteResolution",
    "build_session_key",
    "normalize_agent_id",
    "resolve_route",
]
