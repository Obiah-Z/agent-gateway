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
        "planner",
        "reviewer",
        "doc-writer",
    ]
    assert data["handoff_sequence"][0]["input_contract"]["constraints"] == [
        "只做分析和计划，不直接改代码"
    ]
    assert "不会自动调用任何 Agent" in data["next_actions"][2]
    assert "不代表任何 Agent 已经执行" in data["note"]


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
    assert data["can_answer_directly"] is False
    assert data["confidence"] >= 0.67


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
    assert data["agents"][0]["duties"] == ["明确目标、边界、依赖和风险。", "拆成阶段任务。"]
    assert data["agents"][0]["handoff_inputs"] == [
        "`goal`：用户最终想达成的结果。",
        "`constraints`：时间、环境、权限或风险。",
    ]
    assert data["agents"][0]["tools"] == ["save_task_plan"]


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
