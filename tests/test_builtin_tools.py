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
