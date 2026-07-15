"""Agent manifest loading.

The manifest is a compact, per-agent source of truth stored next to prompt
files. It intentionally supports a small YAML subset so the project does not
need an additional runtime dependency for basic Agent onboarding metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_gateway.runtime.domain.models import AgentConfig


@dataclass(frozen=True, slots=True)
class AgentRouteExample:
    """One routing contract example declared by an Agent manifest."""

    name: str
    user_text: str
    required_tools: tuple[str, ...] = ()
    read_only: bool = True
    requires_confirmation: bool = False
    requires_collaboration: bool = False
    collaboration_mode: str = "single-agent"


@dataclass(frozen=True, slots=True)
class AgentRoutingManifest:
    """Routing metadata that can be aggregated into runtime catalogs."""

    intent: str = ""
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    reason: str = ""
    next: str = ""
    persist_delegate_history: bool = False
    blocked_controller: bool = False


@dataclass(frozen=True, slots=True)
class AgentManifest:
    """Machine-readable Agent onboarding manifest."""

    id: str
    name: str
    path: Path
    layer: str = "custom"
    owner_scope: str = ""
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
    routing: AgentRoutingManifest = field(default_factory=AgentRoutingManifest)
    contract_examples: tuple[AgentRouteExample, ...] = ()

    def to_agent_config(self) -> AgentConfig:
        """Convert the manifest into the existing runtime AgentConfig."""

        return AgentConfig(
            id=self.id,
            name=self.name,
            personality=self.personality,
            model=self.model,
            dm_scope=self.dm_scope,
            extra_system=self.extra_system,
            tool_policy_mode=self.tool_policy_mode,
            tool_names=self.tool_names,
            memory_enabled=self.memory_enabled,
            memory_auto_recall=self.memory_auto_recall,
            memory_top_k=self.memory_top_k,
            prompt_dir=self.prompt_dir,
            use_global_prompt_files=self.use_global_prompt_files,
            skills_enabled=self.skills_enabled,
        )


def load_agent_manifests(workspace_root: Path) -> list[AgentManifest]:
    """Load all Agent manifests under workspace/agents."""

    agents_root = workspace_root / "agents"
    if not agents_root.exists():
        return []
    manifests = [
        load_agent_manifest(path)
        for path in sorted(agents_root.glob("*/agent.yaml"))
    ]
    return manifests


def load_agent_manifest(path: Path) -> AgentManifest:
    """Load a single Agent manifest file."""

    raw = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"agent manifest must be a mapping: {path}")
    manifest_id = _required_str(raw, "id", path)
    expected_id = path.parent.name
    if manifest_id != expected_id:
        raise ValueError(f"agent manifest id must match directory name: {path}")
    name = _required_str(raw, "name", path)
    prompt = _mapping(raw.get("prompt"))
    tools = _mapping(raw.get("tools"))
    memory = _mapping(raw.get("memory"))
    routing = _routing_manifest(_mapping(raw.get("routing")))
    contract = _mapping(raw.get("contract"))
    examples = tuple(
        _contract_example(item, routing.intent)
        for item in _list_of_mappings(contract.get("examples"))
    )
    prompt_dir = str(prompt.get("dir") or f"agents/{manifest_id}")
    _validate_prompt_files(path.parent, manifest_id)
    return AgentManifest(
        id=manifest_id,
        name=name,
        path=path,
        layer=str(raw.get("layer") or "custom"),
        owner_scope=str(raw.get("owner_scope") or ""),
        personality=str(raw.get("personality") or ""),
        model=str(raw.get("model") or ""),
        dm_scope=str(raw.get("dm_scope") or "per-peer"),
        extra_system=str(raw.get("extra_system") or ""),
        tool_policy_mode=str(tools.get("mode") or "all"),
        tool_names=tuple(str(name) for name in _list(tools.get("names")) if str(name).strip()),
        memory_enabled=bool(memory.get("enabled", True)),
        memory_auto_recall=bool(memory.get("auto_recall", True)),
        memory_top_k=max(1, int(memory.get("top_k", 3) or 3)),
        prompt_dir=prompt_dir,
        use_global_prompt_files=bool(prompt.get("use_global_files", True)),
        skills_enabled=bool(prompt.get("skills_enabled", True)),
        routing=routing,
        contract_examples=examples,
    )


def merge_agent_configs_with_manifests(
    config_agents: list[AgentConfig],
    manifests: list[AgentManifest],
) -> list[AgentConfig]:
    """Overlay manifest-derived AgentConfig on top of legacy JSON config."""

    by_id = {agent.id: agent for agent in config_agents}
    order = [agent.id for agent in config_agents]
    for manifest in manifests:
        if manifest.id not in by_id:
            order.append(manifest.id)
        by_id[manifest.id] = manifest.to_agent_config()
    return [by_id[agent_id] for agent_id in order]


def manifest_routing_catalog(workspace_root: Path) -> dict[str, AgentRoutingManifest]:
    """Return routing metadata keyed by Agent id."""

    return {
        manifest.id: manifest.routing
        for manifest in load_agent_manifests(workspace_root)
        if manifest.routing.intent or manifest.routing.aliases or manifest.routing.keywords
    }


def _routing_manifest(raw: dict[str, Any]) -> AgentRoutingManifest:
    return AgentRoutingManifest(
        intent=str(raw.get("intent") or ""),
        aliases=tuple(str(item) for item in _list(raw.get("aliases")) if str(item).strip()),
        keywords=tuple(str(item) for item in _list(raw.get("keywords")) if str(item).strip()),
        reason=str(raw.get("reason") or ""),
        next=str(raw.get("next") or ""),
        persist_delegate_history=bool(raw.get("persist_delegate_history", False)),
        blocked_controller=bool(raw.get("blocked_controller", False)),
    )


def _contract_example(raw: dict[str, Any], default_name: str) -> AgentRouteExample:
    return AgentRouteExample(
        name=str(raw.get("name") or default_name),
        user_text=str(raw.get("user_text") or ""),
        required_tools=tuple(str(item) for item in _list(raw.get("required_tools")) if str(item).strip()),
        read_only=bool(raw.get("read_only", True)),
        requires_confirmation=bool(raw.get("requires_confirmation", False)),
        requires_collaboration=bool(raw.get("requires_collaboration", False)),
        collaboration_mode=str(raw.get("collaboration_mode") or "single-agent"),
    )


def _validate_prompt_files(agent_dir: Path, manifest_id: str) -> None:
    missing = [
        name
        for name in ("IDENTITY.md", "SOUL.md", "TOOLS.md")
        if not (agent_dir / name).exists()
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"agent manifest {manifest_id} missing prompt files: {joined}")


def _required_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise ValueError(f"agent manifest missing required field {key!r}: {path}")
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _parse_simple_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line.strip()))
    value, index = _parse_yaml_block(lines, 0, 0)
    if index != len(lines):
        raise ValueError("unexpected trailing manifest content")
    return value


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, content = lines[index]
    if current_indent < indent:
        return {}, index
    if content.startswith("- "):
        return _parse_yaml_list(lines, index, current_indent)
    return _parse_yaml_mapping(lines, index, current_indent)


def _parse_yaml_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"unexpected indentation near: {content}")
        if content.startswith("- "):
            break
        key, value = _split_key_value(content)
        if value == "":
            nested, index = _parse_yaml_block(lines, index + 1, indent + 2)
            result[key] = nested
        else:
            result[key] = _parse_scalar(value)
            index += 1
    return result, index


def _parse_yaml_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            break
        item = content[2:].strip()
        if item == "":
            nested, index = _parse_yaml_block(lines, index + 1, indent + 2)
            result.append(nested)
            continue
        if ":" in item and not item.startswith(("'", '"')):
            key, value = _split_key_value(item)
            row: dict[str, Any] = {key: _parse_scalar(value) if value else {}}
            index += 1
            if index < len(lines) and lines[index][0] > indent:
                nested, index = _parse_yaml_block(lines, index, indent + 2)
                if isinstance(nested, dict):
                    row.update(nested)
                else:
                    raise ValueError(f"list item mapping expected nested mapping near: {item}")
            result.append(row)
            continue
        result.append(_parse_scalar(item))
        index += 1
    return result, index


def _split_key_value(content: str) -> tuple[str, str]:
    if ":" not in content:
        raise ValueError(f"expected key/value manifest line: {content}")
    key, value = content.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"empty manifest key: {content}")
    return key, value.strip()


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if stripped in {"true", "True"}:
        return True
    if stripped in {"false", "False"}:
        return False
    if stripped in {"null", "None", "~"}:
        return None
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("'") and stripped.endswith("'")
    ):
        return stripped[1:-1]
    try:
        return int(stripped)
    except ValueError:
        return stripped
