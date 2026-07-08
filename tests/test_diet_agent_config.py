import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USER_PEER_ID = "ZhangHaiBo"
USER_SCOPE = "user:wework:wework-main:direct:zhanghaibo"
AGENT_ID = "diet-assistant-zhanghaibo"
SECRETARY_AGENT_ID = "personal-secretary-zhanghaibo"


def test_diet_agent_config_is_user_scoped_without_owning_wework_entry() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    bindings = json.loads((ROOT / "config" / "bindings.json").read_text(encoding="utf-8"))["bindings"]

    agent = next(row for row in agents if row["id"] == AGENT_ID)
    secretary_binding = next(row for row in bindings if row["agent_id"] == SECRETARY_AGENT_ID)

    assert agent["prompt_policy"]["prompt_dir"] == f"agents/{AGENT_ID}"
    assert "format_diet_profile" in agent["tool_policy"]["tool_names"]
    assert "diet_today_status" in agent["tool_policy"]["tool_names"]
    assert "format_diet_today_status" in agent["tool_policy"]["tool_names"]
    assert "format_diet_profile_update" in agent["tool_policy"]["tool_names"]
    assert "meal_log_add" in agent["tool_policy"]["tool_names"]
    assert "format_meal_log_entry" in agent["tool_policy"]["tool_names"]
    assert "format_meal_log_list" in agent["tool_policy"]["tool_names"]
    assert "format_nutrition_day_summary" in agent["tool_policy"]["tool_names"]
    assert "format_diet_plan" in agent["tool_policy"]["tool_names"]
    assert "weight_log_add" in agent["tool_policy"]["tool_names"]
    assert "format_weight_log_entry" in agent["tool_policy"]["tool_names"]
    assert "diet_coach_briefing" in agent["tool_policy"]["tool_names"]
    assert "format_diet_progress_summary" in agent["tool_policy"]["tool_names"]
    assert "format_diet_coach_briefing" in agent["tool_policy"]["tool_names"]
    assert "diet_daily_loop_generate" in agent["tool_policy"]["tool_names"]
    assert "format_diet_daily_loop" in agent["tool_policy"]["tool_names"]
    assert "diet_next_meal_card_generate" in agent["tool_policy"]["tool_names"]
    assert "format_diet_next_meal_card" in agent["tool_policy"]["tool_names"]
    assert "diet_day_review_plan_generate" in agent["tool_policy"]["tool_names"]
    assert "format_diet_day_review_plan" in agent["tool_policy"]["tool_names"]
    assert "diet_weekly_plan_generate" in agent["tool_policy"]["tool_names"]
    assert "format_diet_weekly_plan" in agent["tool_policy"]["tool_names"]
    assert "diet_inbox_triage" in agent["tool_policy"]["tool_names"]
    assert "format_diet_inbox_triage" in agent["tool_policy"]["tool_names"]
    assert "diet_inbox_commit" in agent["tool_policy"]["tool_names"]
    assert "format_diet_inbox_commit" in agent["tool_policy"]["tool_names"]
    assert "format_memory_write" in agent["tool_policy"]["tool_names"]
    assert "format_memory_search" in agent["tool_policy"]["tool_names"]
    assert not any(row["agent_id"] == AGENT_ID and row["match_key"] == "peer_id" for row in bindings)
    assert secretary_binding["tier"] == 1
    assert secretary_binding["match_key"] == "peer_id"
    assert secretary_binding["match_value"] == USER_PEER_ID
    assert secretary_binding["priority"] > 50


def test_platform_entries_and_personal_secretary_are_separated() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    bindings = json.loads((ROOT / "config" / "bindings.json").read_text(encoding="utf-8"))["bindings"]

    agent_ids = {row["id"] for row in agents}
    assert {"feishu-entry", "wework-entry", SECRETARY_AGENT_ID, AGENT_ID}.issubset(agent_ids)
    assert any(
        row["agent_id"] == "wework-entry"
        and row["match_key"] == "account_id"
        and row["match_value"] == "wework-main"
        for row in bindings
    )
    assert any(
        row["agent_id"] == "feishu-entry"
        and row["match_key"] == "account_id"
        and row["match_value"] == "feishu-secondary"
        for row in bindings
    )


def test_main_agent_has_task_intent_classifier_and_prompt_boundary() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    by_id = {row["id"]: row for row in agents}
    tools = set(by_id["main"]["tool_policy"]["tool_names"])
    prompt_dir = ROOT / "workspace" / by_id["main"]["prompt_policy"]["prompt_dir"]

    assert "classify_task_intent" in tools
    assert "format_entry_response" in tools
    assert "build_agent_handoff_prompt" in tools
    assert "build_collaboration_stage_handoff" in tools
    assert "summarize_collaboration_progress" in tools
    assert "format_collaboration_progress" in tools
    assert "compose_collaboration_final_summary" in tools
    assert "format_collaboration_final_summary" in tools
    assert "list_agent_capabilities" in tools
    assert "format_agent_capability_catalog" in tools
    assert "match_agent_capability" in tools
    assert "format_agent_capability_match" in tools
    assert "compose_agent_handoff_package" in tools
    assert "format_agent_handoff_package" in tools
    assert "list_agent_collaboration_routes" in tools
    assert "plan_agent_collaboration" in tools
    assert "explain_agent_route" in tools
    assert "prepare_entry_route_response" in tools
    assert by_id["main"]["prompt_policy"]["prompt_dir"] == "agents/main"
    assert (prompt_dir / "IDENTITY.md").exists()
    assert (prompt_dir / "SOUL.md").exists()
    assert (prompt_dir / "TOOLS.md").exists()

    combined_prompt = "\n".join(
        (prompt_dir / name).read_text(encoding="utf-8")
        for name in ["IDENTITY.md", "SOUL.md", "TOOLS.md"]
    )
    assert "classify_task_intent" in combined_prompt
    assert "format_entry_response" in combined_prompt
    assert "build_agent_handoff_prompt" in combined_prompt
    assert "build_collaboration_stage_handoff" in combined_prompt
    assert "summarize_collaboration_progress" in combined_prompt
    assert "format_collaboration_progress" in combined_prompt
    assert "compose_collaboration_final_summary" in combined_prompt
    assert "format_collaboration_final_summary" in combined_prompt
    assert "list_agent_capabilities" in combined_prompt
    assert "format_agent_capability_catalog" in combined_prompt
    assert "match_agent_capability" in combined_prompt
    assert "format_agent_capability_match" in combined_prompt
    assert "compose_agent_handoff_package" in combined_prompt
    assert "format_agent_handoff_package" in combined_prompt
    assert "list_agent_collaboration_routes" in combined_prompt
    assert "plan_agent_collaboration" in combined_prompt
    assert "explain_agent_route" in combined_prompt
    assert "prepare_entry_route_response" in combined_prompt
    assert "不假装已经完成多 Agent 自动交接" in combined_prompt
    assert "personal_todo_add" not in tools
    assert "meal_log_add" not in tools
    assert "bash" not in tools


