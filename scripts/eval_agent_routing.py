from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_gateway.ai.agent_contracts import (
    DEFAULT_AGENT_ROUTING_CONTRACTS,
    AgentRoutingContract,
    load_agent_tool_allowlists,
)
from agent_gateway.ai.tools.builtin import register_builtin_tools
from agent_gateway.ai.tools.registry import ToolRegistry


@dataclass(frozen=True)
class RoutingResult:
    """单条路由验收结果。"""

    name: str
    passed: bool
    expected_intent: str
    actual_intent: str
    expected_agent_id: str
    actual_agent_id: str
    expected_requires_collaboration: bool
    actual_requires_collaboration: bool
    missing_required_tools: tuple[str, ...]
    read_only: bool
    requires_confirmation: bool
    collaboration_mode: str
    confidence: float
    reason: str
    suggested_next_step: str


DEFAULT_CASES = DEFAULT_AGENT_ROUTING_CONTRACTS


def evaluate_cases(
    cases: tuple[AgentRoutingContract, ...] = DEFAULT_CASES,
    *,
    workspace_root: Path | None = None,
    agent_config_path: Path = Path("config/agents.json"),
) -> list[RoutingResult]:
    """执行入口 Agent 路由验收用例。"""

    registry = ToolRegistry()
    register_builtin_tools(registry, workspace_root or Path("workspace"))
    agent_tools = load_agent_tool_allowlists(agent_config_path)
    results: list[RoutingResult] = []
    for case in cases:
        raw = registry.dispatch(
            "classify_task_intent",
            {"user_text": case.user_text, "context_hint": case.context_hint},
        )
        data = json.loads(raw)
        actual_intent = str(data.get("intent") or "")
        actual_agent_id = str(data.get("recommended_agent_id") or "")
        actual_requires = bool(data.get("requires_collaboration"))
        allowlist = agent_tools.get(case.expected_agent_id, set())
        missing_tools = tuple(tool for tool in case.required_tools if tool not in allowlist)
        passed = (
            actual_intent == case.expected_intent
            and actual_agent_id == case.expected_agent_id
            and actual_requires == case.expected_requires_collaboration
            and not missing_tools
        )
        results.append(
            RoutingResult(
                name=case.name,
                passed=passed,
                expected_intent=case.expected_intent,
                actual_intent=actual_intent,
                expected_agent_id=case.expected_agent_id,
                actual_agent_id=actual_agent_id,
                expected_requires_collaboration=case.expected_requires_collaboration,
                actual_requires_collaboration=actual_requires,
                missing_required_tools=missing_tools,
                read_only=case.read_only,
                requires_confirmation=case.requires_confirmation,
                collaboration_mode=case.collaboration_mode,
                confidence=float(data.get("confidence") or 0.0),
                reason=str(data.get("reason") or ""),
                suggested_next_step=str(data.get("suggested_next_step") or ""),
            )
        )
    return results


def _format_table(results: list[RoutingResult]) -> str:
    headers = ["case", "status", "intent", "agent", "collab", "tools", "risk", "confidence"]
    rows = [
        [
            row.name,
            "PASS" if row.passed else "FAIL",
            f"{row.actual_intent} / expected {row.expected_intent}",
            f"{row.actual_agent_id} / expected {row.expected_agent_id}",
            f"{row.actual_requires_collaboration} / expected {row.expected_requires_collaboration}",
            "ok" if not row.missing_required_tools else ", ".join(row.missing_required_tools),
            _format_risk_contract(row),
            f"{row.confidence:.2f}",
        ]
        for row in results
    ]
    widths = [
        max(len(str(value)) for value in [header, *[row[index] for row in rows]])
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return "\n".join(lines)


def _format_risk_contract(row: RoutingResult) -> str:
    parts = ["read-only" if row.read_only else "write"]
    if row.requires_confirmation:
        parts.append("confirm")
    if row.collaboration_mode != "single-agent":
        parts.append(row.collaboration_mode)
    return "+".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate entry-agent routing cases.")
    parser.add_argument("--workspace", default="workspace", help="Workspace root used by builtin tools.")
    parser.add_argument(
        "--agents-config",
        default="config/agents.json",
        help="Agent config used to verify required tool allowlists.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args(argv)

    results = evaluate_cases(
        workspace_root=Path(args.workspace),
        agent_config_path=Path(args.agents_config),
    )
    if args.json:
        print(json.dumps([asdict(row) for row in results], ensure_ascii=False, indent=2))
    else:
        print(_format_table(results))
        passed = sum(1 for row in results if row.passed)
        print(f"\nSummary: {passed}/{len(results)} passed")
    return 0 if all(row.passed for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
