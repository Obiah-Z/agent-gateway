import json
from pathlib import Path

from agent_gateway.ai.tools.builtin import register_builtin_tools
from agent_gateway.ai.tools.registry import ToolRegistry


def test_write_file_accepts_path_alias(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "write_file",
        {"path": "reports/example.md", "content": "hello"},
    )

    assert result == "Wrote 5 chars to reports/example.md"
    assert (tmp_path / "reports" / "example.md").read_text(encoding="utf-8") == "hello"


def test_write_file_maps_host_workspace_absolute_path(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "write_file",
        {
            "file_path": (
                "/home/obiah/Desktop/claw0/gateway/workspace/"
                "reports/github-repos/仓库分析-demo.md"
            ),
            "content": "report",
        },
    )

    assert result == "Wrote 6 chars to reports/github-repos/仓库分析-demo.md"
    assert (
        tmp_path / "reports" / "github-repos" / "仓库分析-demo.md"
    ).read_text(encoding="utf-8") == "report"


def test_save_markdown_report_writes_report_path_for_channel_attachment(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_markdown_report",
        {
            "title": "仓库分析 demo/repo",
            "category": "github-repos",
            "file_name": "仓库分析-demo-repo",
            "content": "## 结论\n\n这是测试报告。",
        },
    )

    assert result == "报告路径：workspace/reports/github-repos/仓库分析-demo-repo.md"
    assert (
        tmp_path / "reports" / "github-repos" / "仓库分析-demo-repo.md"
    ).read_text(encoding="utf-8").startswith("# 仓库分析 demo/repo\n\n## 结论")


def test_save_markdown_report_sanitizes_unsafe_filename(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_markdown_report",
        {
            "title": "计划: A/B?",
            "category": "plans",
            "content": "# 已有标题\n\n内容",
        },
    )

    assert result == "报告路径：workspace/reports/plans/计划-A-B.md"
    assert (tmp_path / "reports" / "plans" / "计划-A-B.md").read_text(
        encoding="utf-8"
    ) == "# 已有标题\n\n内容"


def test_save_task_plan_writes_structured_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_task_plan",
        {
            "title": "Agent 能力增强计划",
            "goal": "增强共享 Agent 的工具能力",
            "scope": "不做多 Agent 自动协作",
            "phases": [
                {
                    "name": "阶段一",
                    "task": "补工具",
                    "output": "工具 schema",
                    "done": "测试通过",
                }
            ],
            "risks": ["工具权限过宽"],
            "next_steps": ["先跑单测"],
        },
    )

    assert result == "报告路径：workspace/reports/plans/Agent-能力增强计划.md"
    content = (tmp_path / "reports" / "plans" / "Agent-能力增强计划.md").read_text(
        encoding="utf-8"
    )
    assert "| 阶段一 | 补工具 | 工具 schema | 测试通过 |" in content
    assert "## 下一步\n- 先跑单测" in content


def test_structure_task_breakdown_reports_gaps_and_next_steps(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "structure_task_breakdown",
        {
            "goal": "增强 Agent 能力",
            "scope": "先不做自动多 Agent 调度",
            "current_state": "已有入口 Agent 和能力 Agent",
            "constraints": ["每阶段提交一次"],
            "risks": ["工具权限过宽"],
            "phases": [
                {
                    "name": "阶段一",
                    "task": "补结构化工具",
                    "output": "工具 schema 和测试",
                    "done": "测试通过",
                },
                {
                    "name": "阶段二",
                    "task": "更新提示词",
                },
            ],
        },
    )

    data = json.loads(result)
    assert data["type"] == "task_breakdown"
    assert data["readiness"] == "needs_refinement"
    assert data["phases"][0] == {
        "name": "阶段一",
        "task": "补结构化工具",
        "output": "工具 schema 和测试",
        "done": "测试通过",
    }
    assert data["phases"][1]["output"] == "待明确"
    assert data["gaps"]["missing_outputs"] == ["阶段二"]
    assert data["gaps"]["missing_acceptance"] == ["阶段二"]
    assert data["next_steps"][0] == "先执行「阶段一」：补结构化工具"


def test_plan_execution_stage_outputs_engineering_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "plan_execution_stage",
        {
            "objective": "增强 repo-analyzer 的报告产出能力",
            "current_state": "已有 summary 和 fit 工具",
            "scope": "只新增结构化分析工具，不做自动多 Agent 调度",
            "dependencies": ["github_repo_summary", "github_repo_gateway_fit"],
            "risks": ["输出结构过宽导致 Agent 难以落盘"],
            "acceptance_checks": ["pytest tests/test_github_repo_tools.py -q"],
            "next_actions": ["新增工具", "更新提示词", "运行测试并提交"],
        },
    )

    data = json.loads(result)
    assert data["type"] == "execution_stage_plan"
    assert data["objective"] == "增强 repo-analyzer 的报告产出能力"
    assert data["readiness"] == "ready"
    assert data["dependencies"] == ["github_repo_summary", "github_repo_gateway_fit"]
    assert data["acceptance_checks"] == ["pytest tests/test_github_repo_tools.py -q"]
    assert data["commit_strategy"] == "每完成一个可验证小阶段提交一次"
    assert data["next_actions"][0] == "新增工具"


def test_plan_execution_stage_reports_missing_acceptance_checks(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "plan_execution_stage",
            {"objective": "整理 Agent 能力边界"},
        )
    )

    assert data["readiness"] == "needs_refinement"
    assert "缺少 acceptance_checks。" in data["gaps"]
    assert data["acceptance_checks"] == ["补充可执行测试或人工验收标准。"]


def test_format_task_breakdown_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    breakdown = registry.dispatch(
        "structure_task_breakdown",
        {
            "goal": "增强 Agent 能力",
            "scope": "先补 Planner 表达层",
            "phases": [
                {
                    "name": "阶段一",
                    "task": "新增格式化工具",
                    "output": "中文摘要",
                    "done": "测试通过",
                }
            ],
        },
    )

    summary = registry.dispatch(
        "format_task_breakdown",
        {"breakdown_json": breakdown},
    )

    assert "## 计划摘要" in summary
    assert "- 状态：可执行" in summary
    assert "| 阶段一 | 新增格式化工具 | 中文摘要 | 测试通过 |" in summary
    assert "## 下一步" in summary


def test_format_execution_stage_plan_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    plan = registry.dispatch(
        "plan_execution_stage",
        {
            "objective": "补 Planner 用户可读摘要",
            "scope": "只新增格式化工具",
            "dependencies": ["structure_task_breakdown"],
            "risks": ["原始 JSON 直接暴露"],
            "acceptance_checks": ["pytest tests/test_builtin_tools.py -q"],
            "next_actions": ["新增工具", "更新提示词"],
        },
    )

    summary = registry.dispatch(
        "format_execution_stage_plan",
        {"plan_json": plan},
    )

    assert "## 小阶段执行计划" in summary
    assert "- 状态：可执行" in summary
    assert "- 目标：补 Planner 用户可读摘要" in summary
    assert "## 验收检查" in summary
    assert "- pytest tests/test_builtin_tools.py -q" in summary


def test_adapt_adoption_plan_to_task_plan_outputs_save_args(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    adoption_plan = {
        "type": "github_repo_adoption_plan",
        "repository": "demo/workflow",
        "url": "https://github.com/demo/workflow",
        "adoption_goal": "采纳 workflow 模板优化 Gateway 主动任务。",
        "decision": {"action": "pilot", "reason": "先做轻量原型。"},
        "stages": [
            {
                "id": "stage-1",
                "title": "证据复核",
                "objective": "确认 README、许可证和关键目录。",
                "tasks": ["复核 README", "确认许可证"],
            },
            {
                "id": "stage-2",
                "title": "落地验证",
                "objective": "实现最小 Cron workflow 原型。",
                "tasks": ["拆一个最小实验", "补充测试"],
            },
        ],
        "risk_gates": ["确认许可证允许学习、引用或复用。"],
        "acceptance_checks": ["形成一份可追溯的证据摘要。"],
    }

    data = json.loads(
        registry.dispatch(
            "adapt_adoption_plan_to_task_plan",
            {
                "adoption_plan_json": json.dumps(adoption_plan, ensure_ascii=False),
                "title": "workflow 采纳计划",
            },
        )
    )

    assert data["type"] == "task_plan_from_adoption"
    assert data["repository"] == "demo/workflow"
    assert data["decision"]["action"] == "pilot"
    assert data["phases"][0]["name"] == "证据复核"
    assert data["phases"][1]["task"] == "拆一个最小实验；补充测试"
    assert data["risks"] == ["确认许可证允许学习、引用或复用。"]
    assert data["save_task_plan_args"]["title"] == "workflow 采纳计划"

    saved = registry.dispatch("save_task_plan", data["save_task_plan_args"])
    assert saved == "报告路径：workspace/reports/plans/workflow-采纳计划.md"
    assert (tmp_path / "reports" / "plans" / "workflow-采纳计划.md").exists()


def test_compose_repo_review_task_plan_blocks_no_go_risk_gate(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/risky",
        "url": "https://github.com/demo/risky",
        "analysis_goal": "评估是否可复用提示词模板。",
        "gateway_fit": {"score": 80, "priority": "high", "signals": ["包含 Skill 模板。"]},
        "gateway_reuse_ideas": ["参考 Skill 分类和提示词结构。"],
        "risks": ["许可证缺失或未识别。"],
        "recommendations": ["先复核许可证。"],
    }
    risk_gate = {
        "type": "github_repo_risk_gate_review",
        "review_target": "demo/risky",
        "decision": "no-go",
        "source_decision": "hold",
        "checklist": [],
        "next_actions": ["人工复核 LICENSE、README 授权说明或联系作者后再复用。"],
    }

    data = json.loads(
        registry.dispatch(
            "compose_repo_review_task_plan",
            {
                "repo_analysis_json": json.dumps(analysis, ensure_ascii=False),
                "risk_gate_json": json.dumps(risk_gate, ensure_ascii=False),
                "title": "risky 仓库采纳计划",
            },
        )
    )

    assert data["type"] == "task_plan_from_repo_review"
    assert data["repository"] == "demo/risky"
    assert data["decision"]["risk_gate"] == "no-go"
    assert data["decision"]["recommended_action"] == "hold"
    assert data["phases"][1]["name"] == "阻塞项处理"
    assert data["save_task_plan_args"]["title"] == "risky 仓库采纳计划"

    saved = registry.dispatch("save_task_plan", data["save_task_plan_args"])
    assert saved == "报告路径：workspace/reports/plans/risky-仓库采纳计划.md"
    content = (tmp_path / "reports" / "plans" / "risky-仓库采纳计划.md").read_text(
        encoding="utf-8"
    )
    assert "阻塞项处理" in content


def test_compose_research_option_validation_plan_uses_gate_decision(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    comparison = {
        "type": "research_option_comparison",
        "topic": "入站队列中间件选型",
        "decision_question": "Gateway 分布式入站削峰优先选择 RabbitMQ 还是 Redis？",
        "criteria": ["可靠投递", "削峰能力", "会话串行协同"],
        "recommended_option": "RabbitMQ",
        "options": [{"name": "RabbitMQ"}, {"name": "Redis"}],
        "next_actions": ["用压测验证 RabbitMQ 入站削峰能力。"],
        "uncertainty": ["热点 session 仍需 Redis 协调。"],
    }
    gate = {
        "type": "research_option_comparison_gate_review",
        "decision": "conditional-go",
        "next_actions": ["只进入最小验证，不直接生产化。"],
    }

    data = json.loads(
        registry.dispatch(
            "compose_research_option_validation_plan",
            {
                "comparison_json": json.dumps(comparison, ensure_ascii=False),
                "gate_review_json": json.dumps(gate, ensure_ascii=False),
            },
        )
    )

    assert data["type"] == "task_plan_from_research_option_comparison"
    assert data["decision"]["recommended_option"] == "RabbitMQ"
    assert data["decision"]["recommended_action"] == "pilot"
    assert data["decision"]["gate"] == "conditional-go"
    assert data["phases"][1]["name"] == "最小验证设计"
    assert data["save_task_plan_args"]["phases"][1]["name"] == "最小验证设计"
    assert "只进入最小验证，不直接做生产化改造。" in data["next_steps"]
    assert "热点 session 仍需 Redis 协调。" in data["risks"]


def test_render_research_option_validation_plan_markdown_formats_plan(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "task_plan_from_research_option_comparison",
        "title": "RabbitMQ 方案验证计划",
        "goal": "验证 RabbitMQ 是否适合 Gateway 入站削峰。",
        "scope": "只做最小验证，不直接生产化。",
        "decision": {
            "gate": "conditional-go",
            "recommended_option": "RabbitMQ",
            "recommended_action": "pilot",
        },
        "criteria": ["可靠投递", "削峰能力"],
        "candidate_options": ["RabbitMQ", "Redis"],
        "phases": [
            {
                "name": "证据与门禁复核",
                "task": "复核方案对比和门禁。",
                "output": "未决问题清单。",
                "done": "阻塞项已确认。",
            },
            {
                "name": "最小验证设计",
                "task": "设计 RabbitMQ 最小实验。",
                "output": "实验范围和回滚方式。",
                "done": "可独立验证关键假设。",
            },
        ],
        "risks": ["热点 session 仍需 Redis 协调。"],
        "next_steps": ["只进入最小验证，不直接做生产化改造。"],
    }

    markdown = registry.dispatch(
        "render_research_option_validation_plan_markdown",
        {"task_plan_json": json.dumps(plan, ensure_ascii=False)},
    )

    assert markdown.startswith("# RabbitMQ 方案验证计划")
    assert "推荐方案：RabbitMQ" in markdown
    assert "门禁结论：conditional-go" in markdown
    assert "## 候选方案" in markdown
    assert "- RabbitMQ" in markdown
    assert "| 证据与门禁复核 | 复核方案对比和门禁。" in markdown
    assert "热点 session 仍需 Redis 协调。" in markdown


def test_adapt_collaboration_plan_to_task_plan_outputs_staged_handoffs(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    collaboration_plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "user_goal": "评估仓库并沉淀采纳方案。",
        "expected_output": "仓库分析、执行计划和正式报告。",
        "should_persist": True,
        "constraints": ["只规划路线，不自动调用任何 Agent。"],
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库价值和风险。",
                "input_contract": {"user_goal": "评估仓库。"},
                "expected_output": "github_repo_analysis JSON。",
            },
            {
                "step": 2,
                "agent_id": "doc-writer",
                "purpose": "整理成正式 Markdown。",
                "input_contract": {"upstream_result": "github_repo_analysis JSON。"},
                "expected_output": "Markdown 报告。",
            },
        ],
        "next_actions": ["先把第一阶段 handoff_prompt 交给 repo-analyzer。"],
    }

    data = json.loads(
        registry.dispatch(
            "adapt_collaboration_plan_to_task_plan",
            {
                "collaboration_json": json.dumps(collaboration_plan, ensure_ascii=False),
                "title": "仓库评估协作计划",
            },
        )
    )

    assert data["type"] == "task_plan_from_collaboration"
    assert data["title"] == "仓库评估协作计划"
    assert data["goal"] == "评估仓库并沉淀采纳方案。"
    assert data["phases"][0]["name"] == "阶段 1：repo-analyzer"
    assert data["phases"][0]["task"] == "分析仓库价值和风险。；输入依据：评估仓库。"
    assert data["phases"][1]["output"] == "Markdown 报告。"
    assert data["risks"] == ["只规划路线，不自动调用任何 Agent。"]
    assert data["save_task_plan_args"]["phases"][1]["name"] == "阶段 2：doc-writer"

    saved = registry.dispatch("save_task_plan", data["save_task_plan_args"])
    assert saved == "报告路径：workspace/reports/plans/仓库评估协作计划.md"
    assert (tmp_path / "reports" / "plans" / "仓库评估协作计划.md").exists()


