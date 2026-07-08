from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_gateway.ai.tools.builtin import register_builtin_tools
from agent_gateway.ai.tools.registry import ToolRegistry


@dataclass(frozen=True)
class RoutingCase:
    """入口 Agent 路由验收用例。"""

    name: str
    user_text: str
    expected_intent: str
    expected_agent_id: str
    expected_requires_collaboration: bool
    context_hint: str = ""
    required_tools: tuple[str, ...] = ()
    read_only: bool = True
    requires_confirmation: bool = False
    collaboration_mode: str = "single-agent"


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


DEFAULT_CASES: tuple[RoutingCase, ...] = (
    RoutingCase(
        name="simple-chat",
        user_text="你好，简单介绍一下你能做什么",
        expected_intent="chat",
        expected_agent_id="main",
        expected_requires_collaboration=False,
        required_tools=("classify_task_intent", "format_entry_response"),
    ),
    RoutingCase(
        name="repo-analysis",
        user_text="帮我分析一下这个仓库 https://github.com/Obiah-Z/agent-gateway",
        expected_intent="repo-analysis",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
        required_tools=("compose_github_repo_analysis", "format_github_repo_analysis"),
    ),
    RoutingCase(
        name="repo-reading-guide",
        user_text="这个仓库 https://github.com/Obiah-Z/agent-gateway 我应该先看哪些文件，从哪里读起？",
        expected_intent="repo-reading-guide",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
        required_tools=("github_repo_reading_guide", "format_github_repo_reading_guide"),
    ),
    RoutingCase(
        name="repo-adoption",
        user_text="分析这个 GitHub 仓库 https://github.com/Obiah-Z/agent-gateway，评估风险，并给出采纳计划和正式报告",
        expected_intent="repo-adoption",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=True,
        required_tools=("plan_github_repo_adoption", "format_github_repo_adoption_plan"),
        collaboration_mode="repo-adoption",
    ),
    RoutingCase(
        name="research-option-validation",
        user_text="帮我做 RabbitMQ、Redis、Kafka 的技术选型和方案对比，输出验证计划、风险审查和正式报告",
        expected_intent="research-option-validation",
        expected_agent_id="research",
        expected_requires_collaboration=True,
        required_tools=("compose_research_option_comparison",),
        collaboration_mode="research-option-validation",
    ),
    RoutingCase(
        name="planning",
        user_text="帮我规划一下下一阶段任务，拆成阶段和验收标准",
        expected_intent="planning",
        expected_agent_id="planner",
        expected_requires_collaboration=False,
        required_tools=("plan_execution_stage", "format_execution_stage_plan"),
    ),
    RoutingCase(
        name="agent-capabilities",
        user_text="当前系统有哪些 Agent？每个 Agent 能做什么？",
        expected_intent="agent-capabilities",
        expected_agent_id="main",
        expected_requires_collaboration=False,
        required_tools=("list_agent_capabilities", "format_agent_capability_catalog"),
    ),
    RoutingCase(
        name="ops",
        user_text="帮我看一下 Docker 容器和 Redis、RabbitMQ、PostgreSQL 的运行状态",
        expected_intent="ops",
        expected_agent_id="ops",
        expected_requires_collaboration=False,
        required_tools=("ops_readonly_health", "ops_runtime_diagnostics"),
    ),
    RoutingCase(
        name="diet",
        user_text="今天早餐吃了鸡蛋和牛奶，帮我记录一下饮食",
        expected_intent="diet",
        expected_agent_id="diet-assistant-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("meal_log_add", "format_meal_log_entry"),
        read_only=False,
        requires_confirmation=True,
    ),
    RoutingCase(
        name="personal",
        user_text="提醒我明天上午继续背项目八股，并做一次复盘",
        expected_intent="personal",
        expected_agent_id="personal-secretary-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("personal_todo_add", "personal_review_add"),
        read_only=False,
        requires_confirmation=True,
    ),
    RoutingCase(
        name="personal-due-reminders",
        user_text="今天有哪些到期提醒和逾期待办？",
        expected_intent="personal",
        expected_agent_id="personal-secretary-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("personal_due_todo_digest_generate", "format_personal_due_todo_digest"),
    ),
    RoutingCase(
        name="document",
        user_text="把这段材料整理成 Markdown 报告",
        expected_intent="document",
        expected_agent_id="doc-writer",
        expected_requires_collaboration=False,
        required_tools=("outline_structured_document", "save_structured_document"),
        read_only=False,
        requires_confirmation=False,
    ),
    RoutingCase(
        name="review",
        user_text="帮我审查这个方案是否合理，有哪些风险和隐患",
        expected_intent="review",
        expected_agent_id="reviewer",
        expected_requires_collaboration=False,
        required_tools=("assess_risk_decision", "format_risk_decision_assessment"),
    ),
)


def _agent_tool_allowlists(config_path: Path) -> dict[str, set[str]]:
    """读取 Agent 工具白名单，用于验证路由结果是否具备执行能力。"""

    data = json.loads(config_path.read_text(encoding="utf-8"))
    agents = data.get("agents", [])
    return {
        str(agent.get("id") or ""): set(agent.get("tool_policy", {}).get("tool_names", []))
        for agent in agents
        if agent.get("id")
    }


def evaluate_cases(
    cases: tuple[RoutingCase, ...] = DEFAULT_CASES,
    *,
    workspace_root: Path | None = None,
    agent_config_path: Path = Path("config/agents.json"),
) -> list[RoutingResult]:
    """执行入口 Agent 路由验收用例。"""

    registry = ToolRegistry()
    register_builtin_tools(registry, workspace_root or Path("workspace"))
    agent_tools = _agent_tool_allowlists(agent_config_path)
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