def test_platform_entry_agents_share_intent_classification_flow() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    by_id = {row["id"]: row for row in agents}

    for agent_id in ["feishu-entry", "wework-entry"]:
        tools = set(by_id[agent_id]["tool_policy"]["tool_names"])
        prompt_dir = ROOT / "workspace" / by_id[agent_id]["prompt_policy"]["prompt_dir"]
        identity = (prompt_dir / "IDENTITY.md").read_text(encoding="utf-8")
        soul = (prompt_dir / "SOUL.md").read_text(encoding="utf-8")
        tools_md = (prompt_dir / "TOOLS.md").read_text(encoding="utf-8")

        assert "classify_task_intent" in tools
        assert "format_entry_response" in tools
        assert "list_agent_capabilities" in tools
        assert "format_agent_capability_catalog" in tools
        assert "match_agent_capability" in tools
        assert "format_agent_capability_match" in tools
        assert "compose_agent_handoff_package" in tools
        assert "format_agent_handoff_package" in tools
        assert "list_agent_collaboration_routes" in tools
        assert "suggest_agent_delegation" in tools
        assert "build_agent_handoff_prompt" in tools
        assert "build_collaboration_stage_handoff" in tools
        assert "summarize_collaboration_progress" in tools
        assert "format_collaboration_progress" in tools
        assert "compose_collaboration_final_summary" in tools
        assert "format_collaboration_final_summary" in tools
        assert "plan_agent_collaboration" in tools
        assert "explain_agent_route" in tools
        assert "prepare_entry_route_response" in tools
        assert "classify_task_intent" in identity
        assert "classify_task_intent" in soul
        assert "build_agent_handoff_prompt" in identity
        assert "build_agent_handoff_prompt" in soul
        assert "build_collaboration_stage_handoff" in identity
        assert "build_collaboration_stage_handoff" in soul
        assert "summarize_collaboration_progress" in identity
        assert "summarize_collaboration_progress" in soul
        assert "format_collaboration_progress" in soul
        assert "compose_collaboration_final_summary" in soul
        assert "format_collaboration_final_summary" in soul
        assert "list_agent_capabilities" in identity
        assert "format_agent_capability_catalog" in identity
        assert "match_agent_capability" in identity
        assert "format_agent_capability_match" in identity
        assert "compose_agent_handoff_package" in identity
        assert "format_agent_handoff_package" in identity
        assert "format_agent_capability_catalog" in soul
        assert "match_agent_capability" in soul
        assert "format_agent_capability_match" in soul
        assert "compose_agent_handoff_package" in soul
        assert "format_agent_handoff_package" in soul
        assert "list_agent_collaboration_routes" in identity
        assert "list_agent_collaboration_routes" in soul
        assert "plan_agent_collaboration" in identity
        assert "plan_agent_collaboration" in soul
        assert "explain_agent_route" in identity
        assert "explain_agent_route" in soul
        assert "prepare_entry_route_response" in identity
        assert "prepare_entry_route_response" in soul
        assert "format_entry_response" in soul
        assert "suggest_agent_delegation" in soul
        assert "classify_task_intent" in tools_md
        assert "build_agent_handoff_prompt" in tools_md
        assert "build_collaboration_stage_handoff" in tools_md
        assert "summarize_collaboration_progress" in tools_md
        assert "format_collaboration_progress" in tools_md
        assert "compose_collaboration_final_summary" in tools_md
        assert "format_collaboration_final_summary" in tools_md
        assert "list_agent_capabilities" in tools_md
        assert "format_agent_capability_catalog" in tools_md
        assert "match_agent_capability" in tools_md
        assert "format_agent_capability_match" in tools_md
        assert "compose_agent_handoff_package" in tools_md
        assert "format_agent_handoff_package" in tools_md
        assert "list_agent_collaboration_routes" in tools_md
        assert "plan_agent_collaboration" in tools_md
        assert "explain_agent_route" in tools_md
        assert "prepare_entry_route_response" in tools_md
        assert "suggest_agent_delegation" in tools_md
        assert "format_entry_response" in tools_md


