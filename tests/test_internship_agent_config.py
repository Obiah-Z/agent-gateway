import json
from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import load_agents


ROOT = Path(__file__).resolve().parents[1]
AGENT_ID = "internship-assistant-zhanghaibo"
SECRETARY_AGENT_ID = "personal-secretary-zhanghaibo"


def _agent_rows() -> list[dict[str, object]]:
    settings = GatewaySettings(
        config_dir=ROOT / "config",
        data_dir=ROOT / "data",
        workspace_root=ROOT / "workspace",
    )
    rows = []
    for agent in load_agents(settings):
        rows.append(
            {
                "id": agent.id,
                "extra_system": agent.extra_system,
                "tool_policy": {
                    "mode": agent.tool_policy_mode,
                    "tool_names": list(agent.tool_names),
                },
                "prompt_policy": {
                    "prompt_dir": agent.prompt_dir,
                    "use_global_files": agent.use_global_prompt_files,
                    "skills_enabled": agent.skills_enabled,
                },
            }
        )
    return rows


def test_internship_agent_config_is_user_scoped_without_owning_wework_entry() -> None:
    agents = _agent_rows()
    bindings = json.loads((ROOT / "config" / "bindings.json").read_text(encoding="utf-8"))["bindings"]

    agent = next(row for row in agents if row["id"] == AGENT_ID)
    tools = set(agent["tool_policy"]["tool_names"])

    assert agent["prompt_policy"]["prompt_dir"] == f"agents/{AGENT_ID}"
    assert {
        "internship_log_add",
        "format_internship_log_entry",
        "internship_log_list",
        "internship_log_search",
        "format_internship_log_list",
        "internship_daily_report_generate",
        "format_internship_daily_report",
    }.issubset(tools)
    assert "user:wework:wework-main:direct:zhanghaibo" in agent["extra_system"]
    assert not any(row["agent_id"] == AGENT_ID and row["match_key"] == "peer_id" for row in bindings)
    assert any(row["agent_id"] == SECRETARY_AGENT_ID and row["match_key"] == "peer_id" for row in bindings)


def test_internship_agent_prompt_and_secretary_routing_are_present() -> None:
    prompt_dir = ROOT / "workspace" / "agents" / AGENT_ID
    combined = "\n".join(
        (prompt_dir / name).read_text(encoding="utf-8")
        for name in ["IDENTITY.md", "SOUL.md", "TOOLS.md"]
    )
    secretary = "\n".join(
        (ROOT / "workspace" / "agents" / SECRETARY_AGENT_ID / name).read_text(encoding="utf-8")
        for name in ["IDENTITY.md", "SOUL.md"]
    )

    assert "实习记录助手" in combined
    assert "internship_log_add" in combined
    assert "internship_daily_report_generate" in combined
    assert "不编造未记录" in combined
    assert AGENT_ID in secretary
    assert "日报" in secretary
    assert "导师" in secretary
