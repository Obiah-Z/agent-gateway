from __future__ import annotations

import sys
from pathlib import Path

from agent_gateway.ai.context.diet import DietStore, register_diet_tools
from agent_gateway.ai.context.internship import InternshipStore, register_internship_tools
from agent_gateway.ai.context.memory import MemoryStore, register_memory_tools
from agent_gateway.ai.context.personal import PersonalStore, register_personal_tools
from agent_gateway.ai.tools.builtin import register_builtin_tools
from agent_gateway.ai.tools.github_repo import register_github_repo_tools
from agent_gateway.ai.tools.registry import ToolRegistry
from agent_gateway.runtime.domain.agent_manifest import load_agent_manifests


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "workspace"
    manifests = load_agent_manifests(workspace)
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)
    register_memory_tools(registry, MemoryStore(workspace))
    register_diet_tools(registry, DietStore(workspace))
    register_internship_tools(registry, InternshipStore(workspace))
    register_personal_tools(registry, PersonalStore(workspace))
    register_github_repo_tools(registry)
    tool_names = set(registry.names())
    tool_names.update({"web_search", "fetch_url"})
    aliases: dict[str, str] = {}
    errors: list[str] = []
    for manifest in manifests:
        for tool_name in manifest.tool_names:
            if tool_name not in tool_names:
                errors.append(f"{manifest.id}: unknown tool {tool_name}")
        for alias in manifest.routing.aliases:
            normalized = alias.strip().lower()
            existing = aliases.get(normalized)
            if existing and existing != manifest.id:
                errors.append(f"alias {alias!r} is used by both {existing} and {manifest.id}")
            aliases[normalized] = manifest.id
        for example in manifest.contract_examples:
            if not example.user_text.strip():
                errors.append(f"{manifest.id}: contract example {example.name!r} missing user_text")
            if not example.read_only and not example.requires_confirmation:
                errors.append(
                    f"{manifest.id}: write contract example {example.name!r} must require confirmation"
                )
            for tool_name in example.required_tools:
                if tool_name not in manifest.tool_names:
                    errors.append(
                        f"{manifest.id}: contract example {example.name!r} requires non-allowlisted tool {tool_name}"
                    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {len(manifests)} agent manifest(s) validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