def test_research_agent_has_brief_tool_and_source_prompt() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    tools = {row["id"]: set(row["tool_policy"]["tool_names"]) for row in agents}
    identity = (ROOT / "workspace" / "agents" / "research" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "research" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (ROOT / "workspace" / "agents" / "research" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "assess_research_confidence" in tools["research"]
    assert "format_research_confidence_assessment" in tools["research"]
    assert "compose_research_brief" in tools["research"]
    assert "format_research_brief" in tools["research"]
    assert "compose_research_evidence_pack" in tools["research"]
    assert "compose_research_option_comparison" in tools["research"]
    assert {"web_search", "fetch_url"}.issubset(tools["research"])
    assert "assess_research_confidence" in identity
    assert "assess_research_confidence" in soul
    assert "assess_research_confidence" in tools_md
    assert "format_research_confidence_assessment" in identity
    assert "format_research_confidence_assessment" in soul
    assert "format_research_confidence_assessment" in tools_md
    assert "compose_research_brief" in identity
    assert "compose_research_brief" in soul
    assert "compose_research_brief" in tools_md
    assert "format_research_brief" in identity
    assert "format_research_brief" in soul
    assert "format_research_brief" in tools_md
    assert "compose_research_evidence_pack" in identity
    assert "compose_research_evidence_pack" in soul
    assert "compose_research_evidence_pack" in tools_md
    assert "compose_research_option_comparison" in identity
    assert "compose_research_option_comparison" in soul
    assert "compose_research_option_comparison" in tools_md
    assert "技术选型" in tools_md
    assert "未经核验" in tools_md


def test_personal_secretary_cron_targets_single_wework_peer() -> None:
    cron = json.loads(
        (ROOT / "workspace" / "agents" / SECRETARY_AGENT_ID / "CRON.json").read_text(encoding="utf-8")
    )

    for job in cron["jobs"]:
        target = job["target"]
        assert target["channel"] == "wework"
        assert target["account_id"] == "wework-main"
        assert target["peer_id"] == USER_PEER_ID
        assert target["agent_id"] == SECRETARY_AGENT_ID


def test_diet_agent_cron_targets_single_wework_peer() -> None:
    cron = json.loads(
        (ROOT / "workspace" / "agents" / AGENT_ID / "CRON.json").read_text(encoding="utf-8")
    )

    enabled_jobs = [job for job in cron["jobs"] if job["enabled"]]
    assert {"daily-diet-plan", "daily-nutrition-summary"}.issubset(
        {job["id"] for job in enabled_jobs}
    )
    assert all(job["id"].endswith("reminder") or job["id"].startswith("daily-") for job in cron["jobs"])
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


def test_diet_agent_prompt_requires_gender_inference() -> None:
    identity = (ROOT / "workspace" / "agents" / AGENT_ID / "IDENTITY.md").read_text(encoding="utf-8")
    soul = (ROOT / "workspace" / "agents" / AGENT_ID / "SOUL.md").read_text(encoding="utf-8")
    tools_md = (ROOT / "workspace" / "agents" / AGENT_ID / "TOOLS.md").read_text(encoding="utf-8")

    assert "gender=male" in tools_md
    assert "成年男性" in tools_md
    assert "profile_update" in tools_md
    assert "profile_get" in tools_md
    assert "format_diet_profile" in tools_md
    assert "format_diet_profile" in identity
    assert "format_diet_profile" in soul
    assert "diet_today_status" in tools_md
    assert "diet_today_status" in identity
    assert "diet_today_status" in soul
    assert "format_diet_today_status" in tools_md
    assert "format_diet_today_status" in identity
    assert "format_diet_today_status" in soul
    assert "format_diet_profile_update" in tools_md
    assert "format_diet_profile_update" in identity
    assert "format_diet_profile_update" in soul
    assert "format_diet_inbox_commit" in tools_md
    assert "format_diet_inbox_commit" in identity
    assert "format_diet_inbox_commit" in soul
    assert "format_memory_write" in tools_md
    assert "format_memory_write" in identity
    assert "format_memory_write" in soul
    assert "format_memory_search" in tools_md
    assert "format_memory_search" in identity
    assert "format_memory_search" in soul
    assert "meal_log_add" in tools_md
    assert "format_meal_log_entry" in tools_md
    assert "format_meal_log_entry" in identity
    assert "format_meal_log_entry" in soul
    assert "meal_log_list" in tools_md
    assert "format_meal_log_list" in tools_md
    assert "format_meal_log_list" in identity
    assert "format_meal_log_list" in soul
    assert "nutrition_day_summary" in tools_md
    assert "format_nutrition_day_summary" in tools_md
    assert "format_nutrition_day_summary" in identity
    assert "format_nutrition_day_summary" in soul
    assert "diet_plan_generate" in tools_md
    assert "format_diet_plan" in tools_md
    assert "format_diet_plan" in identity
    assert "format_diet_plan" in soul
    assert "weight_log_add" in tools_md
    assert "format_weight_log_entry" in tools_md
    assert "format_weight_log_entry" in identity
    assert "format_weight_log_entry" in soul
    assert "progress_summary" in tools_md
    assert "format_diet_progress_summary" in tools_md
    assert "format_diet_progress_summary" in identity
    assert "format_diet_progress_summary" in soul
    assert "diet_coach_briefing" in tools_md
    assert "format_diet_coach_briefing" in tools_md
    assert "format_diet_coach_briefing" in identity
    assert "format_diet_coach_briefing" in soul
    assert "diet_daily_loop_generate" in tools_md
    assert "format_diet_daily_loop" in tools_md
    assert "format_diet_daily_loop" in identity
    assert "format_diet_daily_loop" in soul
    assert "diet_next_meal_card_generate" in tools_md
    assert "format_diet_next_meal_card" in tools_md
    assert "format_diet_next_meal_card" in identity
    assert "format_diet_next_meal_card" in soul
    assert "diet_day_review_plan_generate" in tools_md
    assert "format_diet_day_review_plan" in tools_md
    assert "format_diet_day_review_plan" in identity
    assert "format_diet_day_review_plan" in soul
    assert "diet_weekly_plan_generate" in tools_md
    assert "format_diet_weekly_plan" in tools_md
    assert "format_diet_weekly_plan" in identity
    assert "format_diet_weekly_plan" in soul
    assert "diet_inbox_triage" in tools_md
    assert "format_diet_inbox_triage" in tools_md
    assert "format_diet_inbox_triage" in identity
    assert "format_diet_inbox_triage" in soul
    assert "diet_inbox_commit" in tools_md


def test_shared_capability_agents_are_configured_without_entry_bindings() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    bindings = json.loads((ROOT / "config" / "bindings.json").read_text(encoding="utf-8"))["bindings"]

    capability_ids = {"repo-analyzer", "doc-writer", "planner", "reviewer"}
    by_id = {row["id"]: row for row in agents}
    assert capability_ids.issubset(by_id)
    for agent_id in capability_ids:
        prompt_dir = by_id[agent_id]["prompt_policy"]["prompt_dir"]
        assert (ROOT / "workspace" / prompt_dir / "IDENTITY.md").exists()
        assert (ROOT / "workspace" / prompt_dir / "SOUL.md").exists()
        assert not any(row["agent_id"] == agent_id for row in bindings)


def test_shared_capability_agents_have_task_specific_tool_boundaries() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    tools = {row["id"]: set(row["tool_policy"]["tool_names"]) for row in agents}

    assert {"github_repo_summary", "read_file", "list_directory", "web_search", "fetch_url"}.issubset(
        tools["repo-analyzer"]
    )
    assert "github_repo_gateway_fit" in tools["repo-analyzer"]
    assert "github_repo_risk_scan" in tools["repo-analyzer"]
    assert "github_repo_decision_card" in tools["repo-analyzer"]
    assert "format_github_repo_decision_card" in tools["repo-analyzer"]
    assert "compose_github_repo_analysis" in tools["repo-analyzer"]
    assert "format_github_repo_analysis" in tools["repo-analyzer"]
    assert "plan_github_repo_adoption" in tools["repo-analyzer"]
    assert "format_github_repo_adoption_plan" in tools["repo-analyzer"]
    assert {
        "read_file",
        "list_directory",
        "write_file",
        "outline_structured_document",
        "render_repo_analysis_markdown",
        "render_github_repo_risk_markdown",
        "render_research_evidence_markdown",
        "render_research_option_comparison_markdown",
        "render_execution_record_markdown",
        "render_release_gate_markdown",
        "render_research_option_validation_plan_markdown",
        "render_agent_collaboration_markdown",
        "render_agent_collaboration_progress_markdown",
        "render_collaboration_progress_gate_markdown",
        "render_agent_handoff_package_gate_markdown",
        "render_agent_collaboration_final_summary_markdown",
        "render_collaboration_final_summary_gate_markdown",
        "save_structured_document",
        "save_markdown_report",
    }.issubset(tools["doc-writer"])
    assert {"read_file", "list_directory", "write_file", "save_markdown_report"}.issubset(
        tools["planner"]
    )
    assert "save_task_plan" in tools["planner"]
    assert "structure_task_breakdown" in tools["planner"]
    assert "plan_execution_stage" in tools["planner"]
    assert "format_task_breakdown" in tools["planner"]
    assert "format_execution_stage_plan" in tools["planner"]
    assert "adapt_adoption_plan_to_task_plan" in tools["planner"]
    assert "compose_repo_review_task_plan" in tools["planner"]
    assert "compose_research_option_validation_plan" in tools["planner"]
    assert "adapt_collaboration_plan_to_task_plan" in tools["planner"]
    assert {"read_file", "list_directory", "save_markdown_report"}.issubset(
        tools["reviewer"]
    )
    assert "save_review_report" in tools["reviewer"]
    assert "assess_risk_decision" in tools["reviewer"]
    assert "format_risk_decision_assessment" in tools["reviewer"]
    assert "review_release_gate" in tools["reviewer"]
    assert "format_release_gate_review" in tools["reviewer"]
    assert "review_task_plan_gate" in tools["reviewer"]
    assert "format_task_plan_gate_review" in tools["reviewer"]
    assert "review_agent_collaboration_gate" in tools["reviewer"]
    assert "format_agent_collaboration_gate_review" in tools["reviewer"]
    assert "review_agent_handoff_package_gate" in tools["reviewer"]
    assert "format_agent_handoff_package_gate_review" in tools["reviewer"]
    assert "review_collaboration_progress_gate" in tools["reviewer"]
    assert "format_collaboration_progress_gate_review" in tools["reviewer"]
    assert "review_collaboration_final_summary_gate" in tools["reviewer"]
    assert "format_collaboration_final_summary_gate_review" in tools["reviewer"]
    assert "review_research_evidence_gate" in tools["reviewer"]
    assert "format_research_evidence_gate_review" in tools["reviewer"]
    assert "review_research_option_comparison_gate" in tools["reviewer"]
    assert "format_research_option_comparison_gate_review" in tools["reviewer"]
    assert "review_github_repo_risk_gate" in tools["reviewer"]
    assert "format_github_repo_risk_gate_review" in tools["reviewer"]
    assert "save_markdown_report" in tools["repo-analyzer"]
    assert "write_file" not in tools["reviewer"]
    assert "bash" not in tools["reviewer"]


def test_ops_agent_has_readonly_health_tool_and_safety_prompt() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    tools = {row["id"]: set(row["tool_policy"]["tool_names"]) for row in agents}
    identity = (ROOT / "workspace" / "agents" / "ops" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "ops" / "SOUL.md").read_text(encoding="utf-8")
    tools_md = (ROOT / "workspace" / "agents" / "ops" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "ops_readonly_health" in tools["ops"]
    assert "summarize_ops_health" in tools["ops"]
    assert "ops_runtime_diagnostics" in tools["ops"]
    assert "ops_troubleshooting_plan" in tools["ops"]
    assert "ops_readonly_health" in identity
    assert "ops_readonly_health" in soul
    assert "ops_readonly_health" in tools_md
    assert "summarize_ops_health" in identity
    assert "summarize_ops_health" in soul
    assert "summarize_ops_health" in tools_md
    assert "ops_runtime_diagnostics" in identity
    assert "ops_runtime_diagnostics" in soul
    assert "ops_runtime_diagnostics" in tools_md
    assert "ops_troubleshooting_plan" in identity
    assert "ops_troubleshooting_plan" in soul
    assert "ops_troubleshooting_plan" in tools_md
    assert "禁止执行删除" in tools_md


def test_planner_has_task_breakdown_tool_and_safety_prompt() -> None:
    identity = (ROOT / "workspace" / "agents" / "planner" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "planner" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (ROOT / "workspace" / "agents" / "planner" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "structure_task_breakdown" in identity
    assert "structure_task_breakdown" in soul
    assert "structure_task_breakdown" in tools_md
    assert "plan_execution_stage" in identity
    assert "plan_execution_stage" in soul
    assert "plan_execution_stage" in tools_md
    assert "format_task_breakdown" in identity
    assert "format_task_breakdown" in soul
    assert "format_task_breakdown" in tools_md
    assert "format_execution_stage_plan" in identity
    assert "format_execution_stage_plan" in soul
    assert "format_execution_stage_plan" in tools_md
    assert "不要直接输出原始 JSON" in identity
    assert "adapt_adoption_plan_to_task_plan" in identity
    assert "adapt_adoption_plan_to_task_plan" in soul
    assert "adapt_adoption_plan_to_task_plan" in tools_md
    assert "compose_repo_review_task_plan" in identity
    assert "compose_repo_review_task_plan" in soul
    assert "compose_repo_review_task_plan" in tools_md
    assert "task_plan_from_repo_review" in tools_md
    assert "compose_research_option_validation_plan" in identity
    assert "compose_research_option_validation_plan" in soul
    assert "compose_research_option_validation_plan" in tools_md
    assert "最小验证" in soul
    assert "adapt_collaboration_plan_to_task_plan" in identity
    assert "adapt_collaboration_plan_to_task_plan" in soul
    assert "adapt_collaboration_plan_to_task_plan" in tools_md
    assert "不自动调用任何 Agent" in tools_md
    assert "只做计划" in tools_md


def test_doc_writer_has_outline_tool_and_material_gap_prompt() -> None:
    identity = (ROOT / "workspace" / "agents" / "doc-writer" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "doc-writer" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (ROOT / "workspace" / "agents" / "doc-writer" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "outline_structured_document" in identity
    assert "outline_structured_document" in soul
    assert "outline_structured_document" in tools_md
    assert "render_repo_analysis_markdown" in identity
    assert "render_repo_analysis_markdown" in soul
    assert "render_repo_analysis_markdown" in tools_md
    assert "render_github_repo_risk_markdown" in identity
    assert "render_github_repo_risk_markdown" in soul
    assert "render_github_repo_risk_markdown" in tools_md
    assert "github_repo_risk_gate_review" in identity
    assert "github_repo_risk_gate_review" in soul
    assert "gate_review_json" in tools_md
    assert "render_research_evidence_markdown" in identity
    assert "render_research_evidence_markdown" in soul
    assert "render_research_evidence_markdown" in tools_md
    assert "render_research_option_comparison_markdown" in identity
    assert "render_research_option_comparison_markdown" in soul
    assert "render_research_option_comparison_markdown" in tools_md
    assert "research_option_comparison" in tools_md
    assert "render_research_option_validation_plan_markdown" in identity
    assert "render_research_option_validation_plan_markdown" in soul
    assert "render_research_option_validation_plan_markdown" in tools_md
    assert "task_plan_from_research_option_comparison" in tools_md
    assert "reports/research" in soul
    assert "render_execution_record_markdown" in identity
    assert "render_execution_record_markdown" in soul
    assert "render_execution_record_markdown" in tools_md
    assert "render_release_gate_markdown" in identity
    assert "render_release_gate_markdown" in soul
    assert "render_release_gate_markdown" in tools_md
    assert "release_gate_review" in tools_md
    assert "render_agent_collaboration_markdown" in identity
    assert "render_agent_collaboration_markdown" in soul
    assert "render_agent_collaboration_markdown" in tools_md
    assert "render_agent_collaboration_progress_markdown" in identity
    assert "render_agent_collaboration_progress_markdown" in soul
    assert "render_agent_collaboration_progress_markdown" in tools_md
    assert "agent_collaboration_progress" in tools_md
    assert "render_collaboration_progress_gate_markdown" in identity
    assert "render_collaboration_progress_gate_markdown" in soul
    assert "render_collaboration_progress_gate_markdown" in tools_md
    assert "collaboration_progress_gate_review" in tools_md
    assert "render_agent_handoff_package_gate_markdown" in identity
    assert "render_agent_handoff_package_gate_markdown" in soul
    assert "render_agent_handoff_package_gate_markdown" in tools_md
    assert "agent_handoff_package_gate_review" in tools_md
    assert "render_agent_collaboration_final_summary_markdown" in identity
    assert "render_agent_collaboration_final_summary_markdown" in soul
    assert "render_agent_collaboration_final_summary_markdown" in tools_md
    assert "agent_collaboration_final_summary" in tools_md
    assert "render_collaboration_final_summary_gate_markdown" in identity
    assert "render_collaboration_final_summary_gate_markdown" in soul
    assert "render_collaboration_final_summary_gate_markdown" in tools_md
    assert "collaboration_final_summary_gate_review" in tools_md
    assert "不代表任何 Agent 已经执行" in soul
    assert "材料不足" in tools_md


def test_repo_analyzer_has_gateway_fit_tool_and_prompt() -> None:
    identity = (ROOT / "workspace" / "agents" / "repo-analyzer" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "repo-analyzer" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (ROOT / "workspace" / "agents" / "repo-analyzer" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "github_repo_gateway_fit" in identity
    assert "github_repo_gateway_fit" in soul
    assert "github_repo_gateway_fit" in tools_md
    assert "github_repo_risk_scan" in identity
    assert "github_repo_risk_scan" in soul
    assert "github_repo_risk_scan" in tools_md
    assert "github_repo_decision_card" in identity
    assert "github_repo_decision_card" in soul
    assert "github_repo_decision_card" in tools_md
    assert "format_github_repo_decision_card" in identity
    assert "format_github_repo_decision_card" in soul
    assert "format_github_repo_decision_card" in tools_md
    assert "compose_github_repo_analysis" in identity
    assert "compose_github_repo_analysis" in soul
    assert "compose_github_repo_analysis" in tools_md
    assert "format_github_repo_analysis" in identity
    assert "format_github_repo_analysis" in soul
    assert "format_github_repo_analysis" in tools_md
    assert "plan_github_repo_adoption" in identity
    assert "plan_github_repo_adoption" in soul
    assert "plan_github_repo_adoption" in tools_md
    assert "format_github_repo_adoption_plan" in identity
    assert "format_github_repo_adoption_plan" in soul
    assert "format_github_repo_adoption_plan" in tools_md
    assert "github_repo_summary" in tools_md


def test_reviewer_has_risk_decision_tool_and_readonly_prompt() -> None:
    identity = (ROOT / "workspace" / "agents" / "reviewer" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )
    soul = (ROOT / "workspace" / "agents" / "reviewer" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (ROOT / "workspace" / "agents" / "reviewer" / "TOOLS.md").read_text(
        encoding="utf-8"
    )

    assert "assess_risk_decision" in identity
    assert "assess_risk_decision" in soul
    assert "assess_risk_decision" in tools_md
    assert "format_risk_decision_assessment" in identity
    assert "format_risk_decision_assessment" in soul
    assert "format_risk_decision_assessment" in tools_md
    assert "review_release_gate" in identity
    assert "review_release_gate" in soul
    assert "review_release_gate" in tools_md
    assert "format_release_gate_review" in identity
    assert "format_release_gate_review" in soul
    assert "format_release_gate_review" in tools_md
    assert "review_task_plan_gate" in identity
    assert "review_task_plan_gate" in soul
    assert "review_task_plan_gate" in tools_md
    assert "format_task_plan_gate_review" in identity
    assert "format_task_plan_gate_review" in soul
    assert "format_task_plan_gate_review" in tools_md
    assert "不要直接贴原始 JSON" in identity
    assert "task_plan_from_research_option_comparison" in soul
    assert "方案验证计划" in tools_md
    assert "review_agent_collaboration_gate" in identity
    assert "review_agent_collaboration_gate" in soul
    assert "review_agent_collaboration_gate" in tools_md
    assert "format_agent_collaboration_gate_review" in identity
    assert "format_agent_collaboration_gate_review" in soul
    assert "format_agent_collaboration_gate_review" in tools_md
    assert "路线门禁" in soul
    assert "review_agent_handoff_package_gate" in identity
    assert "review_agent_handoff_package_gate" in soul
    assert "review_agent_handoff_package_gate" in tools_md
    assert "format_agent_handoff_package_gate_review" in identity
    assert "format_agent_handoff_package_gate_review" in soul
    assert "format_agent_handoff_package_gate_review" in tools_md
    assert "交接包门禁" in soul
    assert "review_collaboration_progress_gate" in identity
    assert "review_collaboration_progress_gate" in soul
    assert "review_collaboration_progress_gate" in tools_md
    assert "format_collaboration_progress_gate_review" in identity
    assert "format_collaboration_progress_gate_review" in soul
    assert "format_collaboration_progress_gate_review" in tools_md
    assert "进度门禁" in soul
    assert "review_collaboration_final_summary_gate" in identity
    assert "review_collaboration_final_summary_gate" in soul
    assert "review_collaboration_final_summary_gate" in tools_md
    assert "format_collaboration_final_summary_gate_review" in identity
    assert "format_collaboration_final_summary_gate_review" in soul
    assert "format_collaboration_final_summary_gate_review" in tools_md
    assert "最终摘要门禁" in soul
    assert "review_research_evidence_gate" in identity
    assert "review_research_evidence_gate" in soul
    assert "review_research_evidence_gate" in tools_md
    assert "format_research_evidence_gate_review" in identity
    assert "format_research_evidence_gate_review" in soul
    assert "format_research_evidence_gate_review" in tools_md
    assert "证据复用门禁" in soul
    assert "review_research_option_comparison_gate" in identity
    assert "review_research_option_comparison_gate" in soul
    assert "review_research_option_comparison_gate" in tools_md
    assert "format_research_option_comparison_gate_review" in identity
    assert "format_research_option_comparison_gate_review" in soul
    assert "format_research_option_comparison_gate_review" in tools_md
    assert "方案对比门禁" in soul
    assert "review_github_repo_risk_gate" in identity
    assert "review_github_repo_risk_gate" in soul
    assert "review_github_repo_risk_gate" in tools_md
    assert "format_github_repo_risk_gate_review" in identity
    assert "format_github_repo_risk_gate_review" in soul
    assert "format_github_repo_risk_gate_review" in tools_md
    assert "仓库风险门禁" in soul
    assert "只读 Agent" in tools_md


def test_shared_capability_agents_document_handoff_inputs() -> None:
    required_terms = {
        "repo-analyzer": ["## 委派输入", "repo_url", "analysis_goal"],
        "doc-writer": ["## 委派输入", "document_type", "source_material"],
        "planner": ["## 委派输入", "goal", "constraints"],
        "reviewer": ["## 委派输入", "review_target", "risk_focus"],
    }

    for agent_id, terms in required_terms.items():
        identity = (ROOT / "workspace" / "agents" / agent_id / "IDENTITY.md").read_text(
            encoding="utf-8"
        )
        for term in terms:
            assert term in identity


def test_entry_agents_require_structured_handoff_prompt() -> None:
    for agent_id in ("feishu-entry", "wework-entry"):
        soul = (ROOT / "workspace" / "agents" / agent_id / "SOUL.md").read_text(
            encoding="utf-8"
        )
        tools_md = (ROOT / "workspace" / "agents" / agent_id / "TOOLS.md").read_text(
            encoding="utf-8"
        )
        assert "suggest_agent_delegation" in soul
        assert "list_agent_capabilities" in soul
        assert "build_agent_handoff_prompt" in soul
        assert "plan_agent_collaboration" in soul
        assert "handoff_prompt" in soul
        assert "用户原始目标" in soul
        assert "期望输出" in soul
        assert "不要声称目标 Agent 已经自动执行完成" in tools_md


def test_entry_agents_route_complex_repo_tasks_to_collaboration_plan() -> None:
    main_soul = (ROOT / "workspace" / "agents" / "main" / "SOUL.md").read_text(
        encoding="utf-8"
    )
    main_tools = (ROOT / "workspace" / "agents" / "main" / "TOOLS.md").read_text(
        encoding="utf-8"
    )
    assert "repo-adoption" in main_soul
    assert "research-option-validation" in main_soul
    assert "research-option-validation" in main_tools
    assert "requires_collaboration=true" in main_soul
    assert "requires_collaboration=true" in main_tools
    assert "collaboration_plan_json" in main_soul
    assert "collaboration_plan_json" in main_tools
    assert "explain_agent_route" in main_soul
    assert "explain_agent_route" in main_tools
    assert "prepare_entry_route_response" in main_soul
    assert "prepare_entry_route_response" in main_tools
    assert "repo-analyzer、reviewer、planner、doc-writer" in main_soul
    assert "repo-analyzer → reviewer → planner → doc-writer" in main_tools
    assert "research、reviewer、planner、reviewer、doc-writer" in main_soul
    assert "research → reviewer → planner → reviewer → doc-writer" in main_tools

    for agent_id in ("feishu-entry", "wework-entry"):
        soul = (ROOT / "workspace" / "agents" / agent_id / "SOUL.md").read_text(
            encoding="utf-8"
        )
        tools_md = (ROOT / "workspace" / "agents" / agent_id / "TOOLS.md").read_text(
            encoding="utf-8"
        )
        assert "repo-adoption" in soul
        assert "research-option-validation" in soul
        assert "research-option-validation" in tools_md
        assert "requires_collaboration=true" in soul
        assert "collaboration_plan_json" in soul
        assert "explain_agent_route" in soul
        assert "prepare_entry_route_response" in soul
        assert "repo-analyzer、reviewer、planner、doc-writer" in soul
        assert "research、reviewer、planner、reviewer、doc-writer" in soul


def test_platform_entry_agents_have_delegation_tool_only_at_entry_layer() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    tools = {row["id"]: set(row["tool_policy"]["tool_names"]) for row in agents}

    assert "suggest_agent_delegation" in tools["feishu-entry"]
    assert "suggest_agent_delegation" in tools["wework-entry"]
    assert "list_agent_capabilities" in tools["feishu-entry"]
    assert "list_agent_capabilities" in tools["wework-entry"]
    assert "format_agent_capability_catalog" in tools["feishu-entry"]
    assert "format_agent_capability_catalog" in tools["wework-entry"]
    assert "match_agent_capability" in tools["feishu-entry"]
    assert "match_agent_capability" in tools["wework-entry"]
    assert "format_agent_capability_match" in tools["feishu-entry"]
    assert "format_agent_capability_match" in tools["wework-entry"]
    assert "compose_agent_handoff_package" in tools["feishu-entry"]
    assert "compose_agent_handoff_package" in tools["wework-entry"]
    assert "format_agent_handoff_package" in tools["feishu-entry"]
    assert "format_agent_handoff_package" in tools["wework-entry"]
    assert "list_agent_collaboration_routes" in tools["feishu-entry"]
    assert "list_agent_collaboration_routes" in tools["wework-entry"]
    assert "build_agent_handoff_prompt" in tools["feishu-entry"]
    assert "build_agent_handoff_prompt" in tools["wework-entry"]
    assert "build_collaboration_stage_handoff" in tools["feishu-entry"]
    assert "build_collaboration_stage_handoff" in tools["wework-entry"]
    assert "summarize_collaboration_progress" in tools["feishu-entry"]
    assert "summarize_collaboration_progress" in tools["wework-entry"]
    assert "format_collaboration_progress" in tools["feishu-entry"]
    assert "format_collaboration_progress" in tools["wework-entry"]
    assert "format_collaboration_final_summary" in tools["feishu-entry"]
    assert "format_collaboration_final_summary" in tools["wework-entry"]
    assert "plan_agent_collaboration" in tools["feishu-entry"]
    assert "plan_agent_collaboration" in tools["wework-entry"]
    assert "explain_agent_route" in tools["feishu-entry"]
    assert "explain_agent_route" in tools["wework-entry"]
    assert "prepare_entry_route_response" in tools["feishu-entry"]
    assert "prepare_entry_route_response" in tools["wework-entry"]
    for agent_id in {
        "repo-analyzer",
        "doc-writer",
        "planner",
        "reviewer",
        SECRETARY_AGENT_ID,
        AGENT_ID,
    }:
        assert "suggest_agent_delegation" not in tools[agent_id]
        assert "list_agent_capabilities" not in tools[agent_id]
        assert "format_agent_capability_catalog" not in tools[agent_id]
        assert "match_agent_capability" not in tools[agent_id]
        assert "format_agent_capability_match" not in tools[agent_id]
        assert "compose_agent_handoff_package" not in tools[agent_id]
        assert "format_agent_handoff_package" not in tools[agent_id]
        assert "list_agent_collaboration_routes" not in tools[agent_id]
        assert "build_agent_handoff_prompt" not in tools[agent_id]
        assert "build_collaboration_stage_handoff" not in tools[agent_id]
        assert "summarize_collaboration_progress" not in tools[agent_id]
        assert "format_collaboration_progress" not in tools[agent_id]
        assert "compose_collaboration_final_summary" not in tools[agent_id]
        assert "format_collaboration_final_summary" not in tools[agent_id]
        assert "plan_agent_collaboration" not in tools[agent_id]
        assert "explain_agent_route" not in tools[agent_id]
        assert "prepare_entry_route_response" not in tools[agent_id]


def test_agent_capability_boundary_doc_covers_recent_capability_tools() -> None:
    content = (ROOT / "doc" / "Agent能力边界总览.md").read_text(encoding="utf-8")

    for term in [
        "compose_research_evidence_pack",
        "compose_research_option_comparison",
        "format_research_confidence_assessment",
        "render_github_repo_risk_markdown",
        "format_research_brief",
        "review_github_repo_risk_gate",
        "format_github_repo_risk_gate_review",
        "github_repo_decision_card",
        "format_github_repo_decision_card",
        "format_github_repo_analysis",
        "format_github_repo_adoption_plan",
        "compose_repo_review_task_plan",
        "compose_research_option_validation_plan",
        "render_research_evidence_markdown",
        "render_research_option_comparison_markdown",
        "render_research_option_validation_plan_markdown",
        "review_research_evidence_gate",
        "review_research_option_comparison_gate",
        "format_research_option_comparison_gate_review",
        "render_execution_record_markdown",
        "render_release_gate_markdown",
        "format_risk_decision_assessment",
        "format_release_gate_review",
        "format_task_breakdown",
        "format_execution_stage_plan",
        "format_task_plan_gate_review",
        "format_agent_collaboration_gate_review",
        "format_agent_handoff_package_gate_review",
        "format_collaboration_progress_gate_review",
        "format_collaboration_final_summary_gate_review",
        "format_research_evidence_gate_review",
        "render_agent_collaboration_markdown",
        "render_agent_collaboration_progress_markdown",
        "render_collaboration_progress_gate_markdown",
        "render_agent_handoff_package_gate_markdown",
        "render_agent_collaboration_final_summary_markdown",
        "render_collaboration_final_summary_gate_markdown",
        "review_agent_collaboration_gate",
        "review_agent_handoff_package_gate",
        "review_collaboration_progress_gate",
        "review_collaboration_final_summary_gate",
        "adapt_collaboration_plan_to_task_plan",
        "ops_runtime_diagnostics",
        "format_personal_todo_entry",
        "personal_todo_complete_by_title",
        "personal_todo_update_by_title",
        "format_personal_todo_update",
        "personal_todo_cancel_by_title",
        "format_personal_todo_cancellation",
        "format_personal_review_entry",
        "format_diet_profile_update",
        "diet_today_status",
        "format_diet_today_status",
        "format_meal_log_entry",
        "format_meal_log_list",
        "format_nutrition_day_summary",
        "format_diet_plan",
        "format_weight_log_entry",
        "format_diet_progress_summary",
        "format_diet_coach_briefing",
        "format_diet_daily_loop",
        "format_personal_todo_list",
        "format_personal_todo_completion",
        "format_personal_review_recent",
        "format_personal_inbox_commit",
        "format_memory_write",
        "format_memory_search",
        "format_personal_briefing",
        "format_personal_time_blocks",
        "format_personal_daily_workflow",
        "personal_focus_card_generate",
        "format_personal_focus_card",
        "personal_day_review_plan_generate",
        "format_personal_day_review_plan",
        "diet_next_meal_card_generate",
        "format_personal_weekly_plan",
        "format_diet_next_meal_card",
        "diet_day_review_plan_generate",
        "format_diet_day_review_plan",
        "format_diet_weekly_plan",
        "diet_inbox_triage",
        "format_diet_inbox_triage",
        "diet_inbox_commit",
        "format_diet_inbox_commit",
        "format_memory_write",
        "format_memory_search",
        "build_agent_handoff_prompt",
        "build_collaboration_stage_handoff",
        "summarize_collaboration_progress",
        "format_collaboration_progress",
        "compose_collaboration_final_summary",
        "format_collaboration_final_summary",
        "list_agent_capabilities",
        "format_agent_capability_catalog",
        "format_diet_profile",
        "match_agent_capability",
        "format_agent_capability_match",
        "compose_agent_handoff_package",
        "format_agent_handoff_package",
        "list_agent_collaboration_routes",
        "plan_agent_collaboration",
        "research-option-validation",
        "explain_agent_route",
        "prepare_entry_route_response",
    ]:
        assert term in content
    for agent_id in [
        "research",
        "repo-analyzer",
        "planner",
        "reviewer",
        "doc-writer",
        "personal-secretary-zhanghaibo",
        "diet-assistant-zhanghaibo",
        "ops",
    ]:
        assert agent_id in content


def test_personal_secretary_has_structured_personal_tools() -> None:
    agents = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))["agents"]
    tools = {row["id"]: set(row["tool_policy"]["tool_names"]) for row in agents}

    assert {
        "personal_todo_add",
        "format_personal_todo_entry",
        "personal_todo_list",
        "format_personal_todo_list",
        "personal_todo_complete",
        "personal_todo_complete_by_title",
        "format_personal_todo_completion",
        "personal_todo_update_by_title",
        "format_personal_todo_update",
        "personal_todo_cancel_by_title",
        "format_personal_todo_cancellation",
        "personal_review_add",
        "format_personal_review_entry",
        "personal_review_recent",
        "format_personal_review_recent",
        "personal_briefing_generate",
        "format_personal_briefing",
        "personal_time_blocks_generate",
        "format_personal_time_blocks",
        "personal_daily_workflow_generate",
        "format_personal_daily_workflow",
        "personal_focus_card_generate",
        "format_personal_focus_card",
        "personal_day_review_plan_generate",
        "format_personal_day_review_plan",
        "personal_weekly_plan_generate",
        "format_personal_weekly_plan",
        "personal_inbox_triage",
        "format_personal_inbox_triage",
        "personal_inbox_commit",
        "format_personal_inbox_commit",
        "format_memory_write",
        "format_memory_search",
    }.issubset(tools[SECRETARY_AGENT_ID])


def test_personal_secretary_has_time_block_prompt_and_tool_rules() -> None:
    identity = (
        ROOT / "workspace" / "agents" / SECRETARY_AGENT_ID / "IDENTITY.md"
    ).read_text(encoding="utf-8")
    soul = (ROOT / "workspace" / "agents" / SECRETARY_AGENT_ID / "SOUL.md").read_text(
        encoding="utf-8"
    )
    tools_md = (
        ROOT / "workspace" / "agents" / SECRETARY_AGENT_ID / "TOOLS.md"
    ).read_text(encoding="utf-8")

    assert "personal_todo_list" in identity
    assert "personal_todo_add" in identity
    assert "format_personal_todo_entry" in identity
    assert "format_personal_todo_entry" in soul
    assert "format_personal_todo_entry" in tools_md
    assert "format_personal_todo_list" in identity
    assert "format_personal_todo_list" in soul
    assert "format_personal_todo_list" in tools_md
    assert "personal_todo_complete" in identity
    assert "format_personal_todo_completion" in identity
    assert "format_personal_todo_completion" in soul
    assert "format_personal_todo_completion" in tools_md
    assert "personal_review_add" in identity
    assert "format_personal_review_entry" in identity
    assert "format_personal_review_entry" in soul
    assert "format_personal_review_entry" in tools_md
    assert "personal_review_recent" in identity
    assert "format_personal_review_recent" in identity
    assert "format_personal_review_recent" in soul
    assert "format_personal_review_recent" in tools_md
    assert "personal_briefing_generate" in identity
    assert "format_personal_briefing" in identity
    assert "format_personal_briefing" in soul
    assert "format_personal_briefing" in tools_md
    assert "personal_time_blocks_generate" in identity
    assert "format_personal_time_blocks" in identity
    assert "format_personal_time_blocks" in soul
    assert "format_personal_time_blocks" in tools_md
    assert "personal_time_blocks_generate" in soul
    assert "personal_time_blocks_generate" in tools_md
    assert "personal_daily_workflow_generate" in identity
    assert "personal_daily_workflow_generate" in soul
    assert "personal_daily_workflow_generate" in tools_md
    assert "format_personal_daily_workflow" in identity
    assert "format_personal_daily_workflow" in soul
    assert "format_personal_daily_workflow" in tools_md
    assert "personal_focus_card_generate" in identity
    assert "personal_focus_card_generate" in soul
    assert "personal_focus_card_generate" in tools_md
    assert "format_personal_focus_card" in identity
    assert "format_personal_focus_card" in soul
    assert "format_personal_focus_card" in tools_md
    assert "personal_day_review_plan_generate" in identity
    assert "personal_day_review_plan_generate" in soul
    assert "personal_day_review_plan_generate" in tools_md
    assert "format_personal_day_review_plan" in identity
    assert "format_personal_day_review_plan" in soul
    assert "format_personal_day_review_plan" in tools_md
    assert "personal_weekly_plan_generate" in identity
    assert "personal_weekly_plan_generate" in soul
    assert "personal_weekly_plan_generate" in tools_md
    assert "format_personal_weekly_plan" in identity
    assert "format_personal_weekly_plan" in soul
    assert "format_personal_weekly_plan" in tools_md
    assert "personal_inbox_triage" in identity
    assert "personal_inbox_triage" in soul
    assert "personal_inbox_triage" in tools_md
    assert "format_personal_inbox_triage" in identity
    assert "format_personal_inbox_triage" in soul
    assert "format_personal_inbox_triage" in tools_md
    assert "personal_inbox_commit" in identity
    assert "personal_inbox_commit" in soul
    assert "personal_inbox_commit" in tools_md
    assert "personal_todo_complete_by_title" in identity
    assert "personal_todo_complete_by_title" in soul
    assert "personal_todo_complete_by_title" in tools_md
    assert "personal_todo_update_by_title" in identity
    assert "personal_todo_update_by_title" in soul
    assert "personal_todo_update_by_title" in tools_md
    assert "format_personal_todo_update" in identity
    assert "format_personal_todo_update" in soul
    assert "format_personal_todo_update" in tools_md
    assert "personal_todo_cancel_by_title" in identity
    assert "personal_todo_cancel_by_title" in soul
    assert "personal_todo_cancel_by_title" in tools_md
    assert "format_personal_todo_cancellation" in identity
    assert "format_personal_todo_cancellation" in soul
    assert "format_personal_todo_cancellation" in tools_md
    assert "format_personal_inbox_commit" in identity
    assert "format_personal_inbox_commit" in soul
    assert "format_personal_inbox_commit" in tools_md
    assert "format_memory_write" in identity
    assert "format_memory_write" in soul
    assert "format_memory_write" in tools_md
    assert "format_memory_search" in identity
    assert "format_memory_search" in soul
    assert "format_memory_search" in tools_md
    assert "上午下午晚上" in tools_md
