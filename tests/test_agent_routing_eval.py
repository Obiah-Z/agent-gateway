from __future__ import annotations

import subprocess
import sys

from scripts.eval_agent_routing import DEFAULT_CASES, evaluate_cases


def test_default_routing_eval_cases_cover_key_agents() -> None:
    case_names = {case.name for case in DEFAULT_CASES}
    expected_names = {
        "simple-chat",
        "repo-analysis",
        "repo-reading-guide",
        "repo-adoption",
        "research-option-validation",
        "planning",
        "agent-capabilities",
        "agent-capability-contract",
        "report-artifacts",
        "ops",
        "diet",
        "personal",
        "personal-due-reminders",
        "document",
        "review",
    }

    assert expected_names.issubset(case_names)


def test_default_routing_eval_cases_pass() -> None:
    results = evaluate_cases()

    assert results
    assert all(row.passed for row in results)
    by_name = {row.name: row for row in results}
    assert by_name["repo-reading-guide"].actual_intent == "repo-reading-guide"
    assert by_name["repo-adoption"].actual_requires_collaboration is True
    assert by_name["personal"].actual_agent_id == "personal-secretary-zhanghaibo"
    assert by_name["personal-due-reminders"].actual_agent_id == "personal-secretary-zhanghaibo"
    assert by_name["diet"].actual_agent_id == "diet-assistant-zhanghaibo"
    assert by_name["agent-capability-contract"].actual_intent == "agent-capability-contract"
    assert by_name["agent-capability-contract"].actual_agent_id == "main"
    assert by_name["report-artifacts"].actual_intent == "report-artifacts"
    assert by_name["report-artifacts"].actual_agent_id == "main"
    assert all(not row.missing_required_tools for row in results)


def test_default_routing_eval_cases_declare_required_tools() -> None:
    missing = [case.name for case in DEFAULT_CASES if not case.required_tools]

    assert not missing


def test_default_routing_eval_cases_declare_risk_contracts() -> None:
    by_name = {case.name: case for case in DEFAULT_CASES}
    collaboration_cases = [
        case.name
        for case in DEFAULT_CASES
        if case.expected_requires_collaboration and case.collaboration_mode == "single-agent"
    ]
    write_without_confirmation = [
        case.name
        for case in DEFAULT_CASES
        if not case.read_only and case.name in {"diet", "personal"} and not case.requires_confirmation
    ]

    assert not collaboration_cases
    assert not write_without_confirmation
    assert by_name["repo-adoption"].collaboration_mode == "repo-adoption"
    assert by_name["research-option-validation"].collaboration_mode == "research-option-validation"
    assert by_name["diet"].read_only is False
    assert by_name["diet"].requires_confirmation is True
    assert by_name["personal"].read_only is False
    assert by_name["personal"].requires_confirmation is True
    assert by_name["personal-due-reminders"].read_only is True
    assert by_name["agent-capability-contract"].read_only is True
    assert by_name["report-artifacts"].read_only is True


def test_agent_routing_eval_cli_outputs_summary() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/eval_agent_routing.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Summary: 15/15 passed" in completed.stdout
    assert "repo-reading-guide" in completed.stdout
    assert "personal-due-reminders" in completed.stdout
    assert "agent-capability-contract" in completed.stdout
    assert "report-artifacts" in completed.stdout
    assert "risk" in completed.stdout
    assert "write+confirm" in completed.stdout
