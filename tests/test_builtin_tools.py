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
