import json
from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import load_agents


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ops_agent_can_run_read_only_space_advisor() -> None:
    settings = GatewaySettings(
        config_dir=REPO_ROOT / "config",
        data_dir=REPO_ROOT / "data",
        workspace_root=REPO_ROOT / "workspace",
    )
    ops = next(agent for agent in load_agents(settings) if agent.id == "ops")

    assert ops.prompt_dir == "agents/ops"
    assert ops.memory_enabled is False
    assert ops.tool_policy_mode == "allowlist"
    tools = set(ops.tool_names)
    assert tools == {
        "bash",
        "get_current_time",
        "list_directory",
        "ops_readonly_health",
        "ops_runtime_diagnostics",
        "ops_troubleshooting_plan",
        "read_file",
        "summarize_ops_health",
    }
    assert "write_file" not in tools
    assert "memory_write" not in tools


def test_space_advisor_cron_targets_ops_agent() -> None:
    payload = json.loads((REPO_ROOT / "workspace" / "CRON.json").read_text(encoding="utf-8"))
    job = next(job for job in payload["jobs"] if job["id"] == "server-space-advisor")

    assert job["enabled"] is True
    assert job["schedule"] == {
        "kind": "cron",
        "expr": "30 9 * * *",
        "tz": "Asia/Shanghai",
    }
    assert job["target"]["agent_id"] == "ops"
    assert "space_advisor.py" in job["payload"]["message"]
    assert "timeout 参数设为 180" in job["payload"]["message"]
