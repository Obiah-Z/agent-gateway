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
        "ops",
        "diet",
        "personal",
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
    assert by_name["diet"].actual_agent_id == "diet-assistant-zhanghaibo"


def test_agent_routing_eval_cli_outputs_summary() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/eval_agent_routing.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Summary: 12/12 passed" in completed.stdout
    assert "repo-reading-guide" in completed.stdout
