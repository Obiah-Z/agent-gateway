import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ops_agent_can_run_read_only_space_advisor() -> None:
    payload = json.loads((REPO_ROOT / "config" / "agents.json").read_text(encoding="utf-8"))
    ops = next(agent for agent in payload["agents"] if agent["id"] == "ops")

    assert ops["prompt_policy"]["prompt_dir"] == "agents/ops"
    assert ops["memory_policy"]["enabled"] is False
    assert ops["tool_policy"]["mode"] == "allowlist"
    assert set(ops["tool_policy"]["tool_names"]) == {
        "bash",
        "get_current_time",
        "list_directory",
        "read_file",
    }


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
