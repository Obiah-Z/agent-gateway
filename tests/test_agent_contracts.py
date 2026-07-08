from pathlib import Path

from agent_gateway.ai.agent_contracts import (
    DEFAULT_AGENT_ROUTING_CONTRACTS,
    baseline_agent_required_tools,
    find_agent_contract_gaps,
    load_agent_tool_allowlists,
)
from scripts.eval_agent_routing import DEFAULT_CASES


def test_routing_eval_uses_shared_agent_contracts() -> None:
    assert DEFAULT_CASES is DEFAULT_AGENT_ROUTING_CONTRACTS


def test_baseline_agent_required_tools_aggregates_contracts() -> None:
    required = baseline_agent_required_tools()

    assert "repo-analyzer" in required
    assert "compose_github_repo_analysis" in required["repo-analyzer"]
    assert "github_repo_reading_guide" in required["repo-analyzer"]
    assert "personal-secretary-zhanghaibo" in required
    assert "personal_due_todo_digest_generate" in required["personal-secretary-zhanghaibo"]


def test_project_agent_config_satisfies_shared_contracts() -> None:
    tool_allowlists = load_agent_tool_allowlists(Path("config/agents.json"))

    missing_agents, missing_tools = find_agent_contract_gaps(tool_allowlists)

    assert missing_agents == []
    assert missing_tools == {}
