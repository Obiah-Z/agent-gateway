from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_gateway.models import AgentConfig
from agent_gateway.tools.registry import ToolRegistry


ALLOWED_TOOL_POLICY_MODES = {"all", "allowlist", "denylist"}
ALLOWED_DM_SCOPES = {"per-peer", "per-channel-peer", "per-account-channel-peer"}
ALLOWED_TOOL_CAPABILITIES = {
    "filesystem",
    "shell",
    "memory",
    "utility",
    "read",
    "write",
    "exec",
    "web",
    "search",
    "fetch",
    "network",
}


@dataclass(slots=True)
class AgentManifestTemplate:
    agent: dict[str, object]
    prompt_files: dict[str, str]


def validate_agent_config(agent: AgentConfig, tools: ToolRegistry) -> list[str]:
    issues: list[str] = []
    if not agent.id.strip():
        issues.append("agent id is required")
    if not agent.name.strip():
        issues.append("agent name is required")
    if agent.tool_policy_mode not in ALLOWED_TOOL_POLICY_MODES:
        issues.append(
            f"tool_policy.mode must be one of {sorted(ALLOWED_TOOL_POLICY_MODES)}"
        )
    if agent.dm_scope not in ALLOWED_DM_SCOPES:
        issues.append(f"dm_scope must be one of {sorted(ALLOWED_DM_SCOPES)}")
    if agent.memory_top_k < 1 or agent.memory_top_k > 20:
        issues.append("memory_policy.top_k must be between 1 and 20")
    if ".." in agent.resolved_prompt_dir().split("/"):
        issues.append("prompt_policy.prompt_dir must stay inside workspace")

    available_tools = set(tools.names())
    unknown_tools = sorted(name for name in agent.tool_names if name not in available_tools)
    if unknown_tools:
        issues.append(f"unknown tool names: {', '.join(unknown_tools)}")
    return issues


def build_agent_template(
    agent_id: str,
    *,
    name: str = "",
    capability_tags: list[str] | None = None,
    use_global_prompt_files: bool = True,
    memory_enabled: bool = True,
    skills_enabled: bool = True,
    tools: ToolRegistry | None = None,
) -> AgentManifestTemplate:
    normalized_id = agent_id.strip().lower().replace(" ", "-")
    capability_tags = capability_tags or []
    selected_tags = [tag for tag in capability_tags if tag in ALLOWED_TOOL_CAPABILITIES]
    tool_names = tools.names_for_tags(selected_tags) if tools is not None else []
    display_name = name.strip() or normalized_id.title()
    prompt_dir = f"agents/{normalized_id}"
    return AgentManifestTemplate(
        agent={
            "id": normalized_id,
            "name": display_name,
            "personality": "",
            "model": "",
            "dm_scope": "per-peer",
            "extra_system": "",
            "tool_policy": {
                "mode": "allowlist" if tool_names else "all",
                "tool_names": tool_names,
            },
            "memory_policy": {
                "enabled": memory_enabled,
                "auto_recall": memory_enabled,
                "top_k": 3,
            },
            "prompt_policy": {
                "prompt_dir": prompt_dir,
                "use_global_files": use_global_prompt_files,
                "skills_enabled": skills_enabled,
            },
        },
        prompt_files={
            "IDENTITY.md": f"你是 {display_name} 智能体，请保持职责边界清晰，稳定完成分配给你的任务。\n",
            "SOUL.md": (
                "默认工作方式：\n\n"
                "- 先确认目标和约束\n"
                "- 输出简洁、稳定、可执行的结果\n"
                "- 必要时使用允许的工具完成任务\n"
            ),
        },
    )


def materialize_agent_template(workspace_root: Path, template: AgentManifestTemplate) -> list[str]:
    prompt_dir = template.agent.get("prompt_policy", {}).get("prompt_dir", "")
    if not isinstance(prompt_dir, str) or not prompt_dir.strip():
        raise ValueError("template prompt_dir is required")
    target_dir = (workspace_root / prompt_dir).resolve()
    target_dir.relative_to(workspace_root.resolve())
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for filename, content in template.prompt_files.items():
        path = target_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(str(path.relative_to(workspace_root)))
    return written
