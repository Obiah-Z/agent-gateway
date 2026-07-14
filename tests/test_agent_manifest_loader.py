from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import load_agents
from agent_gateway.runtime.domain.agent_manifest import (
    load_agent_manifest,
    load_agent_manifests,
    merge_agent_configs_with_manifests,
)
from agent_gateway.runtime.domain.models import AgentConfig


ROOT = Path(__file__).resolve().parents[1]


def test_project_agent_manifests_load_private_specialists() -> None:
    manifests = load_agent_manifests(ROOT / "workspace")
    by_id = {manifest.id: manifest for manifest in manifests}

    assert "diet-assistant-zhanghaibo" in by_id
    assert "internship-assistant-zhanghaibo" in by_id
    assert by_id["diet-assistant-zhanghaibo"].routing.intent == "diet"
    assert "diet-agent" in by_id["diet-assistant-zhanghaibo"].routing.aliases
    assert by_id["internship-assistant-zhanghaibo"].routing.intent == "internship"
    assert "internship-agent" in by_id["internship-assistant-zhanghaibo"].routing.aliases
    assert by_id["internship-assistant-zhanghaibo"].contract_examples[0].requires_confirmation is True


def test_manifest_converts_to_agent_config() -> None:
    manifest = load_agent_manifest(
        ROOT / "workspace" / "agents" / "internship-assistant-zhanghaibo" / "agent.yaml"
    )

    config = manifest.to_agent_config()

    assert config.id == "internship-assistant-zhanghaibo"
    assert config.name == "张海波实习记录助手"
    assert config.dm_scope == "per-account-channel-peer"
    assert config.prompt_dir == "agents/internship-assistant-zhanghaibo"
    assert "internship_log_add" in config.tool_names
    assert config.memory_top_k == 4


def test_load_agents_overlays_matching_manifest_config() -> None:
    settings = GatewaySettings(
        config_dir=ROOT / "config",
        data_dir=ROOT / "data",
        workspace_root=ROOT / "workspace",
    )

    agents = load_agents(settings)
    by_id = {agent.id: agent for agent in agents}

    assert by_id["diet-assistant-zhanghaibo"].prompt_dir == "agents/diet-assistant-zhanghaibo"
    assert by_id["internship-assistant-zhanghaibo"].prompt_dir == "agents/internship-assistant-zhanghaibo"
    assert "format_internship_daily_report" in by_id["internship-assistant-zhanghaibo"].tool_names


def test_merge_agent_configs_with_manifests_appends_new_manifest_agent() -> None:
    manifest = load_agent_manifest(
        ROOT / "workspace" / "agents" / "internship-assistant-zhanghaibo" / "agent.yaml"
    )

    merged = merge_agent_configs_with_manifests(
        [AgentConfig(id="main", name="Main")],
        [manifest],
    )

    assert [agent.id for agent in merged] == ["main", "internship-assistant-zhanghaibo"]
