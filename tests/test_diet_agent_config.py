import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USER_PEER_ID = "dd5bf6254c5b565c1f59edf6b29aa30c"
USER_SCOPE = f"user:wework:wework-main:direct:{USER_PEER_ID}"
AGENT_ID = "diet-assistant-dd5bf625"


def test_diet_agent_config_targets_single_wework_user() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    bindings = json.loads((ROOT / "config" / "bindings.json").read_text(encoding="utf-8"))["bindings"]

    agent = next(row for row in agents if row["id"] == AGENT_ID)
    binding = next(row for row in bindings if row["agent_id"] == AGENT_ID)

    assert agent["prompt_policy"]["prompt_dir"] == f"agents/{AGENT_ID}"
    assert "meal_log_add" in agent["tool_policy"]["tool_names"]
    assert binding["tier"] == 1
    assert binding["match_key"] == "peer_id"
    assert binding["match_value"] == USER_PEER_ID
    assert binding["priority"] > 50


def test_diet_agent_cron_targets_single_wework_peer() -> None:
    cron = json.loads(
        (ROOT / "workspace" / "agents" / AGENT_ID / "CRON.json").read_text(encoding="utf-8")
    )

    enabled_jobs = [job for job in cron["jobs"] if job["enabled"]]
    assert {job["id"] for job in enabled_jobs} == {"daily-diet-plan", "daily-nutrition-summary"}
    for job in cron["jobs"]:
        target = job["target"]
        assert target["channel"] == "wework"
        assert target["account_id"] == "wework-main"
        assert target["peer_id"] == USER_PEER_ID
        assert target["agent_id"] == AGENT_ID
        assert job["payload"]["user_scope"] == USER_SCOPE
        assert job["payload"]["kind"] in {
            "diet_plan_generate",
            "nutrition_day_summary",
            "meal_reminder",
        }
