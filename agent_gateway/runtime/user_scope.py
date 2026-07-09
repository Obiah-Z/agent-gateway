"""用户作用域规范化工具。

系统里 memory、personal、diet 都依赖 user_scope 做用户级隔离；这里集中处理
session_key 派生和历史别名，避免同一用户因入口不同生成多个 scope。
"""

from __future__ import annotations


USER_SCOPE_ALIASES = {
    "user:direct:zhanghaibo": "user:wework:wework-main:direct:zhanghaibo",
    "user:wework:wework-main:direct:ZhangHaiBo": "user:wework:wework-main:direct:zhanghaibo",
}


def canonicalize_user_scope(user_scope: str) -> str:
    """返回规范 user_scope；空值表示全局作用域。"""

    raw = " ".join(str(user_scope or "").strip().split())
    if not raw:
        return ""
    if raw.startswith("agent:"):
        raw = user_scope_from_session_key(raw)
    if raw.startswith("user:"):
        raw = _normalize_user_scope_parts(raw)
    return USER_SCOPE_ALIASES.get(raw, raw)


def user_scope_from_session_key(session_key: str) -> str:
    """从 session_key 推导用户 scope。

    标准会话：
    `agent:<agent_id>:<channel>:<account_id>:direct:<peer_id>`
    会归一为：
    `user:<channel>:<account_id>:direct:<peer_id>`。
    """

    raw = " ".join(str(session_key or "").strip().split())
    if not raw:
        return ""
    if raw.startswith("system:"):
        return ""
    parts = raw.split(":")
    if len(parts) >= 6 and parts[0] == "agent":
        return canonicalize_user_scope("user:" + ":".join(parts[2:6]))
    if len(parts) >= 4 and parts[0] == "agent" and parts[2] == "direct":
        return canonicalize_user_scope(f"user:direct:{parts[3]}")
    if len(parts) >= 3 and parts[0] == "agent":
        return canonicalize_user_scope("user:" + ":".join(parts[2:]))
    return canonicalize_user_scope(raw)


def _normalize_user_scope_parts(scope: str) -> str:
    parts = scope.split(":")
    if len(parts) == 5 and parts[0] == "user" and parts[3] == "direct":
        return ":".join(
            [
                "user",
                parts[1].lower(),
                parts[2].lower(),
                "direct",
                parts[4].lower(),
            ]
        )
    if len(parts) == 3 and parts[0] == "user" and parts[1] == "direct":
        return f"user:direct:{parts[2].lower()}"
    return scope
