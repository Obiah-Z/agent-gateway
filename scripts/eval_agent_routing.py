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
    ),
    RoutingCase(
        name="repo-analysis",
        user_text="帮我分析一下这个仓库 https://github.com/example/repo",
        expected_intent="repo-analysis",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="repo-reading-guide",
        user_text="这个仓库 https://github.com/example/repo 我应该先看哪些文件，从哪里读起？",
        expected_intent="repo-reading-guide",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="repo-adoption",
        user_text="分析这个 GitHub 仓库 https://github.com/example/repo，评估风险，并给出采纳计划和正式报告",
        expected_intent="repo-adoption",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=True,
    ),
    RoutingCase(
        name="research-option-validation",
        user_text="帮我做 RabbitMQ、Redis、Kafka 的技术选型和方案对比，输出验证计划、风险审查和正式报告",
        expected_intent="research-option-validation",
        expected_agent_id="research",
        expected_requires_collaboration=True,
    ),
    RoutingCase(
        name="planning",
        user_text="帮我规划一下下一阶段任务，拆成阶段和验收标准",
        expected_intent="planning",
        expected_agent_id="planner",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="agent-capabilities",
        user_text="当前系统有哪些 Agent？每个 Agent 能做什么？",
        expected_intent="agent-capabilities",
        expected_agent_id="main",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="ops",
        user_text="帮我看一下 Docker 容器和 Redis、RabbitMQ、PostgreSQL 的运行状态",
        expected_intent="ops",
        expected_agent_id="ops",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="diet",
        user_text="今天早餐吃了鸡蛋和牛奶，帮我记录一下饮食",
        expected_intent="diet",
        expected_agent_id="diet-assistant-zhanghaibo",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="personal",
        user_text="提醒我明天上午继续背项目八股，并做一次复盘",
        expected_intent="personal",
        expected_agent_id="personal-secretary-zhanghaibo",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="document",
        user_text="把这段材料整理成 Markdown 报告",
        expected_intent="document",
        expected_agent_id="doc-writer",
        expected_requires_collaboration=False,
    ),
    RoutingCase(
        name="review",
        user_text="帮我审查这个方案是否合理，有哪些风险和隐患",
        expected_intent="review",
        expected_agent_id="reviewer",
        expected_requires_collaboration=False,
    ),
)


def evaluate_cases(
    cases: tuple[RoutingCase, ...] = DEFAULT_CASES,
    *,
    workspace_root: Path | None = None,
) -> list[RoutingResult]:
    """执行入口 Agent 路由验收用例。"""

    registry = ToolRegistry()
    register_builtin_tools(registry, workspace_root or Path("workspace"))
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
        passed = (
            actual_intent == case.expected_intent
            and actual_agent_id == case.expected_agent_id
            and actual_requires == case.expected_requires_collaboration
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
                confidence=float(data.get("confidence") or 0.0),
                reason=str(data.get("reason") or ""),
                suggested_next_step=str(data.get("suggested_next_step") or ""),
            )
        )
    return results


def _format_table(results: list[RoutingResult]) -> str:
    headers = ["case", "status", "intent", "agent", "collab", "confidence"]
    rows = [
        [
            row.name,
            "PASS" if row.passed else "FAIL",
            f"{row.actual_intent} / expected {row.expected_intent}",
            f"{row.actual_agent_id} / expected {row.expected_agent_id}",
            f"{row.actual_requires_collaboration} / expected {row.expected_requires_collaboration}",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate entry-agent routing cases.")
    parser.add_argument("--workspace", default="workspace", help="Workspace root used by builtin tools.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args(argv)

    results = evaluate_cases(workspace_root=Path(args.workspace))
    if args.json:
        print(json.dumps([asdict(row) for row in results], ensure_ascii=False, indent=2))
    else:
        print(_format_table(results))
        passed = sum(1 for row in results if row.passed)
        print(f"\nSummary: {passed}/{len(results)} passed")
    return 0 if all(row.passed for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
