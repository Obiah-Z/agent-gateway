"""网关内部 ID 规范化规则。"""

from __future__ import annotations

import re

VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
INVALID_CHARS_RE = re.compile(r"[^a-z0-9_-]+")
DEFAULT_AGENT_ID = "main"

__all__ = ["DEFAULT_AGENT_ID", "normalize_agent_id"]


def normalize_agent_id(value: str) -> str:
    """把用户输入或配置中的 Agent ID 规整成稳定、可用于路径和绑定的形式。"""

    trimmed = value.strip()
    if not trimmed:
        return DEFAULT_AGENT_ID
    lowered = trimmed.lower()
    if VALID_ID_RE.match(lowered):
        return lowered
    cleaned = INVALID_CHARS_RE.sub("-", lowered).strip("-")[:64]
    return cleaned or DEFAULT_AGENT_ID