def test_save_review_report_writes_structured_review(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_review_report",
        {
            "title": "工具权限审查",
            "conclusion": "有条件通过",
            "findings": [
                {
                    "severity": "中",
                    "issue": "权限偏宽",
                    "impact": "可能误写文件",
                    "suggestion": "改用专用工具",
                }
            ],
            "test_gaps": ["缺少失败路径测试"],
            "residual_risks": ["模型仍可能选择通用工具"],
        },
    )

    assert result == "报告路径：workspace/reports/reviews/工具权限审查.md"
    content = (tmp_path / "reports" / "reviews" / "工具权限审查.md").read_text(
        encoding="utf-8"
    )
    assert "| 中 | 权限偏宽 | 可能误写文件 | 改用专用工具 |" in content
    assert "## 残余风险\n- 模型仍可能选择通用工具" in content


def test_review_release_gate_allows_low_risk_change(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "review_release_gate",
        {
            "change_summary": "新增 doc-writer 仓库分析 Markdown 渲染工具。",
            "risk_items": [
                {
                    "severity": "low",
                    "issue": "Markdown 章节可能不完整",
                    "status": "mitigated",
                    "mitigation": "已补充渲染和落盘测试。",
                }
            ],
            "test_evidence": ["pytest tests/test_builtin_tools.py -q"],
            "rollback_plan": "回滚本次工具和配置提交。",
        },
    )

    data = json.loads(result)
    assert data["type"] == "release_gate_review"
    assert data["decision"] == "go"
    assert all(item["passed"] for item in data["checklist"])
    assert data["next_actions"] == ["保留本次门禁记录，按计划推进。"]


def test_review_release_gate_blocks_open_critical_risk(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "review_release_gate",
            {
                "change_summary": "切换入站任务调度实现。",
                "risk_items": [
                    {
                        "severity": "critical",
                        "issue": "同一 session 可能并发执行",
                        "status": "open",
                        "mitigation": "补 session lane 互斥测试。",
                    }
                ],
                "test_evidence": ["pytest tests/test_task_worker.py -q"],
                "unresolved_items": ["缺少 worker 崩溃恢复验证"],
            },
        )
    )

    assert data["decision"] == "no-go"
    assert any(item["passed"] is False for item in data["checklist"])
    assert "缺少 worker 崩溃恢复验证" in data["next_actions"]
    assert "补 session lane 互斥测试。" in data["next_actions"]


def test_format_release_gate_review_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    review = registry.dispatch(
        "review_release_gate",
        {
            "change_summary": "切换入站任务调度实现。",
            "risk_items": [
                {
                    "severity": "critical",
                    "issue": "同一 session 可能并发执行",
                    "status": "open",
                    "mitigation": "补 session lane 互斥测试。",
                }
            ],
            "test_evidence": ["pytest tests/test_task_worker.py -q"],
            "unresolved_items": ["缺少 worker 崩溃恢复验证"],
        },
    )

    formatted = registry.dispatch(
        "format_release_gate_review",
        {"gate_review_json": review},
    )

    assert "## 发布门禁审查" in formatted
    assert "- 结论：不建议继续" in formatted
    assert "- 变更摘要：切换入站任务调度实现。" in formatted
    assert "- 测试证据：1 条" in formatted
    assert "- 未决项：1 条" in formatted
    assert "- 回滚方案：未说明" in formatted
    assert "| 无未解决阻塞项 | 未通过 | 缺少 worker 崩溃恢复验证 |" in formatted
    assert "| 严重 | open | 同一 session 可能并发执行 | 补 session lane 互斥测试。 |" in formatted
    assert "- 补充回滚或恢复方案。" in formatted


def test_review_task_plan_gate_allows_complete_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "task_plan_from_adoption",
        "title": "workflow 采纳计划",
        "goal": "采纳 workflow 模板优化 Gateway 主动任务。",
        "scope": "只做最小原型，不修改生产配置。",
        "phases": [
            {
                "name": "证据复核",
                "task": "复核 README 和许可证",
                "output": "证据摘要",
                "done": "证据摘要已落盘",
            }
        ],
        "risks": ["许可证需要人工确认。"],
        "next_steps": ["pytest tests/test_builtin_tools.py -q"],
    }

    data = json.loads(
        registry.dispatch(
            "review_task_plan_gate",
            {"plan_json": json.dumps(plan, ensure_ascii=False)},
        )
    )

    assert data["type"] == "task_plan_gate_review"
    assert data["decision"] == "go"
    assert all(item["passed"] for item in data["checklist"])
    assert data["next_actions"] == ["计划具备进入执行的基本条件，执行前保留审查记录。"]


