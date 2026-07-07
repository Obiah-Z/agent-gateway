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