def test_review_task_plan_gate_blocks_under_specified_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "title": "模糊计划",
        "goal": "",
        "phases": [{"name": "阶段一", "task": "做一下"}],
    }

    data = json.loads(
        registry.dispatch(
            "review_task_plan_gate",
            {"plan_json": json.dumps(plan, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "no-go"
    assert len([item for item in data["checklist"] if not item["passed"]]) >= 3
    assert "补充 scope，明确做什么和不做什么。" in data["next_actions"]
    assert "为每个阶段补齐完成标准。" in data["next_actions"]


def test_review_task_plan_gate_blocks_no_go_research_validation_plan(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "task_plan_from_research_option_comparison",
        "title": "RabbitMQ 方案验证计划",
        "goal": "验证 RabbitMQ 是否适合 Gateway 入站削峰。",
        "scope": "只做最小验证。",
        "decision": {
            "gate": "no-go",
            "recommended_option": "RabbitMQ",
            "recommended_action": "pilot",
        },
        "criteria": ["可靠投递"],
        "candidate_options": ["RabbitMQ", "Redis"],
        "phases": [
            {
                "name": "最小验证设计",
                "task": "设计实验。",
                "output": "实验设计。",
                "done": "回滚路径明确。",
            }
        ],
        "risks": ["门禁未通过。"],
        "next_steps": ["pytest tests/test_builtin_tools.py -q"],
    }

    data = json.loads(
        registry.dispatch(
            "review_task_plan_gate",
            {"plan_json": json.dumps(plan, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "conditional-go"
    failed_items = [item["item"] for item in data["checklist"] if not item["passed"]]
    assert "方案验证门禁已通过或有条件通过" in failed_items
    assert "执行动作限制合理" in failed_items
    assert "先让 reviewer 审查 research_option_comparison，并处理 no-go 阻塞项。" in data[
        "next_actions"
    ]


def test_format_task_plan_gate_review_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "title": "模糊计划",
        "goal": "",
        "scope": "",
        "phases": [{"name": "阶段一", "task": ""}],
        "risks": [],
        "next_steps": [],
    }
    review = registry.dispatch(
        "review_task_plan_gate",
        {"plan_json": json.dumps(plan, ensure_ascii=False)},
    )

    summary = registry.dispatch(
        "format_task_plan_gate_review",
        {"gate_review_json": review},
    )

    assert "## 计划门禁审查" in summary
    assert "- 结论：不建议继续" in summary
    assert "| 边界已说明 | 未通过 | 缺少 scope / 不做事项。 |" in summary
    assert "## 下一步" in summary
    assert "- 补充 scope，明确做什么和不做什么。" in summary
    assert "这是计划进入执行前的门禁审查" in summary


def test_review_agent_collaboration_gate_allows_complete_route(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "user_goal": "评估仓库并沉淀采纳方案。",
        "expected_output": "结构化仓库分析、采纳计划和正式 Markdown。",
        "constraints": ["不自动执行目标 Agent。"],
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库。",
                "input_contract": {"user_goal": "评估仓库。"},
                "expected_output": "github_repo_analysis JSON。",
            },
            {
                "step": 2,
                "agent_id": "doc-writer",
                "purpose": "整理正式文档。",
                "input_contract": {"upstream_result": "github_repo_analysis JSON。"},
                "expected_output": "Markdown 报告。",
            },
        ],
        "next_actions": ["当前工具只生成协作路线，不会自动调用任何 Agent。"],
        "note": "这是多 Agent 协作路线规划，不代表任何 Agent 已经执行。",
    }

    data = json.loads(
        registry.dispatch(
            "review_agent_collaboration_gate",
            {"collaboration_json": json.dumps(plan, ensure_ascii=False)},
        )
    )

    assert data["type"] == "collaboration_gate_review"
    assert data["decision"] == "go"
    assert data["agents"] == ["repo-analyzer", "doc-writer"]
    assert all(item["passed"] for item in data["checklist"])
    assert data["next_actions"] == ["协作路线具备交接条件，执行前仍需逐阶段保留产物。"]


def test_review_agent_collaboration_gate_blocks_missing_contracts(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "user_goal": "评估仓库。",
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库。",
            }
        ],
    }

    data = json.loads(
        registry.dispatch(
            "review_agent_collaboration_gate",
            {"collaboration_json": json.dumps(plan, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "no-go"
    assert len([item for item in data["checklist"] if not item["passed"]]) >= 3
    assert "为每个阶段补充 input_contract，明确上游结果和必要输入。" in data["next_actions"]
    assert "明确说明该结果只生成协作路线，不代表任何 Agent 已经执行。" in data["next_actions"]


def test_format_agent_collaboration_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "user_goal": "评估仓库并沉淀采纳方案。",
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库。",
            }
        ],
    }
    review = registry.dispatch(
        "review_agent_collaboration_gate",
        {"collaboration_json": json.dumps(plan, ensure_ascii=False)},
    )

    summary = registry.dispatch(
        "format_agent_collaboration_gate_review",
        {"gate_review_json": review},
    )

    assert "## 协作路线门禁审查" in summary
    assert "- 结论：不建议继续" in summary
    assert "- 参与 Agent：repo-analyzer" in summary
    assert "| 交接输入契约完整 | 未通过 | 缺少输入契约阶段数：1 |" in summary
    assert "## 下一步" in summary
    assert "- 为每个阶段补充 input_contract，明确上游结果和必要输入。" in summary


def test_review_agent_handoff_package_gate_allows_complete_package(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    package = {
        "type": "agent_handoff_package",
        "user_goal": "把已有材料整理成 Markdown 报告",
        "target_agent_id": "doc-writer",
        "confidence": 0.71,
        "match": {"type": "agent_capability_match"},
        "handoff_prompt": "\n".join(
            [
                "目标 Agent：doc-writer",
                "",
                "## 用户原始目标",
                "把已有材料整理成 Markdown 报告",
                "",
                "## 关键上下文",
                "目标与该 Agent 的职责命中。",
                "",
                "## 已知约束",
                "- 只整理已有材料",
                "",
                "## 期望输出",
                "Markdown 报告",
            ]
        ),
        "delegation_suggestion": {
            "type": "agent_delegation_suggestion",
            "target_agent_id": "doc-writer",
            "confidence": 0.71,
        },
        "next_actions": ["不要声称目标 Agent 已经自动执行。"],
        "boundary": "这是入口层交接包，不代表目标 Agent 已经自动执行。",
    }

    data = json.loads(
        registry.dispatch(
            "review_agent_handoff_package_gate",
            {"package_json": json.dumps(package, ensure_ascii=False)},
        )
    )

    assert data["type"] == "agent_handoff_package_gate_review"
    assert data["decision"] == "go"
    assert all(item["passed"] for item in data["checklist"])
    assert data["review_target"] == "doc-writer"
    assert data["next_actions"] == ["交接包具备交给目标 Agent 的条件，执行前仍需保留原始用户目标。"]


def test_review_agent_handoff_package_gate_blocks_incomplete_package(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    package = {
        "type": "agent_handoff_package",
        "target_agent_id": "doc-writer",
        "handoff_prompt": "目标 Agent：doc-writer",
        "delegation_suggestion": {"target_agent_id": "planner"},
    }

    data = json.loads(
        registry.dispatch(
            "review_agent_handoff_package_gate",
            {"package_json": json.dumps(package, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "no-go"
    failed = {item["item"] for item in data["checklist"] if not item["passed"]}
    assert "交接目标明确" in failed
    assert "用户目标明确" in failed
    assert "交接提示结构完整" in failed
    assert "推荐依据可追溯" in failed
    assert "补充 target_agent_id" in data["next_actions"][0]


def test_format_agent_handoff_package_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    package = {
        "type": "agent_handoff_package",
        "target_agent_id": "doc-writer",
        "handoff_prompt": "目标 Agent：doc-writer",
        "delegation_suggestion": {"target_agent_id": "planner"},
    }
    review = registry.dispatch(
        "review_agent_handoff_package_gate",
        {"package_json": json.dumps(package, ensure_ascii=False)},
    )

    summary = registry.dispatch(
        "format_agent_handoff_package_gate_review",
        {"gate_review_json": review},
    )

    assert "## Agent 交接包门禁审查" in summary
    assert "- 结论：不建议继续" in summary
    assert "- 目标 Agent：doc-writer" in summary
    assert "| 交接目标明确 | 未通过 | target=doc-writer；delegation_target=planner |" in summary
    assert "## 下一步" in summary
    assert "- 补充 target_agent_id，并确保 delegation_suggestion.target_agent_id 与之一致。" in summary


def test_review_collaboration_progress_gate_allows_next_stage(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {
            "step": 3,
            "agent_id": "planner",
            "purpose": "把方案对比和门禁结论转成最小验证计划。",
            "expected_output": "task_plan_from_research_option_comparison JSON。",
        },
        "stages": [
            {"step": 1, "agent_id": "research", "status": "completed"},
            {"step": 2, "agent_id": "reviewer", "status": "completed"},
            {"step": 3, "agent_id": "planner", "status": "next"},
            {"step": 4, "agent_id": "reviewer", "status": "pending"},
        ],
        "next_handoff_args": {
            "stage": 3,
            "upstream_result_summary": "reviewer 已给出 conditional-go。",
        },
        "boundary": "这是协作进度摘要，不代表任何 Agent 已经自动执行。",
    }

    data = json.loads(
        registry.dispatch(
            "review_collaboration_progress_gate",
            {"progress_json": json.dumps(progress, ensure_ascii=False)},
        )
    )

    assert data["type"] == "collaboration_progress_gate_review"
    assert data["decision"] == "go"
    assert data["next_stage"]["agent_id"] == "planner"
    assert all(item["passed"] for item in data["checklist"])
    assert data["next_actions"] == ["协作进度具备进入下一阶段交接的条件。"]


def test_review_collaboration_progress_gate_blocks_missing_handoff_context(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {"step": 3, "agent_id": "planner"},
        "stages": [
            {"step": 1, "agent_id": "research", "status": "completed"},
            {"step": 2, "agent_id": "reviewer", "status": "pending"},
            {"step": 3, "agent_id": "planner", "status": "next"},
            {"step": 4, "agent_id": "reviewer", "status": "completed"},
        ],
        "next_handoff_args": {"stage": 2},
    }

    data = json.loads(
        registry.dispatch(
            "review_collaboration_progress_gate",
            {"progress_json": json.dumps(progress, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "no-go"
    failed_items = [item["item"] for item in data["checklist"] if not item["passed"]]
    assert "阶段状态连续" in failed_items
    assert "下一阶段 handoff 参数可用" in failed_items
    assert "上游结果可追溯" in failed_items
    assert "修正 stages 状态，确保已完成阶段连续且最多只有一个 next。" in data["next_actions"]
    assert "补充 next_handoff_args，并确保 stage 与 next_stage.step 一致。" in data[
        "next_actions"
    ]


def test_format_collaboration_progress_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {"step": 3, "agent_id": "planner"},
        "stages": [
            {"step": 1, "agent_id": "research", "status": "completed"},
            {"step": 2, "agent_id": "reviewer", "status": "pending"},
            {"step": 3, "agent_id": "planner", "status": "next"},
            {"step": 4, "agent_id": "reviewer", "status": "completed"},
        ],
        "next_handoff_args": {"stage": 2},
    }
    review = registry.dispatch(
        "review_collaboration_progress_gate",
        {"progress_json": json.dumps(progress, ensure_ascii=False)},
    )

    summary = registry.dispatch(
        "format_collaboration_progress_gate_review",
        {"gate_review_json": review},
    )

    assert "## 协作进度门禁审查" in summary
    assert "- 结论：不建议继续" in summary
    assert "- 当前进度：2/5" in summary
    assert "- 下一阶段：第 3 阶段 / planner" in summary
    assert "| 阶段状态连续 | 未通过 | 异常阶段数：2；next 标记数：1。 |" in summary
    assert "- 修正 stages 状态，确保已完成阶段连续且最多只有一个 next。" in summary


def test_review_collaboration_final_summary_gate_allows_complete_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "user_goal": "分析仓库并给出采纳建议。",
        "status": "completed",
        "completed_stage_count": 2,
        "total_stage_count": 2,
        "final_conclusion": "建议有条件采纳，先复核许可证。",
        "stage_summaries": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "status": "completed",
                "output_summary": "仓库适配度较高。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
        ],
        "unresolved_items": ["许可证仍需人工确认。"],
        "next_actions": ["把许可证复核作为实施前置门槛。"],
        "boundary": "这是入口层对多 Agent 协作结果的最终摘要，不代表重新执行任何 Agent。",
    }

    data = json.loads(
        registry.dispatch(
            "review_collaboration_final_summary_gate",
            {"summary_json": json.dumps(summary, ensure_ascii=False)},
        )
    )

    assert data["type"] == "collaboration_final_summary_gate_review"
    assert data["decision"] == "go"
    assert data["completed_stage_count"] == 2
    assert data["total_stage_count"] == 2
    assert all(item["passed"] for item in data["checklist"])
    assert "doc-writer" in data["next_actions"][0]


def test_review_collaboration_final_summary_gate_blocks_incomplete_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "status": "in-progress",
        "completed_stage_count": 1,
        "total_stage_count": 2,
        "final_conclusion": "缺少可直接展示给用户的最终结论。",
        "stage_summaries": [
            {"step": 1, "agent_id": "repo-analyzer", "status": "completed"}
        ],
        "next_actions": [],
        "boundary": "",
    }

    data = json.loads(
        registry.dispatch(
            "review_collaboration_final_summary_gate",
            {"summary_json": json.dumps(summary, ensure_ascii=False)},
        )
    )

    assert data["decision"] == "no-go"
    failed_items = [item["item"] for item in data["checklist"] if not item["passed"]]
    assert "最终结论明确" in failed_items
    assert "完成状态一致" in failed_items
    assert "未自动执行声明明确" in failed_items


def test_format_collaboration_final_summary_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "status": "in-progress",
        "completed_stage_count": 1,
        "total_stage_count": 2,
        "final_conclusion": "缺少可直接展示给用户的最终结论。",
        "stage_summaries": [
            {"step": 1, "agent_id": "repo-analyzer", "status": "completed"}
        ],
        "next_actions": [],
        "boundary": "",
    }
    review = registry.dispatch(
        "review_collaboration_final_summary_gate",
        {"summary_json": json.dumps(summary, ensure_ascii=False)},
    )

    formatted = registry.dispatch(
        "format_collaboration_final_summary_gate_review",
        {"gate_review_json": review},
    )

    assert "## 协作最终摘要门禁审查" in formatted
    assert "- 结论：不建议继续" in formatted
    assert "- 阶段覆盖：1/2" in formatted
    assert "| 最终结论明确 | 未通过 | 缺少可直接展示给用户的最终结论。 |" in formatted
    assert "## 下一步" in formatted
    assert "- 补充可直接给用户看的 final_conclusion，不要只写占位说明。" in formatted


def test_review_research_evidence_gate_allows_verified_pack(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    evidence = {
        "type": "research_evidence_pack",
        "topic": "Redis 看门狗",
        "research_question": "是否适合 Gateway session lane？",
        "conclusion": "可作为长任务锁续期候选。",
        "evidence_quality": "strong",
        "primary_source_count": 1,
        "sources": [
            {
                "title": "Redisson Docs",
                "url": "https://redisson.org/docs/data-and-services/locks-and-synchronizers/",
                "source_type": "docs",
                "fact": "Redisson watchdog 会自动续期锁。",
            },
            {
                "title": "Redis SET",
                "url": "https://redis.io/docs/latest/commands/set/",
                "source_type": "docs",
                "fact": "锁需要唯一值和过期时间。",
            },
        ],
        "key_facts": ["锁续期适合模型调用耗时不可预测场景。"],
        "source_conflicts": [],
        "uncertainty": [],
        "freshness": "2026-07-07 检索。",
    }

    data = json.loads(
        registry.dispatch(
            "review_research_evidence_gate",
            {"evidence_json": json.dumps(evidence, ensure_ascii=False), "time_sensitive": True},
        )
    )

    assert data["type"] == "research_evidence_gate_review"
    assert data["decision"] == "go"
    assert data["source_count"] == 2
    assert data["primary_source_count"] == 1
    assert all(item["passed"] for item in data["checklist"])
    assert data["next_actions"] == ["证据包可交给 doc-writer、planner 或 reviewer 继续复用。"]


def test_review_research_evidence_gate_blocks_weak_pack(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    evidence = {
        "type": "research_evidence_pack",
        "topic": "未知方案",
        "research_question": "能不能用？",
        "conclusion": "",
        "evidence_quality": "missing",
        "sources": [{"title": "没有链接的来源", "source_type": "blog", "fact": "只是一句描述。"}],
        "key_facts": [],
        "uncertainty": ["缺少来源。"],
    }

    data = json.loads(
        registry.dispatch(
            "review_research_evidence_gate",
            {
                "evidence_json": json.dumps(evidence, ensure_ascii=False),
                "min_sources": 2,
                "require_primary_source": True,
                "time_sensitive": True,
            },
        )
    )

    assert data["decision"] == "no-go"
    assert len([item for item in data["checklist"] if not item["passed"]]) >= 3
    assert "为每个来源补充可访问 URL。" in data["next_actions"]
    assert "补充官方文档、论文或一手资料来源。" in data["next_actions"]
    assert "补充检索日期、发布时间或最后更新时间。" in data["next_actions"]


def test_format_research_evidence_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    evidence = {
        "type": "research_evidence_pack",
        "topic": "未知方案",
        "research_question": "能不能用？",
        "conclusion": "",
        "evidence_quality": "missing",
        "sources": [{"title": "没有链接的来源", "source_type": "blog", "fact": "只是一句描述。"}],
        "key_facts": [],
        "uncertainty": ["缺少来源。"],
    }
    review = registry.dispatch(
        "review_research_evidence_gate",
        {
            "evidence_json": json.dumps(evidence, ensure_ascii=False),
            "min_sources": 2,
            "require_primary_source": True,
            "time_sensitive": True,
        },
    )

    formatted = registry.dispatch(
        "format_research_evidence_gate_review",
        {"gate_review_json": review},
    )

    assert "## Research 证据门禁审查" in formatted
    assert "- 结论：不建议继续" in formatted
    assert "- 证据质量：missing" in formatted
    assert "- 来源数量：1" in formatted
    assert "| 来源 URL 可核验 | 未通过 | 可核验 URL 数量：0。 |" in formatted
    assert "- 为每个来源补充可访问 URL。" in formatted


def test_review_research_option_comparison_gate_allows_complete_comparison(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    comparison = {
        "type": "research_option_comparison",
        "topic": "入站队列中间件选型",
        "decision_question": "Gateway 分布式入站削峰优先选择 RabbitMQ 还是 Redis？",
        "criteria": ["可靠投递", "削峰能力", "会话串行协同"],
        "recommended_option": "RabbitMQ",
        "evidence_quality": "strong",
        "options": [
            {
                "name": "RabbitMQ",
                "score": 86,
                "strengths": ["确认机制成熟"],
                "weaknesses": ["会话串行仍需协调层"],
                "evidence": ["官方文档覆盖 ack 和 durable queues。"],
            },
            {
                "name": "Redis",
                "score": 72,
                "strengths": ["轻量协调"],
                "weaknesses": ["消息队列语义不足"],
                "evidence": ["官方文档覆盖分布式锁模式。"],
            },
        ],
        "sources": [
            {
                "title": "RabbitMQ docs",
                "url": "https://www.rabbitmq.com/docs",
                "source_type": "official",
                "fact": "RabbitMQ documents acknowledgements and durable queues.",
            },
            {
                "title": "Redis distributed locks",
                "url": "https://redis.io/docs/latest/develop/use/patterns/distributed-locks/",
                "source_type": "docs",
                "fact": "Redis documents distributed lock patterns.",
            },
        ],
        "uncertainty": [],
    }

    data = json.loads(
        registry.dispatch(
            "review_research_option_comparison_gate",
            {"comparison_json": json.dumps(comparison, ensure_ascii=False)},
        )
    )

    assert data["type"] == "research_option_comparison_gate_review"
    assert data["decision"] == "go"
    assert data["recommended_option"] == "RabbitMQ"
    assert data["option_count"] == 2
    assert data["source_count"] == 2
    assert data["primary_source_count"] == 2
    assert all(item["passed"] for item in data["checklist"])


def test_format_research_option_comparison_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    comparison = {
        "type": "research_option_comparison",
        "topic": "缓存选型",
        "decision_question": "是否采用 Redis 作为分布式协调组件？",
        "criteria": [],
        "recommended_option": "",
        "evidence_quality": "missing",
        "options": [{"name": "Redis"}],
        "sources": [{"title": "一篇博客", "source_type": "blog", "fact": "只说 Redis 很快。"}],
        "uncertainty": [],
    }
    review = registry.dispatch(
        "review_research_option_comparison_gate",
        {
            "comparison_json": json.dumps(comparison, ensure_ascii=False),
            "min_options": 2,
            "min_sources": 2,
            "require_primary_source": True,
            "require_recommendation": True,
        },
    )

    formatted = registry.dispatch(
        "format_research_option_comparison_gate_review",
        {"gate_review_json": review},
    )

    assert "## Research 方案对比门禁审查" in formatted
    assert "- 结论：不建议继续" in formatted
    assert "- 推荐方案：未说明" in formatted
    assert "- 候选方案数量：1" in formatted
    assert "- 来源数量：1" in formatted
    assert "| 评价维度已列出 | 未通过 | 缺少 criteria。 |" in formatted
    assert "| 推荐方案已说明 | 未通过 | 缺少 recommended_option。 |" in formatted
    assert "- 补充推荐方案和推荐理由。" in formatted


def test_review_github_repo_risk_gate_blocks_unclear_license(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    risk_scan = {
        "type": "github_repo_risk_scan",
        "repository": "demo/risky",
        "intended_use": "复用提示词模板",
        "risk_level": "high",
        "decision": "hold",
        "risk_items": [
            {
                "severity": "high",
                "area": "license",
                "issue": "许可证缺失或未识别。",
                "impact": "复用边界不清晰。",
                "mitigation": "复用前人工确认 LICENSE。",
            }
        ],
        "summary": {
            "license": "unknown",
            "archived": False,
            "open_issues": 1,
            "stars": 20,
        },
        "next_actions": ["复核许可证和 README。"],
    }

    data = json.loads(
        registry.dispatch(
            "review_github_repo_risk_gate",
            {"risk_scan_json": json.dumps(risk_scan, ensure_ascii=False)},
        )
    )

    assert data["type"] == "github_repo_risk_gate_review"
    assert data["review_target"] == "demo/risky"
    assert data["decision"] == "no-go"
    assert data["source_decision"] == "hold"
    assert any(not item["passed"] for item in data["checklist"])
    assert "人工复核 LICENSE、README 授权说明或联系作者后再复用。" in data["next_actions"]


def test_format_github_repo_risk_gate_review_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    risk_scan = {
        "type": "github_repo_risk_scan",
        "repository": "demo/risky",
        "intended_use": "复用提示词模板",
        "risk_level": "high",
        "decision": "hold",
        "risk_items": [
            {
                "severity": "high",
                "area": "license",
                "issue": "许可证缺失或未识别。",
                "impact": "复用边界不清晰。",
                "mitigation": "复用前人工确认 LICENSE。",
            }
        ],
        "summary": {
            "license": "unknown",
            "archived": False,
        },
        "next_actions": ["复核许可证和 README。"],
    }
    review = registry.dispatch(
        "review_github_repo_risk_gate",
        {"risk_scan_json": json.dumps(risk_scan, ensure_ascii=False)},
    )

    formatted = registry.dispatch(
        "format_github_repo_risk_gate_review",
        {"gate_review_json": review},
    )

    assert "## GitHub 仓库风险门禁审查" in formatted
    assert "- 结论：不建议继续" in formatted
    assert "- 审查对象：demo/risky" in formatted
    assert "- 预期用途：复用提示词模板" in formatted
    assert "| 许可证风险可接受 | 未通过 | unknown |" in formatted
    assert "| 高 | license | 许可证缺失或未识别。 | 复用前人工确认 LICENSE。 |" in formatted
    assert "- 人工复核 LICENSE、README 授权说明或联系作者后再复用。" in formatted


def test_format_github_repo_decision_card_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    decision_card = {
        "type": "github_repo_decision_card",
        "repository": "demo/workflow",
        "url": "https://github.com/demo/workflow",
        "decision_goal": "判断是否适合 Gateway 借鉴",
        "decision": "deep-dive",
        "decision_label": "值得深入分析",
        "reason": "仓库与 Gateway 适配信号较强，适合进入深入分析或小实验。",
        "fit": {
            "score": 80,
            "priority": "high",
            "signals": ["包含 Agent / Skill / Tool / Workflow 相关信号。"],
        },
        "risk": {
            "level": "low",
            "decision": "pass",
            "items": [],
        },
        "repo_snapshot": {
            "language": "Markdown",
            "stars": 2000,
            "license": "MIT",
            "archived": False,
        },
        "reuse_ideas": ["参考工作流或调度模式，改进 Cron / 主动任务的表达方式。"],
        "next_actions": ["建议生成正式仓库分析报告，并列出可迁移到 Gateway 的小任务。"],
        "note": "这是仓库轻量决策卡片，不代表已经完成正式分析、风险门禁或采纳计划。",
    }

    formatted = registry.dispatch(
        "format_github_repo_decision_card",
        {"decision_card_json": json.dumps(decision_card, ensure_ascii=False)},
    )

    assert "## GitHub 仓库快速判断" in formatted
    assert "| 仓库 | demo/workflow |" in formatted
    assert "| 结论 | 值得深入分析 |" in formatted
    assert "| 适配分 | 80 |" in formatted
    assert "## 判断理由" in formatted
    assert "- 包含 Agent / Skill / Tool / Workflow 相关信号。" in formatted
    assert "- 参考工作流或调度模式，改进 Cron / 主动任务的表达方式。" in formatted
    assert "> 边界：这是仓库轻量决策卡片，不代表已经完成正式分析、风险门禁或采纳计划。" in formatted


def test_save_structured_document_writes_technical_report(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_structured_document",
        {
            "title": "Redis 技术栈分析",
            "document_type": "technical-report",
            "summary": "说明 Redis 在系统中的作用。",
            "background": "Gateway 已接入 Redis。",
            "content": "Redis 负责幂等、限流和 session lane。",
            "conclusions": ["Redis 适合做轻量协调层"],
            "risks": ["不能把 Redis 当长期主存储"],
            "next_steps": ["补充容量基线"],
        },
    )

    assert result == "报告路径：workspace/reports/technical-reports/Redis-技术栈分析.md"
    content = (
        tmp_path / "reports" / "technical-reports" / "Redis-技术栈分析.md"
    ).read_text(encoding="utf-8")
    assert "## 技术分析\nRedis 负责幂等、限流和 session lane。" in content
    assert "## 结论\n- Redis 适合做轻量协调层" in content


def test_outline_structured_document_reports_sections_and_material_gaps(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "outline_structured_document",
        {
            "title": "Gateway README 重构",
            "document_type": "readme",
            "target_audience": "新接触项目的开发者",
            "source_material_summary": "已有部署方式和架构说明。",
            "missing_materials": ["缺少端口表", "缺少健康检查说明"],
            "tone": "正式、清晰",
        },
    )

    data = json.loads(result)
    assert data["type"] == "document_outline"
    assert data["document_type"] == "readme"
    assert data["readiness"] == "needs_material"
    assert data["recommended_tool"] == "save_structured_document"
    assert "架构预览" in data["sections"]
    assert data["missing_materials"] == ["缺少端口表", "缺少健康检查说明"]
    assert data["next_steps"][0] == "补齐缺失材料后再成文。"


def test_save_structured_document_writes_retrospective(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "save_structured_document",
        {
            "title": "7月复盘",
            "document_type": "retrospective",
            "summary": "本月完成 Agent 能力增强。",
            "content": "完成工具和提示词增强。",
            "risks": ["多 Agent 协作还未实现"],
            "next_steps": ["继续补协作编排"],
        },
    )

    assert result == "报告路径：workspace/reports/retrospectives/7月复盘.md"
    content = (tmp_path / "reports" / "retrospectives" / "7月复盘.md").read_text(
        encoding="utf-8"
    )
    assert "## 完成情况\n完成工具和提示词增强。" in content
    assert "## 后续行动\n- 继续补协作编排" in content


def test_render_repo_analysis_markdown_formats_structured_analysis(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/repo",
        "url": "https://github.com/demo/repo",
        "analysis_goal": "判断是否值得 Gateway 借鉴。",
        "project_positioning": {
            "description": "Agent workflow templates",
            "language": "Markdown",
            "topics": ["agent", "workflow"],
            "license": "MIT",
            "lifecycle": "active-or-recent",
        },
        "gateway_fit": {
            "score": 85,
            "priority": "high",
            "signals": ["包含 Agent / Skill 信号。"],
        },
        "key_findings": ["README 提供工作流模板。"],
        "gateway_reuse_ideas": ["参考工作流组织方式。"],
        "risks": ["需要确认许可证复用边界。"],
        "recommendations": ["优先抽取模板并做小规模验证。"],
    }

    markdown = registry.dispatch(
        "render_repo_analysis_markdown",
        {
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 仓库分析：demo/repo")
    assert "## 项目定位" in markdown
    assert "- 仓库：demo/repo" in markdown
    assert "- 评分：85" in markdown
    assert "## 对 Gateway 的借鉴点" in markdown
    assert "- 参考工作流组织方式。" in markdown
    assert "```json" in markdown


def test_format_github_repo_analysis_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/repo",
        "url": "https://github.com/demo/repo",
        "analysis_goal": "判断是否值得 Gateway 借鉴。",
        "project_positioning": {
            "description": "Agent workflow templates",
            "language": "Markdown",
            "topics": ["agent", "workflow"],
            "license": "MIT",
            "lifecycle": "active-or-recent",
        },
        "gateway_fit": {
            "score": 85,
            "priority": "high",
            "signals": ["包含 Agent / Skill 信号。"],
        },
        "key_findings": ["README 提供工作流模板。"],
        "gateway_reuse_ideas": ["参考工作流组织方式。"],
        "risks": ["需要确认许可证复用边界。"],
        "recommendations": ["优先抽取模板并做小规模验证。"],
    }

    formatted = registry.dispatch(
        "format_github_repo_analysis",
        {"analysis_json": json.dumps(analysis, ensure_ascii=False)},
    )

    assert "## GitHub 仓库分析摘要" in formatted
    assert "- 分析目标：判断是否值得 Gateway 借鉴。" in formatted
    assert "- 一句话结论：Agent workflow templates" in formatted
    assert "| 仓库 | demo/repo |" in formatted
    assert "| 适配分 | 85 |" in formatted
    assert "- README 提供工作流模板。" in formatted
    assert "- 参考工作流组织方式。" in formatted
    assert "- 需要确认许可证复用边界。" in formatted
    assert "正式报告落盘仍应交给 doc-writer" in formatted


def test_format_github_repo_adoption_plan_outputs_user_facing_roadmap(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    adoption_plan = {
        "type": "github_repo_adoption_plan",
        "repository": "demo/workflow",
        "url": "https://github.com/demo/workflow",
        "adoption_goal": "拆成 Gateway 落地阶段",
        "decision": {
            "action": "hold",
            "reason": "存在许可或维护状态风险，先人工确认再进入实现。",
        },
        "fit": {
            "score": 82,
            "priority": "high",
            "signals": ["包含 Agent workflow 信号。"],
        },
        "stages": [
            {
                "title": "证据复核",
                "objective": "确认 README、许可证、维护状态和关键目录是否支持继续采纳。",
                "tasks": ["复核 README 与目录树。", "确认许可证。"],
            },
            {
                "title": "落地验证 1",
                "objective": "参考工作流模板，改进 Gateway 主动任务编排。",
                "tasks": ["拆成一个小实验。", "补充测试。"],
            },
        ],
        "risk_gates": ["确认许可证允许学习、引用或复用。"],
        "acceptance_checks": ["形成一份可追溯的证据摘要。"],
        "handoff": {
            "target_agent_id": "planner",
            "summary": "可交给 planner 拆成 PROJECT_PLAN 小阶段。",
        },
        "note": "这是基于仓库分析生成的采纳路线图，不代表已经完成代码实现或依赖引入。",
    }

    formatted = registry.dispatch(
        "format_github_repo_adoption_plan",
        {"adoption_plan_json": json.dumps(adoption_plan, ensure_ascii=False)},
    )

    assert "## GitHub 仓库采纳路线图" in formatted
    assert "- 仓库：demo/workflow" in formatted
    assert "- 决策：建议暂缓" in formatted
    assert "| 适配分 | 82 |" in formatted
    assert "| 1 | 证据复核 | 确认 README、许可证、维护状态和关键目录是否支持继续采纳。 | 复核 README 与目录树。；确认许可证。 |" in formatted
    assert "- 确认许可证允许学习、引用或复用。" in formatted
    assert "- 形成一份可追溯的证据摘要。" in formatted
    assert "- 目标 Agent：planner" in formatted
    assert "不代表已经完成代码实现或依赖引入" in formatted


def test_render_repo_analysis_markdown_can_be_saved_as_report(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/repo",
        "url": "https://github.com/demo/repo",
        "analysis_goal": "生成仓库分析报告。",
        "project_positioning": {"description": "demo", "language": "Python"},
        "gateway_fit": {"score": 60, "priority": "medium", "signals": []},
        "key_findings": [],
        "gateway_reuse_ideas": [],
        "risks": [],
        "recommendations": [],
    }

    markdown = registry.dispatch(
        "render_repo_analysis_markdown",
        {"analysis_json": json.dumps(analysis, ensure_ascii=False)},
    )
    result = registry.dispatch(
        "save_markdown_report",
        {
            "title": "仓库分析 demo/repo",
            "category": "github-repos",
            "file_name": "仓库分析-demo-repo",
            "content": markdown,
        },
    )

    assert result == "报告路径：workspace/reports/github-repos/仓库分析-demo-repo.md"
    content = (
        tmp_path / "reports" / "github-repos" / "仓库分析-demo-repo.md"
    ).read_text(encoding="utf-8")
    assert content.startswith("# 仓库分析：demo/repo")
    assert "## 建议下一步" in content


def test_render_github_repo_risk_markdown_formats_risk_scan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    scan = {
        "type": "github_repo_risk_scan",
        "repository": "demo/risky",
        "url": "https://github.com/demo/risky",
        "intended_use": "学习 Agent 工具组织",
        "risk_level": "high",
        "decision": "hold",
        "risk_items": [
            {
                "severity": "high",
                "area": "license",
                "issue": "许可证缺失或未识别。",
                "impact": "复用边界不清晰。",
                "mitigation": "复用前人工确认 LICENSE。",
            }
        ],
        "dependency_files": ["requirements.txt", "package.json"],
        "summary": {
            "license": "unknown",
            "archived": True,
            "open_issues": 120,
            "stars": 5,
        },
        "next_actions": ["复核许可证和 README。"],
        "note": (
            "这是基于 github_repo_summary 的轻量风险扫描，"
            "不代表已经完成法律、安全或运行验证。"
        ),
    }
    gate_review = {
        "type": "github_repo_risk_gate_review",
        "review_target": "demo/risky",
        "source_decision": "hold",
        "decision": "no-go",
        "checklist": [
            {
                "item": "许可证风险可接受",
                "passed": False,
                "evidence": "unknown",
            }
        ],
        "next_actions": ["人工复核 LICENSE、README 授权说明或联系作者后再复用。"],
    }

    markdown = registry.dispatch(
        "render_github_repo_risk_markdown",
        {
            "risk_scan_json": json.dumps(scan, ensure_ascii=False),
            "gate_review_json": json.dumps(gate_review, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 仓库风险扫描：demo/risky")
    assert "- 风险等级：high" in markdown
    assert "- 建议决策：hold" in markdown
    assert "| high | license | 许可证缺失或未识别。 | 复用边界不清晰。 | 复用前人工确认 LICENSE。 |" in markdown
    assert "## 门禁审查结论" in markdown
    assert "- reviewer 结论：no-go" in markdown
    assert "| 许可证风险可接受 | 未通过 | unknown |" in markdown
    assert "- requirements.txt" in markdown
    assert "- package.json" in markdown
    assert "- 复核许可证和 README。" in markdown
    assert '"gate_review"' in markdown
    assert "```json" in markdown


def test_render_research_evidence_markdown_formats_sources_and_gaps(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    evidence = {
        "type": "research_evidence_pack",
        "topic": "Redis 看门狗",
        "research_question": "Redis 锁续期是否适合 Gateway session lane？",
        "conclusion": "适合长任务互斥，但需要 owner token 和续期失败处理。",
        "evidence_quality": "strong",
        "source_count": 2,
        "primary_source_count": 1,
        "sources": [
            {
                "title": "Redisson Lock Docs",
                "url": "https://redisson.org/docs/data-and-services/locks-and-synchronizers/",
                "source_type": "docs",
                "fact": "Redisson 支持 watchdog 自动续期。",
            },
            {
                "title": "Redis SET NX PX",
                "url": "https://redis.io/docs/latest/commands/set/",
                "source_type": "docs",
                "fact": "锁需要设置过期时间和唯一值。",
            },
        ],
        "key_facts": ["看门狗适合任务耗时不可预测的场景。"],
        "source_conflicts": [],
        "uncertainty": ["需要验证 Python 客户端实现质量。"],
        "freshness": "2026-07-07 检索。",
        "downstream_use": "供 planner 和 reviewer 复用。",
        "reusable_payload": {
            "topic": "Redis 看门狗",
            "question": "是否适合 session lane？",
            "conclusion": "可作为锁续期方案候选。",
        },
        "next_actions": ["补一个锁续期 smoke test。"],
    }

    markdown = registry.dispatch(
        "render_research_evidence_markdown",
        {
            "evidence_json": json.dumps(evidence, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 调研证据包：Redis 看门狗")
    assert "- 证据质量：strong" in markdown
    assert "| 1 | Redisson Lock Docs | docs | Redisson 支持 watchdog 自动续期。 | https://redisson.org/docs/data-and-services/locks-and-synchronizers/ |" in markdown
    assert "- 看门狗适合任务耗时不可预测的场景。" in markdown
    assert "- 需要验证 Python 客户端实现质量。" in markdown
    assert "- conclusion：可作为锁续期方案候选。" in markdown
    assert "```json" in markdown


def test_render_research_option_comparison_markdown_formats_decision_report(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    comparison = {
        "type": "research_option_comparison",
        "topic": "入站队列中间件选型",
        "decision_question": "Gateway 分布式入站削峰优先选择 RabbitMQ 还是 Redis？",
        "criteria": ["可靠投递", "削峰能力", "会话串行协同"],
        "constraints": ["优先企业级可靠性", "保留 Redis 做协调层"],
        "recommended_option": "RabbitMQ",
        "evidence_quality": "strong",
        "source_count": 2,
        "primary_source_count": 2,
        "options": [
            {
                "name": "RabbitMQ",
                "score": 86,
                "strengths": ["确认机制成熟", "适合削峰"],
                "weaknesses": ["会话串行仍需协调层"],
                "best_for": ["企业级可靠队列"],
                "avoid_when": ["只需要轻量协调"],
            },
            {
                "name": "Redis",
                "score": 72,
                "strengths": ["轻量"],
                "weaknesses": ["消息队列语义不足"],
                "best_for": ["锁和 ready index"],
                "avoid_when": ["需要完整可靠队列"],
            },
        ],
        "sources": [
            {
                "title": "RabbitMQ docs",
                "url": "https://www.rabbitmq.com/docs",
                "source_type": "official",
                "fact": "RabbitMQ documents acknowledgements and durable queues.",
            }
        ],
        "uncertainty": ["仍需本机压测确认容量。"],
        "freshness": "2026-07-07 检索",
        "next_actions": ["把推荐方案「RabbitMQ」交给 planner 拆成验证计划。"],
        "downstream_use": "供 planner、reviewer 和 doc-writer 复用。",
    }

    markdown = registry.dispatch(
        "render_research_option_comparison_markdown",
        {
            "comparison_json": json.dumps(comparison, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 方案对比：入站队列中间件选型")
    assert "- 推荐方案：RabbitMQ" in markdown
    assert "## 候选方案对比" in markdown
    assert "| RabbitMQ | 86 | 确认机制成熟；适合削峰 | 会话串行仍需协调层 | 企业级可靠队列 | 只需要轻量协调 |" in markdown
    assert "| 1 | RabbitMQ docs | official | RabbitMQ documents acknowledgements and durable queues. | https://www.rabbitmq.com/docs |" in markdown
    assert "- 仍需本机压测确认容量。" in markdown
    assert "```json" in markdown


def test_render_execution_record_markdown_formats_plan_and_gate_review(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "task_plan_from_adoption",
        "title": "workflow 采纳计划",
        "goal": "采纳 workflow 模板优化 Gateway 主动任务。",
        "scope": "只做最小原型，不修改生产配置。",
        "repository": "demo/workflow",
        "decision": {"action": "pilot", "reason": "先小规模验证。"},
        "phases": [
            {
                "name": "证据复核",
                "task": "复核 README 和许可证",
                "output": "证据摘要",
                "done": "证据摘要已落盘",
            }
        ],
        "risks": ["许可证需要人工确认。"],
        "next_steps": ["pytest tests/test_builtin_tools.py -q"],
    }
    review = {
        "type": "task_plan_gate_review",
        "review_target": "workflow 采纳计划",
        "decision": "conditional-go",
        "checklist": [
            {"item": "目标已明确", "passed": True, "evidence": "目标已给出。"},
            {"item": "风险和门槛已列出", "passed": False, "evidence": "风险不足。"},
        ],
        "risks": ["需要补充回滚验证。"],
        "next_actions": ["补充风险门槛。"],
    }

    markdown = registry.dispatch(
        "render_execution_record_markdown",
        {
            "task_plan_json": json.dumps(plan, ensure_ascii=False),
            "gate_review_json": json.dumps(review, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 执行记录：workflow 采纳计划")
    assert "- 门禁结论：conditional-go" in markdown
    assert "| 证据复核 | 复核 README 和许可证 | 证据摘要 | 证据摘要已落盘 |" in markdown
    assert "| 风险和门槛已列出 | 未通过 | 风险不足。 |" in markdown
    assert "- 许可证需要人工确认。" in markdown
    assert "- 补充风险门槛。" in markdown
    assert "```json" in markdown


def test_render_release_gate_markdown_formats_review(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    review = {
        "type": "release_gate_review",
        "change_summary": "新增 doc-writer 发布门禁 Markdown 渲染工具。",
        "decision": "conditional-go",
        "checklist": [
            {"item": "测试证据已提供", "passed": True, "evidence": "pytest 已通过。"},
            {"item": "回滚或恢复方案已说明", "passed": False, "evidence": "缺少回滚方案。"},
        ],
        "risks": [
            {
                "severity": "medium",
                "issue": "发布门禁报告格式可能不完整",
                "status": "mitigated",
                "mitigation": "补充渲染测试。",
            }
        ],
        "test_evidence": ["pytest tests/test_builtin_tools.py -q"],
        "unresolved_items": ["补充回滚方案"],
        "rollback_plan": "",
        "next_actions": ["补充回滚或恢复方案。"],
    }

    markdown = registry.dispatch(
        "render_release_gate_markdown",
        {
            "gate_review_json": json.dumps(review, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 发布门禁审查报告")
    assert "- 门禁结论：conditional-go" in markdown
    assert "新增 doc-writer 发布门禁 Markdown 渲染工具。" in markdown
    assert "| 回滚或恢复方案已说明 | 未通过 | 缺少回滚方案。 |" in markdown
    assert "| medium | 发布门禁报告格式可能不完整 | mitigated | 补充渲染测试。 |" in markdown
    assert "- pytest tests/test_builtin_tools.py -q" in markdown
    assert "- 补充回滚方案" in markdown
    assert "```json" in markdown


def test_render_agent_collaboration_markdown_formats_handoff_route(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "user_goal": "评估一个 GitHub 仓库是否值得接入 Gateway。",
        "expected_output": "协作路线和最终 Markdown 报告。",
        "should_persist": True,
        "constraints": ["不自动调用任何 Agent。"],
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库价值和接入风险。",
                "input_contract": {
                    "user_goal": "评估仓库。",
                    "upstream_result": "第一阶段为空。",
                },
                "expected_output": "结构化仓库分析 JSON。",
            },
            {
                "step": 2,
                "agent_id": "doc-writer",
                "purpose": "把结构化分析整理成正式文档。",
                "input_contract": {
                    "upstream_result": "repo-analyzer 输出的 github_repo_analysis。",
                },
                "expected_output": "Markdown 报告。",
            },
        ],
        "next_actions": ["先把第一阶段 handoff_prompt 交给 repo-analyzer。"],
        "note": "这是多 Agent 协作路线规划，不代表任何 Agent 已经执行。",
    }

    markdown = registry.dispatch(
        "render_agent_collaboration_markdown",
        {
            "collaboration_json": json.dumps(plan, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# Agent 协作方案：repo-adoption")
    assert "- 用户目标：评估一个 GitHub 仓库是否值得接入 Gateway。" in markdown
    assert "| 1 | repo-analyzer | 分析仓库价值和接入风险。 | 评估仓库。 | 结构化仓库分析 JSON。 |" in markdown
    assert "| 2 | doc-writer | 把结构化分析整理成正式文档。 | repo-analyzer 输出的 github_repo_analysis。 | Markdown 报告。 |" in markdown
    assert "- 不自动调用任何 Agent。" in markdown
    assert "不代表任何 Agent 已经执行" in markdown
    assert "```json" in markdown


def test_render_agent_collaboration_progress_markdown_formats_status(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {
            "step": 3,
            "agent_id": "planner",
            "purpose": "把方案对比和门禁结论转成最小验证计划。",
            "expected_output": "task_plan_from_research_option_comparison JSON。",
        },
        "stages": [
            {
                "step": 1,
                "agent_id": "research",
                "status": "completed",
                "expected_output": "research_option_comparison JSON。",
                "output_summary": "已输出方案对比。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "expected_output": "research_option_comparison_gate_review JSON。",
                "output_summary": "conditional-go。",
            },
            {
                "step": 3,
                "agent_id": "planner",
                "status": "next",
                "expected_output": "task_plan_from_research_option_comparison JSON。",
                "output_summary": "",
            },
        ],
        "next_handoff_args": {
            "stage": 3,
            "upstream_result_summary": "conditional-go。",
        },
        "next_actions": ["调用 build_collaboration_stage_handoff 生成第 3 阶段交接提示。"],
    }

    markdown = registry.dispatch(
        "render_agent_collaboration_progress_markdown",
        {"progress_json": json.dumps(progress, ensure_ascii=False)},
    )

    assert markdown.startswith("# Agent 协作进度：research-option-validation")
    assert "当前状态：in-progress" in markdown
    assert "| 1 | research | completed | research_option_comparison JSON。 | 已输出方案对比。 |" in markdown
    assert "目标 Agent：planner" in markdown
    assert "task_plan_from_research_option_comparison JSON" in markdown
    assert "upstream_result_summary" in markdown
    assert "build_collaboration_stage_handoff" in markdown


def test_render_collaboration_progress_gate_markdown_formats_review(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "stages": [
            {
                "step": 1,
                "agent_id": "research",
                "status": "completed",
                "output_summary": "已输出方案对比。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
            {
                "step": 3,
                "agent_id": "planner",
                "status": "next",
                "output_summary": "",
            },
        ],
    }
    review = {
        "type": "collaboration_progress_gate_review",
        "review_target": "research-option-validation",
        "decision": "conditional-go",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {"step": 3, "agent_id": "planner"},
        "checklist": [
            {"item": "阶段状态连续", "passed": True, "evidence": "阶段连续。"},
            {"item": "风险边界已说明", "passed": False, "evidence": "缺少风险边界。"},
        ],
        "risks": ["需要补充风险边界。"],
        "next_actions": ["补充协作进度边界。"],
    }

    markdown = registry.dispatch(
        "render_collaboration_progress_gate_markdown",
        {
            "gate_review_json": json.dumps(review, ensure_ascii=False),
            "progress_json": json.dumps(progress, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 协作进度门禁审查：research-option-validation")
    assert "- 门禁结论：conditional-go" in markdown
    assert "| 2 | reviewer | completed | conditional-go。 |" in markdown
    assert "| 风险边界已说明 | 未通过 | 缺少风险边界。 |" in markdown
    assert "- 需要补充风险边界。" in markdown
    assert "- 补充协作进度边界。" in markdown
    assert "```json" in markdown


def test_render_agent_handoff_package_gate_markdown_formats_review(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    package = {
        "type": "agent_handoff_package",
        "user_goal": "把已有材料整理成 Markdown 报告。",
        "target_agent_id": "doc-writer",
        "handoff_prompt": "目标 Agent：doc-writer\n\n请把材料整理成正式报告。",
    }
    review = {
        "type": "agent_handoff_package_gate_review",
        "review_target": "doc-writer",
        "decision": "conditional-go",
        "checklist": [
            {"item": "目标 Agent 明确", "passed": True, "evidence": "目标为 doc-writer。"},
            {"item": "约束边界完整", "passed": False, "evidence": "缺少保存路径约束。"},
        ],
        "risks": ["保存路径需要补充。"],
        "next_actions": ["补充 reports/plans 保存路径。"],
    }

    markdown = registry.dispatch(
        "render_agent_handoff_package_gate_markdown",
        {
            "gate_review_json": json.dumps(review, ensure_ascii=False),
            "package_json": json.dumps(package, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# Agent 交接包门禁审查：doc-writer")
    assert "- 门禁结论：conditional-go" in markdown
    assert "| 约束边界完整 | 未通过 | 缺少保存路径约束。 |" in markdown
    assert "```text\n目标 Agent：doc-writer" in markdown
    assert "- 保存路径需要补充。" in markdown
    assert "- 补充 reports/plans 保存路径。" in markdown
    assert "```json" in markdown


def test_suggest_agent_delegation_outputs_structured_json(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "suggest_agent_delegation",
        {
            "task_type": "repo-analysis",
            "target_agent_id": "repo-analyzer",
            "reason": "需要读取仓库元数据并生成分析报告。",
            "context_summary": "用户给了一个 GitHub 仓库链接，希望了解项目用途。",
            "handoff_prompt": "请分析 https://github.com/example/repo 并生成中文报告。",
            "confidence": 1.5,
            "can_answer_briefly": False,
        },
    )

    data = json.loads(result)
    assert data == {
        "type": "agent_delegation_suggestion",
        "task_type": "repo-analysis",
        "target_agent_id": "repo-analyzer",
        "reason": "需要读取仓库元数据并生成分析报告。",
        "context_summary": "用户给了一个 GitHub 仓库链接，希望了解项目用途。",
        "handoff_prompt": "请分析 https://github.com/example/repo 并生成中文报告。",
        "confidence": 1.0,
        "can_answer_briefly": False,
        "status": "suggested",
        "note": "这是委派建议，不会自动调用目标 Agent。",
    }


def test_build_agent_handoff_prompt_formats_required_context(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "build_agent_handoff_prompt",
        {
            "user_goal": "分析 https://github.com/example/repo 并生成中文报告",
            "target_agent_id": "repo-analyzer",
            "context_summary": "用户从企业微信发起，希望了解项目用途和可借鉴点。",
            "constraints": ["只做分析，不修改代码"],
            "expected_output": "输出仓库用途、核心模块、风险和 Gateway 借鉴点。",
            "source_platform": "wework",
            "should_persist": True,
            "known_inputs": ["仓库链接：https://github.com/example/repo"],
            "open_questions": ["是否需要附带采纳计划？"],
        },
    )

    assert "目标 Agent：repo-analyzer" in result
    assert "## 用户原始目标\n分析 https://github.com/example/repo 并生成中文报告" in result
    assert "- 仓库链接：https://github.com/example/repo" in result
    assert "- 只做分析，不修改代码" in result
    assert "输出仓库用途、核心模块、风险和 Gateway 借鉴点。" in result
    assert "需要落盘" in result
    assert "## 来源平台\nwework" in result
    assert "- 是否需要附带采纳计划？" in result


def test_plan_agent_collaboration_builds_repo_adoption_route(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "plan_agent_collaboration",
        {
            "user_goal": "分析 https://github.com/example/repo，并形成 Gateway 采纳计划和执行记录。",
            "task_type": "repo-adoption",
            "constraints": ["只做分析和计划，不直接改代码"],
            "expected_output": "Markdown 执行记录",
            "should_persist": True,
        },
    )

    data = json.loads(result)
    assert data["type"] == "agent_collaboration_plan"
    assert data["task_type"] == "repo-adoption"
    assert data["should_persist"] is True
    assert [stage["agent_id"] for stage in data["handoff_sequence"]] == [
        "repo-analyzer",
        "reviewer",
        "planner",
        "doc-writer",
    ]
    assert "github_repo_risk_scan" in data["handoff_sequence"][0]["expected_output"]
    assert data["handoff_sequence"][1]["expected_output"] == "github_repo_risk_gate_review JSON。"
    assert data["handoff_sequence"][2]["expected_output"] == "task_plan_from_repo_review JSON。"
    assert data["handoff_sequence"][0]["input_contract"]["constraints"] == [
        "只做分析和计划，不直接改代码"
    ]
    assert "不会自动调用任何 Agent" in data["next_actions"][2]
    assert "不代表任何 Agent 已经执行" in data["note"]


def test_list_agent_collaboration_routes_filters_by_alias(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "list_agent_collaboration_routes",
            {"task_types": ["tech-selection"], "include_stages": True},
        )
    )

    assert data["type"] == "agent_collaboration_route_catalog"
    assert data["count"] == 1
    route = data["routes"][0]
    assert route["task_type"] == "research-option-validation"
    assert route["agent_sequence"] == [
        "research",
        "reviewer",
        "planner",
        "reviewer",
        "doc-writer",
    ]
    assert "tech-selection" in route["aliases"]
    assert route["stages"][0]["expected_output"] == "research_option_comparison JSON。"
    assert data["aliases"]["tech-selection"] == "research-option-validation"
    assert "不代表任何 Agent 已经自动执行" in data["boundary"]


def test_build_collaboration_stage_handoff_uses_upstream_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = json.loads(
        registry.dispatch(
            "plan_agent_collaboration",
            {
                "user_goal": "对比 RabbitMQ、Redis、Kafka，并形成 Gateway 入站队列验证计划。",
                "task_type": "research-option-validation",
                "constraints": ["只做验证计划，不直接改生产配置"],
            },
        )
    )

    handoff = registry.dispatch(
        "build_collaboration_stage_handoff",
        {
            "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
            "stage": 3,
            "upstream_result_summary": "reviewer 已给出 conditional-go，要求只进入最小验证。",
            "additional_context": "重点关注入站削峰和会话串行。",
        },
    )

    assert "目标 Agent：planner" in handoff
    assert "协作类型：research-option-validation" in handoff
    assert "阶段：3/5" in handoff
    assert "把方案对比和门禁结论转成最小验证计划" in handoff
    assert "research_option_comparison_gate_review JSON" in handoff
    assert "reviewer 已给出 conditional-go" in handoff
    assert "- 只做验证计划，不直接改生产配置" in handoff
    assert "重点关注入站削峰和会话串行。" in handoff
    assert "不代表目标 Agent 已经执行" in handoff


def test_summarize_collaboration_progress_returns_next_handoff_args(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = json.loads(
        registry.dispatch(
            "plan_agent_collaboration",
            {
                "user_goal": "对比 RabbitMQ、Redis、Kafka，并形成 Gateway 入站队列验证计划。",
                "task_type": "research-option-validation",
                "constraints": ["只做验证计划，不直接改生产配置"],
            },
        )
    )

    data = json.loads(
        registry.dispatch(
            "summarize_collaboration_progress",
            {
                "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
                "completed_stage_outputs": [
                    {
                        "step": 1,
                        "summary": "research 已输出 research_option_comparison。",
                        "payload": {"type": "research_option_comparison"},
                    },
                    {
                        "step": 2,
                        "summary": "reviewer 已给出 conditional-go。",
                        "payload": {"type": "research_option_comparison_gate_review"},
                    },
                ],
            },
        )
    )

    assert data["type"] == "agent_collaboration_progress"
    assert data["status"] == "in-progress"
    assert data["completed_stage_count"] == 2
    assert data["total_stage_count"] == 5
    assert data["next_stage"]["step"] == 3
    assert data["next_stage"]["agent_id"] == "planner"
    assert data["stages"][0]["status"] == "completed"
    assert data["stages"][2]["status"] == "next"
    assert data["next_handoff_args"]["stage"] == 3
    assert "reviewer 已给出 conditional-go" in data["next_handoff_args"][
        "upstream_result_summary"
    ]
    assert "build_collaboration_stage_handoff" in data["next_actions"][0]
    assert "不代表任何 Agent 已经自动执行" in data["boundary"]


def test_format_collaboration_progress_outputs_user_reply(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    progress = {
        "type": "agent_collaboration_progress",
        "task_type": "research-option-validation",
        "status": "in-progress",
        "completed_stage_count": 2,
        "total_stage_count": 5,
        "next_stage": {
            "step": 3,
            "agent_id": "planner",
            "purpose": "把方案对比和门禁结论转成最小验证计划。",
        },
        "stages": [
            {
                "step": 1,
                "agent_id": "research",
                "status": "completed",
                "output_summary": "已输出方案对比。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
            {
                "step": 3,
                "agent_id": "planner",
                "status": "next",
                "expected_output": "task_plan_from_research_option_comparison JSON。",
            },
        ],
        "next_actions": ["调用 build_collaboration_stage_handoff 生成第 3 阶段交接提示。"],
        "boundary": "这是协作进度摘要，不代表任何 Agent 已经自动执行。",
    }

    reply = registry.dispatch(
        "format_collaboration_progress",
        {
            "progress_json": json.dumps(progress, ensure_ascii=False),
            "include_stage_details": True,
        },
    )

    assert reply.startswith("# 协作进度摘要")
    assert "- 完成阶段：2 / 5" in reply
    assert "第 3 阶段交给 `planner`" in reply
    assert "| 2 | reviewer | completed | conditional-go。 |" in reply
    assert "- 调用 build_collaboration_stage_handoff 生成第 3 阶段交接提示。" in reply
    assert "不代表任何 Agent 已经自动执行" in reply


def test_compose_collaboration_final_summary_collects_stage_outputs(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = json.loads(
        registry.dispatch(
            "plan_agent_collaboration",
            {
                "user_goal": "分析一个 GitHub 仓库并给出是否采纳的执行建议。",
                "task_type": "repo-adoption",
                "expected_output": "最终采纳建议",
                "should_persist": True,
            },
        )
    )
    progress = json.loads(
        registry.dispatch(
            "summarize_collaboration_progress",
            {
                "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
                "completed_stage_outputs": [
                    {"step": 1, "summary": "repo-analyzer 已完成仓库分析。"},
                    {"step": 2, "summary": "reviewer 给出 conditional-go。"},
                    {"step": 3, "summary": "planner 生成三阶段采纳计划。"},
                    {"step": 4, "summary": "doc-writer 已生成正式 Markdown 报告。"},
                ],
            },
        )
    )

    data = json.loads(
        registry.dispatch(
            "compose_collaboration_final_summary",
            {
                "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
                "progress_json": json.dumps(progress, ensure_ascii=False),
                "completed_stage_outputs": [
                    {"step": 1, "summary": "repo-analyzer 已完成仓库分析。"},
                    {"step": 2, "summary": "reviewer 给出 conditional-go。"},
                    {"step": 3, "summary": "planner 生成三阶段采纳计划。"},
                    {"step": 4, "summary": "doc-writer 已生成正式 Markdown 报告。"},
                ],
                "unresolved_items": ["许可证仍需人工复核。"],
                "next_actions": ["把报告路径发给用户。"],
            },
        )
    )

    assert data["type"] == "agent_collaboration_final_summary"
    assert data["task_type"] == "repo-adoption"
    assert data["status"] == "completed"
    assert data["completed_stage_count"] == 4
    assert data["total_stage_count"] == 4
    assert data["final_conclusion"] == "doc-writer 已生成正式 Markdown 报告。"
    assert data["stage_summaries"][1]["agent_id"] == "reviewer"
    assert data["unresolved_items"] == ["许可证仍需人工复核。"]
    assert data["next_actions"] == ["把报告路径发给用户。"]
    assert "不代表重新执行任何 Agent" in data["boundary"]


def test_format_collaboration_final_summary_outputs_user_reply(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "status": "completed",
        "completed_stage_count": 4,
        "total_stage_count": 4,
        "final_conclusion": "建议有条件采纳，先复核许可证再进入实现。",
        "stage_summaries": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "status": "completed",
                "output_summary": "仓库适配度较高。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
        ],
        "unresolved_items": ["许可证仍需人工确认。"],
        "next_actions": ["把报告路径发给用户。"],
        "boundary": "这是入口层对多 Agent 协作结果的最终摘要，不代表重新执行任何 Agent。",
    }

    reply = registry.dispatch(
        "format_collaboration_final_summary",
        {
            "summary_json": json.dumps(summary, ensure_ascii=False),
            "include_stage_details": True,
        },
    )

    assert reply.startswith("# 协作最终摘要")
    assert "建议有条件采纳，先复核许可证再进入实现。" in reply
    assert "- 完成阶段：4 / 4" in reply
    assert "| 2 | reviewer | completed | conditional-go。 |" in reply
    assert "- 许可证仍需人工确认。" in reply
    assert "- 把报告路径发给用户。" in reply
    assert "不代表重新执行任何 Agent" in reply


def test_render_agent_collaboration_final_summary_markdown_formats_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "user_goal": "分析一个 GitHub 仓库并给出是否采纳的执行建议。",
        "status": "completed",
        "completed_stage_count": 4,
        "total_stage_count": 4,
        "final_conclusion": "建议有条件采纳，先复核许可证再进入实现。",
        "stage_summaries": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库价值。",
                "status": "completed",
                "output_summary": "仓库适配度较高。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "purpose": "审查风险。",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
        ],
        "unresolved_items": ["许可证仍需人工确认。"],
        "next_actions": ["把报告路径发给用户。"],
        "boundary": "这是入口层对多 Agent 协作结果的最终摘要，不代表重新执行任何 Agent。",
    }

    markdown = registry.dispatch(
        "render_agent_collaboration_final_summary_markdown",
        {
            "summary_json": json.dumps(summary, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# Agent 协作最终摘要：repo-adoption")
    assert "- 用户目标：分析一个 GitHub 仓库并给出是否采纳的执行建议。" in markdown
    assert "建议有条件采纳，先复核许可证再进入实现。" in markdown
    assert "| 2 | reviewer | completed | 审查风险。 | conditional-go。 |" in markdown
    assert "- 许可证仍需人工确认。" in markdown
    assert "- 把报告路径发给用户。" in markdown
    assert "```json" in markdown


def test_render_collaboration_final_summary_gate_markdown_formats_review(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    summary = {
        "type": "agent_collaboration_final_summary",
        "task_type": "repo-adoption",
        "status": "completed",
        "completed_stage_count": 2,
        "total_stage_count": 2,
        "final_conclusion": "建议有条件采纳，先复核许可证。",
        "stage_summaries": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "status": "completed",
                "output_summary": "仓库适配度较高。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "status": "completed",
                "output_summary": "conditional-go。",
            },
        ],
    }
    review = {
        "type": "collaboration_final_summary_gate_review",
        "review_target": "repo-adoption",
        "decision": "conditional-go",
        "completed_stage_count": 2,
        "total_stage_count": 2,
        "unresolved_items": ["许可证仍需人工确认。"],
        "checklist": [
            {"item": "最终结论明确", "passed": True, "evidence": "已有明确结论。"},
            {"item": "后续动作明确", "passed": False, "evidence": "缺少 next_actions。"},
        ],
        "risks": ["许可证未确认。"],
        "next_actions": ["补充许可证复核动作。"],
    }

    markdown = registry.dispatch(
        "render_collaboration_final_summary_gate_markdown",
        {
            "gate_review_json": json.dumps(review, ensure_ascii=False),
            "summary_json": json.dumps(summary, ensure_ascii=False),
            "include_raw_metadata": True,
        },
    )

    assert markdown.startswith("# 协作最终摘要门禁审查：repo-adoption")
    assert "- 门禁结论：conditional-go" in markdown
    assert "建议有条件采纳，先复核许可证。" in markdown
    assert "| 2 | reviewer | completed | conditional-go。 |" in markdown
    assert "| 后续动作明确 | 未通过 | 缺少 next_actions。 |" in markdown
    assert "- 许可证仍需人工确认。" in markdown
    assert "- 补充许可证复核动作。" in markdown
    assert "```json" in markdown


def test_plan_agent_collaboration_builds_research_option_validation_route(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "plan_agent_collaboration",
        {
            "user_goal": "对比 RabbitMQ、Redis、Kafka，并形成 Gateway 入站队列选型验证计划和正式报告。",
            "task_type": "research-option-validation",
            "constraints": ["只形成验证计划，不直接改生产配置"],
            "expected_output": "Markdown 方案验证计划",
            "should_persist": True,
        },
    )

    data = json.loads(result)
    assert data["type"] == "agent_collaboration_plan"
    assert data["task_type"] == "research-option-validation"
    assert [stage["agent_id"] for stage in data["handoff_sequence"]] == [
        "research",
        "reviewer",
        "planner",
        "reviewer",
        "doc-writer",
    ]
    assert data["handoff_sequence"][0]["expected_output"] == "research_option_comparison JSON。"
    assert data["handoff_sequence"][1]["expected_output"] == (
        "research_option_comparison_gate_review JSON。"
    )
    assert data["handoff_sequence"][2]["expected_output"] == (
        "task_plan_from_research_option_comparison JSON。"
    )
    assert data["handoff_sequence"][3]["expected_output"] == "task_plan_gate_review JSON。"
    assert "正式 Markdown 方案验证计划" in data["handoff_sequence"][4]["expected_output"]
    assert data["handoff_sequence"][0]["input_contract"]["constraints"] == [
        "只形成验证计划，不直接改生产配置"
    ]


def test_classify_task_intent_routes_github_repo_analysis(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {"user_text": "帮我分析一下这个仓库 https://github.com/example/repo"},
    )

    data = json.loads(result)
    assert data["type"] == "task_intent_classification"
    assert data["intent"] == "repo-analysis"
    assert data["recommended_agent_id"] == "repo-analyzer"
    assert data["requires_collaboration"] is False
    assert data["collaboration_task_type"] == ""
    assert data["can_answer_directly"] is False
    assert data["confidence"] >= 0.67


def test_classify_task_intent_routes_repo_adoption_collaboration(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {
            "user_text": (
                "分析这个 GitHub 仓库 https://github.com/example/repo，"
                "评估风险，并给出是否值得采纳的计划和正式报告"
            )
        },
    )

    data = json.loads(result)
    assert data["intent"] == "repo-adoption"
    assert data["recommended_agent_id"] == "repo-analyzer"
    assert data["requires_collaboration"] is True
    assert data["collaboration_task_type"] == "repo-adoption"
    assert "plan_agent_collaboration" in data["suggested_next_step"]


def test_classify_task_intent_routes_research_option_validation_collaboration(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {
            "user_text": (
                "帮我做 RabbitMQ、Redis、Kafka 的技术选型和方案对比，"
                "输出验证计划、风险审查和正式报告"
            )
        },
    )

    data = json.loads(result)
    assert data["intent"] == "research-option-validation"
    assert data["recommended_agent_id"] == "research"
    assert data["requires_collaboration"] is True
    assert data["collaboration_task_type"] == "research-option-validation"
    assert "research → reviewer → planner → reviewer → doc-writer" in data[
        "suggested_next_step"
    ]


def test_classify_task_intent_routes_planning_request(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {"user_text": "帮我规划一下下一阶段任务，拆成阶段和验收标准"},
    )

    data = json.loads(result)
    assert data["intent"] == "planning"
    assert data["recommended_agent_id"] == "planner"
    assert "planner" in data["suggested_next_step"]


def test_classify_task_intent_routes_agent_capability_query(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {"user_text": "当前系统有哪些 Agent？每个 Agent 能做什么？"},
    )

    data = json.loads(result)
    assert data["type"] == "task_intent_classification"
    assert data["intent"] == "agent-capabilities"
    assert data["recommended_agent_id"] == "main"
    assert data["can_answer_directly"] is True
    assert data["requires_collaboration"] is False
    assert "list_agent_capabilities" in data["suggested_next_step"]
    assert "format_agent_capability_catalog" in data["suggested_next_step"]


def test_classify_task_intent_keeps_simple_chat_on_main(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "classify_task_intent",
        {"user_text": "你好，简单介绍一下你能做什么"},
    )

    data = json.loads(result)
    assert data["intent"] == "chat"
    assert data["recommended_agent_id"] == "main"
    assert data["can_answer_directly"] is True


def test_format_entry_response_formats_delegation_reply(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "format_entry_response",
        {
            "intent": "repo-analysis",
            "recommended_agent_id": "repo-analyzer",
            "reason": "需要分析仓库结构和可借鉴点。",
            "context_summary": "用户提供了 GitHub 仓库链接，希望生成中文分析。",
            "handoff_prompt": "请分析 https://github.com/example/repo 并落盘报告。",
            "current_reply": "我会先按仓库分析任务处理。",
        },
    )

    assert "判断：这属于 repo-analysis。" in result
    assert "建议交给：`repo-analyzer`。" in result
    assert "交接摘要：用户提供了 GitHub 仓库链接，希望生成中文分析。" in result
    assert "请分析 https://github.com/example/repo 并落盘报告。" in result
    assert "不代表目标 Agent 已经自动执行" in result


def test_format_entry_response_formats_collaboration_route(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库并输出风险扫描。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "purpose": "审查风险门禁。",
            },
        ],
    }

    result = registry.dispatch(
        "format_entry_response",
        {
            "intent": "repo-adoption",
            "recommended_agent_id": "repo-analyzer",
            "reason": "需要多个能力 Agent 串联处理。",
            "context_summary": "用户希望分析仓库、评估风险并形成采纳计划。",
            "current_reply": "我会先生成协作路线。",
            "requires_collaboration": True,
            "collaboration_task_type": "repo-adoption",
            "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
        },
    )

    assert "判断：这属于 repo-adoption，需要多 Agent 协作。" in result
    assert "协作类型：`repo-adoption`。" in result
    assert "1. `repo-analyzer`：分析仓库并输出风险扫描。" in result
    assert "2. `reviewer`：审查风险门禁。" in result
    assert "不代表这些 Agent 已经自动执行" in result


def test_format_entry_response_formats_direct_reply(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "format_entry_response",
        {
            "intent": "chat",
            "recommended_agent_id": "main",
            "reason": "普通问答。",
            "current_reply": "你好，我可以帮你处理日常问答和任务入口判断。",
            "can_answer_directly": True,
        },
    )

    assert result.startswith("你好，我可以帮你处理日常问答")
    assert "判断：这属于 chat，当前由 `main` 直接处理。" in result
    assert "建议交给" not in result


def test_explain_agent_route_describes_collaboration_stages(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    plan = {
        "type": "agent_collaboration_plan",
        "task_type": "repo-adoption",
        "handoff_sequence": [
            {
                "step": 1,
                "agent_id": "repo-analyzer",
                "purpose": "分析仓库并输出风险扫描。",
                "expected_output": "github_repo_analysis JSON。",
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "purpose": "审查风险门禁。",
                "expected_output": "github_repo_risk_gate_review JSON。",
            },
        ],
    }

    data = json.loads(
        registry.dispatch(
            "explain_agent_route",
            {
                "user_goal": "分析仓库并给出采纳计划。",
                "intent": "repo-adoption",
                "recommended_agent_id": "repo-analyzer",
                "reason": "需要分析和审查串联。",
                "requires_collaboration": True,
                "collaboration_task_type": "repo-adoption",
                "collaboration_plan_json": json.dumps(plan, ensure_ascii=False),
            },
        )
    )

    assert data["type"] == "agent_route_explanation"
    assert data["route_type"] == "collaboration"
    assert data["readiness"] == "ready"
    assert [stage["agent_id"] for stage in data["stages"]] == ["repo-analyzer", "reviewer"]
    assert data["stages"][1]["expected_output"] == "github_repo_risk_gate_review JSON。"
    assert "不代表任何目标 Agent 已经自动执行" in data["boundary"]


def test_prepare_entry_route_response_builds_repo_adoption_reply(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "prepare_entry_route_response",
            {
                "user_text": (
                    "分析这个 GitHub 仓库 https://github.com/example/repo，"
                    "评估风险，并给出采纳计划和正式报告"
                ),
                "context_hint": "来自企业微信私聊。",
                "source_platform": "wework",
                "should_persist": True,
                "constraints": ["只做分析和计划，不直接改代码"],
                "expected_output": "协作路线和最终 Markdown 报告",
            },
        )
    )

    assert data["type"] == "entry_route_preparation"
    assert data["classification"]["intent"] == "repo-adoption"
    assert data["classification"]["requires_collaboration"] is True
    assert data["collaboration_plan"]["task_type"] == "repo-adoption"
    assert data["route_explanation"]["route_type"] == "collaboration"
    assert "repo-analyzer" in data["formatted_response"]
    assert "不代表这些 Agent 已经自动执行" in data["formatted_response"]
    assert data["handoff_prompt"] == ""


def test_prepare_entry_route_response_builds_research_option_validation_reply(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "prepare_entry_route_response",
            {
                "user_text": (
                    "帮我做 RabbitMQ、Redis、Kafka 的技术选型和方案对比，"
                    "输出验证计划、风险审查和正式报告"
                ),
                "context_hint": "来自飞书私聊。",
                "source_platform": "feishu",
                "should_persist": True,
                "constraints": ["只做验证计划，不直接改生产配置"],
                "expected_output": "协作路线和最终 Markdown 方案验证计划",
            },
        )
    )

    assert data["type"] == "entry_route_preparation"
    assert data["classification"]["intent"] == "research-option-validation"
    assert data["classification"]["requires_collaboration"] is True
    assert data["collaboration_plan"]["task_type"] == "research-option-validation"
    assert [stage["agent_id"] for stage in data["collaboration_plan"]["handoff_sequence"]] == [
        "research",
        "reviewer",
        "planner",
        "reviewer",
        "doc-writer",
    ]
    assert data["route_explanation"]["route_type"] == "collaboration"
    assert data["route_explanation"]["collaboration_task_type"] == "research-option-validation"
    assert "协作类型：`research-option-validation`。" in data["formatted_response"]
    assert "1. `research`" in data["formatted_response"]
    assert "5. `doc-writer`" in data["formatted_response"]
    assert "不代表这些 Agent 已经自动执行" in data["formatted_response"]
    assert data["handoff_prompt"] == ""


def test_prepare_entry_route_response_formats_agent_capability_catalog(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    agent_dir = workspace / "agents" / "doc-writer"
    agent_dir.mkdir(parents=True)
    config.mkdir()
    (agent_dir / "IDENTITY.md").write_text(
        "\n".join(
            [
                "# 文档整理 Agent",
                "",
                "## 职责",
                "",
                "- 生成 README、报告、手册和 Markdown 文档。",
                "- 整理已有材料。",
            ]
        ),
        encoding="utf-8",
    )
    (config / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "doc-writer",
                        "name": "DocWriter",
                        "personality": "正式、清晰",
                        "tool_policy": {"tool_names": ["save_markdown_report"]},
                        "prompt_policy": {"prompt_dir": "agents/doc-writer"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)

    data = json.loads(
        registry.dispatch(
            "prepare_entry_route_response",
            {"user_text": "当前系统有哪些 Agent？每个 Agent 能做什么？"},
        )
    )

    assert data["type"] == "entry_route_preparation"
    assert data["classification"]["intent"] == "agent-capabilities"
    assert data["capability_catalog"]["count"] == 1
    assert data["capability_match"] is None
    assert data["collaboration_plan"] is None
    assert "# Agent 能力目录" in data["formatted_response"]
    assert "doc-writer" in data["formatted_response"]
    assert data["handoff_prompt"] == ""


def test_prepare_entry_route_response_matches_agent_capability(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    doc_dir = workspace / "agents" / "doc-writer"
    ops_dir = workspace / "agents" / "ops"
    doc_dir.mkdir(parents=True)
    ops_dir.mkdir(parents=True)
    config.mkdir()
    (doc_dir / "IDENTITY.md").write_text(
        "\n".join(["# 文档整理 Agent", "", "## 职责", "", "- 生成 Markdown 报告和项目文档。"]),
        encoding="utf-8",
    )
    (ops_dir / "IDENTITY.md").write_text(
        "\n".join(["# 运维 Agent", "", "## 职责", "", "- 查看 Docker、日志和运行状态。"]),
        encoding="utf-8",
    )
    (config / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "doc-writer",
                        "name": "DocWriter",
                        "personality": "正式、清晰",
                        "tool_policy": {"tool_names": ["save_markdown_report"]},
                        "prompt_policy": {"prompt_dir": "agents/doc-writer"},
                    },
                    {
                        "id": "ops",
                        "name": "GatewayOps",
                        "personality": "只读排障",
                        "tool_policy": {"tool_names": ["ops_readonly_health"]},
                        "prompt_policy": {"prompt_dir": "agents/ops"},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)

    data = json.loads(
        registry.dispatch(
            "prepare_entry_route_response",
            {"user_text": "把已有材料整理成 Markdown 报告，这个任务交给谁处理？"},
        )
    )

    assert data["classification"]["intent"] == "agent-capabilities"
    assert data["capability_catalog"]["count"] == 2
    assert data["capability_match"]["recommended_agent_id"] == "doc-writer"
    assert data["capability_handoff_package"]["target_agent_id"] == "doc-writer"
    assert "# Agent 交接包" in data["formatted_response"]
    assert "目标 Agent：`doc-writer`" in data["formatted_response"]
    assert "```text" in data["formatted_response"]
    assert "不代表目标 Agent 已经自动执行" in data["formatted_response"]


def test_list_agent_capabilities_reads_configured_agent_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    agent_dir = workspace / "agents" / "planner"
    agent_dir.mkdir(parents=True)
    config.mkdir()
    (agent_dir / "IDENTITY.md").write_text(
        "\n".join(
            [
                "# 计划拆解 Agent",
                "",
                "## 职责",
                "",
                "- 明确目标、边界、依赖和风险。",
                "- 拆成阶段任务。",
                "",
                "## 委派输入",
                "",
                "- `goal`：用户最终想达成的结果。",
                "- `constraints`：时间、环境、权限或风险。",
            ]
        ),
        encoding="utf-8",
    )
    (config / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "planner",
                        "name": "TaskPlanner",
                        "personality": "清晰",
                        "tool_policy": {"tool_names": ["save_task_plan"]},
                        "prompt_policy": {"prompt_dir": "agents/planner"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)

    result = registry.dispatch(
        "list_agent_capabilities",
        {"include_tools": True, "agent_ids": ["planner"]},
    )

    data = json.loads(result)
    assert data["type"] == "agent_capability_catalog"
    assert data["count"] == 1
    assert data["agents"][0]["id"] == "planner"
    assert data["agents"][0]["layer"] == "shared-capability"
    assert data["agents"][0]["duties"] == ["明确目标、边界、依赖和风险。", "拆成阶段任务。"]
    assert data["agents"][0]["handoff_inputs"] == [
        "`goal`：用户最终想达成的结果。",
        "`constraints`：时间、环境、权限或风险。",
    ]
    assert data["agents"][0]["tools"] == ["save_task_plan"]


def test_format_agent_capability_catalog_outputs_user_facing_directory(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    catalog = {
        "type": "agent_capability_catalog",
        "count": 2,
        "agents": [
            {
                "id": "planner",
                "name": "TaskPlanner",
                "layer": "shared-capability",
                "personality": "清晰、务实",
                "duties": ["拆解阶段任务。", "明确验收标准。"],
                "handoff_inputs": ["`goal`：用户目标。"],
                "tools": ["save_task_plan", "compose_repo_review_task_plan"],
            },
            {
                "id": "reviewer",
                "name": "RiskReviewer",
                "personality": "谨慎",
                "duties": ["审查风险门禁。"],
                "handoff_inputs": ["`plan_json`：计划 JSON。"],
                "tools": ["review_task_plan_gate"],
            },
        ],
    }

    result = registry.dispatch(
        "format_agent_capability_catalog",
        {
            "catalog_json": json.dumps(catalog, ensure_ascii=False),
            "focus_agent_id": "planner",
            "include_tools": True,
        },
    )

    assert result.startswith("# Agent 能力目录")
    assert "当前可展示 1 个 Agent" in result
    assert "## `planner` - TaskPlanner" in result
    assert "- 分层：shared-capability" in result
    assert "- 拆解阶段任务。" in result
    assert "- `goal`：用户目标。" in result
    assert "save_task_plan, compose_repo_review_task_plan" in result
    assert "reviewer" not in result
    assert "不代表目标 Agent 已经自动执行" in result


def test_match_agent_capability_recommends_agent_from_catalog(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    catalog = {
        "type": "agent_capability_catalog",
        "count": 2,
        "agents": [
            {
                "id": "doc-writer",
                "name": "DocWriter",
                "personality": "正式、清晰",
                "duties": ["生成 README、报告、手册和 Markdown 文档。"],
                "handoff_inputs": ["`source_material`：已有分析、计划或审查结论。"],
                "tools": ["render_repo_analysis_markdown", "save_markdown_report"],
            },
            {
                "id": "ops",
                "name": "GatewayOps",
                "personality": "只读排障",
                "duties": ["查看 Docker、日志和运行状态。"],
                "handoff_inputs": ["`symptom`：错误现象。"],
                "tools": ["ops_readonly_health"],
            },
        ],
    }

    data = json.loads(
        registry.dispatch(
            "match_agent_capability",
            {
                "user_goal": "把已有材料整理成 Markdown 报告",
                "catalog_json": json.dumps(catalog, ensure_ascii=False),
            },
        )
    )

    assert data["type"] == "agent_capability_match"
    assert data["recommended_agent_id"] == "doc-writer"
    assert data["confidence"] > 0.35
    assert data["matches"][0]["agent_id"] == "doc-writer"
    assert "文档" in data["matches"][0]["matched_terms"] or "报告" in data["matches"][0]["matched_terms"]
    assert "build_agent_handoff_prompt" in data["next_actions"][0]
    assert "不代表目标 Agent 已经自动执行" in data["boundary"]


def test_format_agent_capability_match_outputs_recommendation(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    match = {
        "type": "agent_capability_match",
        "user_goal": "把已有材料整理成 Markdown 报告",
        "recommended_agent_id": "doc-writer",
        "confidence": 0.71,
        "matches": [
            {
                "agent_id": "doc-writer",
                "name": "DocWriter",
                "score": 4,
                "matched_terms": ["文档", "报告"],
                "reason": "目标与该 Agent 的职责或工具命中：文档, 报告。",
                "handoff_inputs": ["`source_material`：已有分析、计划或审查结论。"],
            },
            {
                "agent_id": "planner",
                "name": "TaskPlanner",
                "score": 1,
                "matched_terms": ["计划"],
                "reason": "低置信备选。",
                "handoff_inputs": [],
            },
        ],
        "next_actions": ["调用 build_agent_handoff_prompt 生成交接提示。"],
        "boundary": "这是基于当前 Agent 能力目录的推荐，不代表目标 Agent 已经自动执行。",
    }

    result = registry.dispatch(
        "format_agent_capability_match",
        {"match_json": json.dumps(match, ensure_ascii=False)},
    )

    assert result.startswith("# Agent 推荐")
    assert "推荐 Agent：`doc-writer`" in result
    assert "置信度：0.71" in result
    assert "`source_material`：已有分析、计划或审查结论。" in result
    assert "| planner | 1 | 计划 |" in result
    assert "build_agent_handoff_prompt" in result
    assert "不代表目标 Agent 已经自动执行" in result


def test_compose_agent_handoff_package_builds_prompt_and_suggestion(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    match = {
        "type": "agent_capability_match",
        "user_goal": "把已有材料整理成 Markdown 报告",
        "recommended_agent_id": "doc-writer",
        "confidence": 0.71,
        "matches": [
            {
                "agent_id": "doc-writer",
                "name": "DocWriter",
                "score": 4,
                "matched_terms": ["文档", "报告"],
                "reason": "目标与该 Agent 的职责或工具命中：文档, 报告。",
                "handoff_inputs": ["`source_material`：已有分析、计划或审查结论。"],
            }
        ],
        "next_actions": ["调用 build_agent_handoff_prompt 生成交接提示。"],
        "boundary": "这是基于当前 Agent 能力目录的推荐，不代表目标 Agent 已经自动执行。",
    }

    data = json.loads(
        registry.dispatch(
            "compose_agent_handoff_package",
            {
                "user_goal": "把已有材料整理成 Markdown 报告",
                "match_json": json.dumps(match, ensure_ascii=False),
                "source_platform": "wework",
                "constraints": ["只整理已有材料，不补事实"],
                "expected_output": "Markdown 报告",
                "should_persist": True,
            },
        )
    )

    assert data["type"] == "agent_handoff_package"
    assert data["target_agent_id"] == "doc-writer"
    assert data["delegation_suggestion"]["target_agent_id"] == "doc-writer"
    assert data["delegation_suggestion"]["confidence"] == 0.71
    assert "目标 Agent：doc-writer" in data["handoff_prompt"]
    assert "只整理已有材料，不补事实" in data["handoff_prompt"]
    assert "Markdown 报告" in data["handoff_prompt"]
    assert "不要声称目标 Agent 已经自动执行" in data["next_actions"][1]
    assert "不代表目标 Agent 已经自动执行" in data["boundary"]


def test_format_agent_handoff_package_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    package = {
        "type": "agent_handoff_package",
        "user_goal": "把已有材料整理成 Markdown 报告",
        "target_agent_id": "doc-writer",
        "confidence": 0.71,
        "handoff_prompt": "目标 Agent：doc-writer\n\n## 用户原始目标\n把已有材料整理成 Markdown 报告",
        "delegation_suggestion": {
            "type": "agent_delegation_suggestion",
            "target_agent_id": "doc-writer",
            "reason": "目标与该 Agent 的职责命中：文档, 报告。",
            "confidence": 0.71,
        },
        "next_actions": [
            "把 handoff_prompt 交给目标 Agent，或复制给用户确认后继续。",
            "不要声称目标 Agent 已经自动执行。",
        ],
        "boundary": "这是入口层交接包，不代表目标 Agent 已经自动执行。",
    }

    result = registry.dispatch(
        "format_agent_handoff_package",
        {"package_json": json.dumps(package, ensure_ascii=False)},
    )

    assert result.startswith("# Agent 交接包")
    assert "目标 Agent：`doc-writer`" in result
    assert "置信度：0.71" in result
    assert "目标与该 Agent 的职责命中" in result
    assert "```text" in result
    assert "目标 Agent：doc-writer" in result
    assert "不要声称目标 Agent 已经自动执行" in result


def test_ops_readonly_health_reports_disk_and_key_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "reports").mkdir(parents=True)
    (workspace / "reports" / "example.md").write_text("hello", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)

    result = registry.dispatch("ops_readonly_health", {"include_sizes": True})

    data = json.loads(result)
    assert data["type"] == "ops_readonly_health"
    assert data["project_root"] == str(tmp_path)
    assert data["disk"]["total_bytes"] > 0
    assert data["disk"]["usage_percent"] >= 0
    paths = {row["name"]: row for row in data["paths"]}
    assert {"project", "workspace", "data", "config"}.issubset(paths)
    assert paths["workspace"]["exists"] is True
    assert paths["workspace"]["file_count"] == 1
    assert data["note"] == "只读采集结果；未执行 shell 命令，未修改文件。"


def test_summarize_ops_health_reports_normal_state(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    health = {
        "type": "ops_readonly_health",
        "generated_at": "2026-07-07T00:00:00+00:00",
        "project_root": str(tmp_path),
        "disk": {"usage_percent": 42.0, "free": "50.0 GiB"},
        "paths": [
            {"name": "workspace", "exists": True, "size_bytes": 1024, "size": "1.0 KiB", "file_count": 1}
        ],
        "risk_flags": [],
        "note": "只读采集结果；未执行 shell 命令，未修改文件。",
    }

    result = registry.dispatch(
        "summarize_ops_health",
        {"health_json": json.dumps(health, ensure_ascii=False)},
    )

    data = json.loads(result)
    assert data["type"] == "ops_health_summary"
    assert data["risk_level"] == "normal"
    assert "磁盘使用率 42.0%" in data["findings"][0]
    assert "保持当前巡检频率" in data["safe_recommendations"][0]
    assert "删除文件" in data["manual_confirmation_required"]


def test_summarize_ops_health_flags_missing_paths_and_disk_warning(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    health = {
        "type": "ops_readonly_health",
        "disk": {"usage_percent": 91.5, "free": "2.0 GiB"},
        "paths": [
            {"name": "workspace", "exists": False, "size_bytes": 0, "size": "0 B", "file_count": 0},
            {"name": "data", "exists": True, "size_bytes": 2 * 1024 * 1024 * 1024, "size": "2.0 GiB", "file_count": 100},
        ],
        "risk_flags": ["disk_critical", "missing_paths"],
    }

    data = json.loads(
        registry.dispatch(
            "summarize_ops_health",
            {"health_json": json.dumps(health, ensure_ascii=False)},
        )
    )

    assert data["risk_level"] == "critical"
    assert any("关键路径缺失：workspace" in item for item in data["findings"])
    assert any("较大目录：data 2.0 GiB" in item for item in data["findings"])
    assert "优先做只读空间定位" in data["safe_recommendations"][0]


def test_ops_runtime_diagnostics_reads_recent_events_and_failures(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    events = tmp_path / "data" / "events"
    alerts = tmp_path / "data" / "alerts"
    failed = tmp_path / "data" / "delivery-queue" / "failed"
    events.mkdir(parents=True)
    alerts.mkdir(parents=True)
    failed.mkdir(parents=True)
    (events / "runtime-events-2026-07-07.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "delivery.sent",
                        "component": "delivery",
                        "status": "ok",
                        "message": "Delivery sent",
                        "time": "2026-07-07T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "feishu.event.rejected",
                        "component": "feishu",
                        "status": "rejected",
                        "error": "method not allowed",
                        "message": "Feishu webhook request rejected",
                        "channel": "feishu",
                        "time": "2026-07-07T00:01:00+00:00",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (alerts / "history.jsonl").write_text(
        json.dumps({"event": "triggered", "rule": {"id": "delivery_failed"}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (failed / "delivery-1.json").write_text("{}", encoding="utf-8")
    registry = ToolRegistry()
    register_builtin_tools(registry, workspace)

    data = json.loads(registry.dispatch("ops_runtime_diagnostics", {"event_limit": 20}))

    assert data["type"] == "ops_runtime_diagnostics"
    assert data["risk_level"] == "warning"
    assert data["error_event_count"] == 1
    assert data["failed_delivery_count"] == 1
    assert data["error_by_component"] == {"feishu": 1}
    assert data["recent_errors"][0]["error"] == "method not allowed"
    assert any("飞书拒绝事件优先检查" in item for item in data["safe_recommendations"])
    assert "清空失败投递" in data["manual_confirmation_required"]


def test_ops_troubleshooting_plan_orders_health_and_runtime_checks(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    health = {
        "type": "ops_health_summary",
        "risk_level": "warning",
        "findings": ["磁盘使用率 86.0%，可用空间 8.0 GiB。"],
        "safe_recommendations": ["持续观察磁盘趋势，优先确认日志和构建产物增长。"],
        "manual_confirmation_required": ["删除文件", "清空日志", "重启服务"],
    }
    runtime = {
        "type": "ops_runtime_diagnostics",
        "risk_level": "critical",
        "failed_delivery_count": 12,
        "error_by_component": {"feishu": 2, "delivery": 4},
        "findings": ["本地失败投递文件 12 个。"],
        "safe_recommendations": ["先查看失败投递详情。"],
        "manual_confirmation_required": ["清空失败投递", "修改通道配置"],
    }

    result = registry.dispatch(
        "ops_troubleshooting_plan",
        {
            "health_summary_json": json.dumps(health, ensure_ascii=False),
            "runtime_diagnostics_json": json.dumps(runtime, ensure_ascii=False),
            "focus": "飞书投递失败",
        },
    )

    data = json.loads(result)
    assert data["type"] == "ops_troubleshooting_plan"
    assert data["risk_level"] == "critical"
    assert data["focus"] == "飞书投递失败"
    assert data["ordered_steps"][0]["area"] == "磁盘与关键路径"
    assert any(step["area"] == "可靠投递" and step["priority"] == "P0" for step in data["ordered_steps"])
    assert any(step["area"] == "飞书接入" for step in data["ordered_steps"])
    assert any("飞书投递失败" in command for command in data["safe_readonly_commands"])
    assert "清空失败投递" in data["manual_confirmation_required"]
    assert "不会自动执行清理" in data["note"]


def test_assess_risk_decision_scores_findings_and_actions(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "assess_risk_decision",
        {
            "review_target": "分布式入站队列方案",
            "findings": [
                {
                    "severity": "高",
                    "issue": "锁可能过期",
                    "impact": "同一会话可能并发执行",
                    "suggestion": "增加锁续期和任务接管。",
                },
                {
                    "severity": "low",
                    "issue": "文档缺少压测说明",
                    "impact": "容量边界不清晰",
                    "suggestion": "补充压测基线。",
                },
            ],
            "test_gaps": ["缺少 worker 崩溃恢复测试"],
            "residual_risks": ["模型调用耗时仍可能拖慢队列"],
            "evidence_level": "low",
        },
    )

    data = json.loads(result)
    assert data["type"] == "risk_decision_assessment"
    assert data["review_target"] == "分布式入站队列方案"
    assert data["decision"] == "有条件通过"
    assert data["risk_score"] == 58
    assert data["findings"][0]["severity"] == "high"
    assert data["priority_actions"][:2] == [
        "增加锁续期和任务接管。",
        "缺少 worker 崩溃恢复测试",
    ]


def test_format_risk_decision_assessment_outputs_user_facing_summary(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    assessment = registry.dispatch(
        "assess_risk_decision",
        {
            "review_target": "分布式入站队列方案",
            "findings": [
                {
                    "severity": "高",
                    "issue": "锁可能过期",
                    "impact": "同一会话可能并发执行",
                    "suggestion": "增加锁续期和任务接管。",
                },
                {
                    "severity": "low",
                    "issue": "文档缺少压测说明",
                    "impact": "容量边界不清晰",
                    "suggestion": "补充压测基线。",
                },
            ],
            "test_gaps": ["缺少 worker 崩溃恢复测试"],
            "residual_risks": ["模型调用耗时仍可能拖慢队列"],
            "evidence_level": "low",
        },
    )

    formatted = registry.dispatch(
        "format_risk_decision_assessment",
        {"assessment_json": assessment},
    )

    assert "## 风险决策评估" in formatted
    assert "- 结论：有条件通过" in formatted
    assert "- 审查对象：分布式入站队列方案" in formatted
    assert "- 风险分：58/100" in formatted
    assert "| 高 | 锁可能过期 | 同一会话可能并发执行 | 增加锁续期和任务接管。 |" in formatted
    assert "文档缺少压测说明" not in formatted
    assert "- 缺少 worker 崩溃恢复测试" in formatted
    assert "- 模型调用耗时仍可能拖慢队列" in formatted
    assert "- 增加锁续期和任务接管。" in formatted


def test_compose_research_brief_structures_sources_and_uncertainty(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "compose_research_brief",
        {
            "topic": "RabbitMQ 适合 Gateway 入站队列吗",
            "conclusion": "适合做可靠任务削峰，但会话串行仍需要额外协调层。",
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "fact": "RabbitMQ supports durable queues and acknowledgements.",
                },
                {
                    "title": "Redis docs",
                    "url": "https://redis.io/docs",
                    "fact": "Redis can be used for distributed coordination primitives.",
                },
            ],
            "uncertainty": ["具体吞吐需要本机压测确认"],
            "freshness": "2026-07-07 检索",
            "reusable_summary": "RabbitMQ 适合削峰，Redis 更适合轻量协调。",
        },
    )

    data = json.loads(result)
    assert data["type"] == "research_brief"
    assert data["evidence_level"] == "limited"
    assert data["sources"][0]["url"] == "https://www.rabbitmq.com/docs"
    assert data["uncertainty"] == ["具体吞吐需要本机压测确认"]
    assert data["reusable_summary"] == "RabbitMQ 适合削峰，Redis 更适合轻量协调。"


def test_format_research_brief_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    brief = registry.dispatch(
        "compose_research_brief",
        {
            "topic": "RabbitMQ 适合 Gateway 入站队列吗",
            "conclusion": "适合做可靠任务削峰，但会话串行仍需要额外协调层。",
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "fact": "RabbitMQ supports durable queues and acknowledgements.",
                }
            ],
            "uncertainty": ["具体吞吐需要本机压测确认"],
            "freshness": "2026-07-07 检索",
            "reusable_summary": "RabbitMQ 适合削峰，Redis 更适合轻量协调。",
        },
    )

    formatted = registry.dispatch("format_research_brief", {"brief_json": brief})

    assert "## 调研简报" in formatted
    assert "- 主题：RabbitMQ 适合 Gateway 入站队列吗" in formatted
    assert "- 证据等级：limited" in formatted
    assert "适合做可靠任务削峰" in formatted
    assert "| RabbitMQ docs | https://www.rabbitmq.com/docs | RabbitMQ supports durable queues and acknowledgements. |" in formatted
    assert "- 具体吞吐需要本机压测确认" in formatted
    assert "RabbitMQ 适合削峰，Redis 更适合轻量协调。" in formatted


def test_assess_research_confidence_scores_primary_sources(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "assess_research_confidence",
        {
            "topic": "RabbitMQ 是否适合可靠队列",
            "conclusion": "RabbitMQ 适合可靠任务削峰。",
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "source_type": "official",
                    "fact": "Supports durable queues and acknowledgements.",
                },
                {
                    "title": "RabbitMQ confirms guide",
                    "url": "https://www.rabbitmq.com/docs/confirms",
                    "source_type": "docs",
                    "fact": "Publisher confirms and consumer acknowledgements are documented.",
                },
            ],
            "time_sensitive": False,
        },
    )

    data = json.loads(result)
    assert data["type"] == "research_confidence_assessment"
    assert data["confidence"] == "high"
    assert data["source_count"] == 2
    assert data["sources"][0]["quality"] == "high"
    assert data["recommended_next_actions"] == ["可以基于当前证据形成可复用摘要。"]


def test_assess_research_confidence_marks_missing_time_sensitive_sources(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "assess_research_confidence",
            {
                "topic": "某公司当前 CEO",
                "conclusion": "",
                "sources": [],
                "time_sensitive": True,
            },
        )
    )

    assert data["confidence"] == "missing"
    assert data["confidence_score"] == 0
    assert "缺少可核验来源。" in data["uncertainty"]
    assert "确认来源发布时间或最后更新时间。" in data["recommended_next_actions"]


def test_format_research_confidence_assessment_outputs_user_facing_report(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    assessment = registry.dispatch(
        "assess_research_confidence",
        {
            "topic": "RabbitMQ 是否适合可靠队列",
            "conclusion": "RabbitMQ 适合可靠任务削峰。",
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "source_type": "official",
                    "fact": "Supports durable queues and acknowledgements.",
                }
            ],
            "uncertainty": ["仍需用本机压测确认容量边界"],
            "time_sensitive": True,
        },
    )

    formatted = registry.dispatch(
        "format_research_confidence_assessment",
        {"assessment_json": assessment},
    )

    assert "## 调研置信度评估" in formatted
    assert "- 主题：RabbitMQ 是否适合可靠队列" in formatted
    assert "- 置信度：" in formatted
    assert "- 时效敏感：是" in formatted
    assert "## 被评估结论" in formatted
    assert "RabbitMQ 适合可靠任务削峰。" in formatted
    assert "| RabbitMQ docs | https://www.rabbitmq.com/docs | high | Supports durable queues and acknowledgements. |" in formatted
    assert "- 仍需用本机压测确认容量边界" in formatted
    assert "确认来源发布时间或最后更新时间。" in formatted


def test_compose_research_evidence_pack_prepares_downstream_payload(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "compose_research_evidence_pack",
        {
            "topic": "RabbitMQ 入站队列选型",
            "research_question": "RabbitMQ 是否适合 Gateway 的企业级入站削峰？",
            "conclusion": "RabbitMQ 适合可靠削峰，但会话串行需要 Redis 或数据库协调。",
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "source_type": "official",
                    "fact": "RabbitMQ supports acknowledgements and durable queues.",
                },
                {
                    "title": "Redis docs",
                    "url": "https://redis.io/docs/latest/develop/use/patterns/distributed-locks/",
                    "source_type": "docs",
                    "fact": "Redis can provide distributed coordination primitives.",
                },
            ],
            "key_facts": ["RabbitMQ 负责削峰和可靠投递。", "会话串行需要额外协调层。"],
            "freshness": "2026-07-07 检索",
            "downstream_use": "供 planner 输出实施阶段，供 doc-writer 写技术选型文档。",
        },
    )

    data = json.loads(result)
    assert data["type"] == "research_evidence_pack"
    assert data["evidence_quality"] == "strong"
    assert data["source_count"] == 2
    assert data["primary_source_count"] == 2
    assert data["reusable_payload"]["question"] == "RabbitMQ 是否适合 Gateway 的企业级入站削峰？"
    assert data["reusable_payload"]["sources"][0]["url"] == "https://www.rabbitmq.com/docs"
    assert data["next_actions"] == [
        "可把 reusable_payload 交给 repo-analyzer、planner 或 doc-writer 复用。"
    ]


def test_compose_research_option_comparison_selects_recommended_option(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "compose_research_option_comparison",
        {
            "topic": "入站队列中间件选型",
            "decision_question": "Gateway 分布式入站削峰优先选择 RabbitMQ 还是 Redis？",
            "criteria": ["可靠投递", "削峰能力", "会话串行协同", "运维复杂度"],
            "options": [
                {
                    "name": "RabbitMQ",
                    "score": 86,
                    "strengths": ["确认机制和持久化队列成熟", "适合任务削峰"],
                    "weaknesses": ["会话级串行仍需额外协调层"],
                    "best_for": ["企业级可靠队列"],
                    "avoid_when": ["只需要极轻量本地协调"],
                    "evidence": ["官方文档覆盖 ack、durable queues。"],
                },
                {
                    "name": "Redis",
                    "score": 72,
                    "strengths": ["轻量，适合锁和 ready index"],
                    "weaknesses": ["不适合作为唯一可靠消息主干"],
                    "best_for": ["会话协调和幂等"],
                    "avoid_when": ["需要完整消息队列语义"],
                    "evidence": ["官方文档覆盖分布式锁模式。"],
                },
            ],
            "sources": [
                {
                    "title": "RabbitMQ docs",
                    "url": "https://www.rabbitmq.com/docs",
                    "source_type": "official",
                    "fact": "RabbitMQ documents acknowledgements and durable queues.",
                },
                {
                    "title": "Redis distributed locks",
                    "url": "https://redis.io/docs/latest/develop/use/patterns/distributed-locks/",
                    "source_type": "docs",
                    "fact": "Redis documents distributed lock patterns.",
                },
            ],
            "freshness": "2026-07-07 检索",
        },
    )

    data = json.loads(result)
    assert data["type"] == "research_option_comparison"
    assert data["recommended_option"] == "RabbitMQ"
    assert data["evidence_quality"] == "strong"
    assert data["primary_source_count"] == 2
    assert data["options"][0]["score"] == 86
    assert "把推荐方案「RabbitMQ」交给 planner 拆成验证计划。" in data["next_actions"]
    assert "reviewer" in data["downstream_use"]


def test_compose_research_brief_marks_missing_sources(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    data = json.loads(
        registry.dispatch(
            "compose_research_brief",
            {"topic": "未知事实", "conclusion": ""},
        )
    )

    assert data["evidence_level"] == "missing"
    assert "缺少可核验来源 URL。" in data["uncertainty"]
    assert "缺少明确结论。" in data["uncertainty"]


def test_bash_rewrites_host_workspace_absolute_path(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "bash",
        {
            "command": (
                "mkdir -p /home/obiah/Desktop/claw0/gateway/workspace/reports "
                "&& printf report > "
                "/home/obiah/Desktop/claw0/gateway/workspace/reports/from-bash.md"
            )
        },
    )

    assert result == "[exit=0]"
    assert (tmp_path / "reports" / "from-bash.md").read_text(encoding="utf-8") == "report"


def test_bash_rewrites_host_gateway_project_absolute_path(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)

    result = registry.dispatch(
        "bash",
        {
            "command": (
                "mkdir -p /home/obiah/Desktop/claw0/gateway/workspace/reports "
                "&& test -d /home/obiah/Desktop/claw0/gateway/workspace "
                "&& printf ok > /home/obiah/Desktop/claw0/gateway/workspace/reports/project-path.md"
            )
        },
    )

    assert result == "[exit=0]"
    assert (tmp_path / "reports" / "project-path.md").read_text(encoding="utf-8") == "ok"
