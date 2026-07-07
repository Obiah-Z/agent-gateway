from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    """把工具传入路径解析到 workspace 内，并阻止越界访问。"""

    root = workspace_root.resolve()
    normalized = _normalize_workspace_path(workspace_root, raw_path)
    candidate = normalized.resolve() if normalized.is_absolute() else (root / normalized).resolve()
    candidate.relative_to(root)
    return candidate


def _normalize_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    """把常见 workspace 路径别名转换为当前运行环境的 workspace 路径。"""

    value = str(raw_path or "").strip()
    if not value:
        return Path(".")
    path = Path(value)
    root = workspace_root.resolve()
    if path.is_absolute():
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            pass
        parts = path.parts
        if "workspace" in parts and "gateway" in parts:
            workspace_index = parts.index("workspace")
            suffix = Path(*parts[workspace_index + 1 :])
            return root / suffix
        return path
    if path.parts and path.parts[0] == "workspace":
        return root / Path(*path.parts[1:])
    return path


def _rewrite_workspace_aliases(command: str, workspace_root: Path) -> str:
    """把 shell 命令中的宿主机 gateway/workspace 绝对路径改写为当前运行路径。"""

    workspace = str(workspace_root.resolve())
    gateway = str(workspace_root.resolve().parent)

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        marker = "/gateway/workspace"
        if marker in raw:
            suffix = raw.split(marker, 1)[1]
            return f"{workspace}{suffix}"
        suffix = raw.split("/gateway", 1)[1]
        return f"{gateway}{suffix}"

    # Docker 内的 worker 不应该把宿主机绝对路径写进容器私有目录；项目根路径和
    # workspace 路径都要重写，否则模型用宿主机路径 ls/cat 会白白消耗工具轮次。
    return re.sub(r"/[^\s'\"`]*?/gateway(?:/workspace)?(?:/[^\s'\"`]*)?", replace, command)


def _truncate(text: str, limit: int) -> str:
    """截断过长工具输出，避免撑爆上下文。"""

    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


def _slugify_report_name(value: str) -> str:
    """把模型给出的标题转换为安全文件名，保留中文标题可读性。"""

    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", value.strip())
    name = re.sub(r"\s+", "-", name).strip(" .-")
    name = re.sub(r"-+", "-", name).strip(" .-")
    return name or "未命名报告"


def _markdown_list(items: list[str]) -> str:
    """把字符串列表渲染成 Markdown 列表。"""

    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in cleaned)


def register_builtin_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    *,
    max_output_chars: int = 50_000,
    default_timeout: int = 30,
) -> None:
    """注册网关内置工具集。"""

    def read_file(file_path: str) -> str:
        """读取 workspace 内单个文件。"""

        path = _resolve_workspace_path(workspace_root, file_path)
        if not path.exists():
            return f"Error: file not found: {file_path}"
        return _truncate(path.read_text(encoding="utf-8"), max_output_chars)

    def write_file(file_path: str = "", content: str = "", path: str = "") -> str:
        """写入 workspace 内文件。"""

        # Some models use the common `path` argument name even when the schema says `file_path`.
        file_path = file_path or path
        if not file_path:
            return "Error: write_file requires file_path"
        path = _resolve_workspace_path(workspace_root, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path.relative_to(workspace_root)}"

    def save_markdown_report(
        title: str,
        content: str,
        category: str = "general",
        file_name: str = "",
    ) -> str:
        """按 Gateway 约定保存 Markdown 报告，并返回可被通道附件逻辑识别的路径。"""

        safe_category = _slugify_report_name(category).lower()
        safe_file_name = _slugify_report_name(file_name or title)
        if not safe_file_name.endswith(".md"):
            safe_file_name += ".md"

        path = _resolve_workspace_path(
            workspace_root,
            str(Path("reports") / safe_category / safe_file_name),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        stripped = content.lstrip()
        document = content if stripped.startswith("# ") else f"# {title}\n\n{content}"
        path.write_text(document, encoding="utf-8")
        relative = path.relative_to(workspace_root)
        return f"报告路径：workspace/{relative}"

    def save_task_plan(
        title: str,
        goal: str,
        scope: str = "",
        phases: list[dict[str, str]] | None = None,
        risks: list[str] | None = None,
        next_steps: list[str] | None = None,
        file_name: str = "",
    ) -> str:
        """保存结构化计划文档，供 planner 固定产出格式。"""

        rows = []
        for phase in phases or []:
            rows.append(
                "| {name} | {task} | {output} | {done} |".format(
                    name=str(phase.get("name", "")).replace("|", "\\|"),
                    task=str(phase.get("task", "")).replace("|", "\\|"),
                    output=str(phase.get("output", "")).replace("|", "\\|"),
                    done=str(phase.get("done", "") or phase.get("acceptance", "")).replace("|", "\\|"),
                )
            )
        phase_table = "\n".join(
            [
                "| 阶段 | 任务 | 输出 | 完成标准 |",
                "|---|---|---|---|",
                *(rows or ["| 待拆分 | 待明确 | 待明确 | 待明确 |"]),
            ]
        )
        content = "\n\n".join(
            [
                f"## 目标\n{goal or '待明确'}",
                f"## 边界\n{scope or '待明确'}",
                f"## 阶段计划\n{phase_table}",
                f"## 风险\n{_markdown_list(risks or [])}",
                f"## 下一步\n{_markdown_list(next_steps or [])}",
            ]
        )
        return save_markdown_report(
            title=title,
            content=content,
            category="plans",
            file_name=file_name or title,
        )

    def save_review_report(
        title: str,
        conclusion: str,
        findings: list[dict[str, str]] | None = None,
        test_gaps: list[str] | None = None,
        residual_risks: list[str] | None = None,
        file_name: str = "",
    ) -> str:
        """保存结构化审查报告，供 reviewer 固定产出格式。"""

        rows = []
        for finding in findings or []:
            rows.append(
                "| {severity} | {issue} | {impact} | {suggestion} |".format(
                    severity=str(finding.get("severity", "")).replace("|", "\\|"),
                    issue=str(finding.get("issue", "")).replace("|", "\\|"),
                    impact=str(finding.get("impact", "")).replace("|", "\\|"),
                    suggestion=str(finding.get("suggestion", "")).replace("|", "\\|"),
                )
            )
        findings_table = "\n".join(
            [
                "| 严重级别 | 问题 | 影响 | 建议 |",
                "|---|---|---|---|",
                *(rows or ["| 无 | 未发现明确问题 | 无 | 继续按测试结果复核 |"]),
            ]
        )
        content = "\n\n".join(
            [
                f"## 审查结论\n{conclusion or '待明确'}",
                f"## 主要问题\n{findings_table}",
                f"## 测试缺口\n{_markdown_list(test_gaps or [])}",
                f"## 残余风险\n{_markdown_list(residual_risks or [])}",
            ]
        )
        return save_markdown_report(
            title=title,
            content=content,
            category="reviews",
            file_name=file_name or title,
        )

    def list_directory(directory: str = ".") -> str:
        """列出 workspace 子目录内容。"""

        path = _resolve_workspace_path(workspace_root, directory)
        if not path.exists():
            return f"Error: directory not found: {directory}"
        if not path.is_dir():
            return f"Error: not a directory: {directory}"
        entries = []
        for child in sorted(path.iterdir()):
            marker = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{marker}")
        return "\n".join(entries[:1000])

    def bash(command: str, timeout: int = default_timeout) -> str:
        """在 workspace 内执行一条 shell 命令。"""

        command = _rewrite_workspace_aliases(command, workspace_root)
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        output = completed.stdout
        if completed.stderr:
            output += ("\n" if output else "") + completed.stderr
        if not output:
            output = f"[exit={completed.returncode}]"
        return _truncate(output, max_output_chars)

    def get_current_time() -> str:
        """返回当前 UTC 时间。"""

        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    registry.register(
        RegisteredTool(
            name="read_file",
            description="Read a file from the configured workspace.",
            input_schema={
                "type": "object",
                "required": ["file_path"],
                "properties": {"file_path": {"type": "string"}},
            },
            handler=read_file,
            tags=("filesystem", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="write_file",
            description="Write text content to a file in the workspace.",
            input_schema={
                "type": "object",
                "required": ["file_path", "content"],
                "properties": {
                    "file_path": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Compatibility alias for file_path. Prefer file_path.",
                    },
                    "content": {"type": "string"},
                },
            },
            handler=write_file,
            tags=("filesystem", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="save_markdown_report",
            description=(
                "Save a Markdown report under workspace/reports/<category>/ and return "
                "a report path that messaging channels can attach."
            ),
            input_schema={
                "type": "object",
                "required": ["title", "content"],
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "category": {
                        "type": "string",
                        "description": "Report category folder, for example github-repos, plans, reviews.",
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Optional Markdown filename. Chinese names are supported.",
                    },
                },
            },
            handler=save_markdown_report,
            tags=("filesystem", "write", "report"),
        )
    )
    registry.register(
        RegisteredTool(
            name="save_task_plan",
            description="Save a structured task plan Markdown document under workspace/reports/plans/.",
            input_schema={
                "type": "object",
                "required": ["title", "goal"],
                "properties": {
                    "title": {"type": "string"},
                    "goal": {"type": "string"},
                    "scope": {"type": "string"},
                    "phases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "task": {"type": "string"},
                                "output": {"type": "string"},
                                "done": {"type": "string"},
                                "acceptance": {"type": "string"},
                            },
                        },
                    },
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                    "file_name": {"type": "string"},
                },
            },
            handler=save_task_plan,
            tags=("filesystem", "write", "report", "plan"),
        )
    )
    registry.register(
        RegisteredTool(
            name="save_review_report",
            description="Save a structured risk review Markdown document under workspace/reports/reviews/.",
            input_schema={
                "type": "object",
                "required": ["title", "conclusion"],
                "properties": {
                    "title": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {"type": "string"},
                                "issue": {"type": "string"},
                                "impact": {"type": "string"},
                                "suggestion": {"type": "string"},
                            },
                        },
                    },
                    "test_gaps": {"type": "array", "items": {"type": "string"}},
                    "residual_risks": {"type": "array", "items": {"type": "string"}},
                    "file_name": {"type": "string"},
                },
            },
            handler=save_review_report,
            tags=("filesystem", "write", "report", "review"),
        )
    )
    registry.register(
        RegisteredTool(
            name="list_directory",
            description="List files and folders under a workspace directory.",
            input_schema={
                "type": "object",
                "properties": {"directory": {"type": "string"}},
            },
            handler=list_directory,
            tags=("filesystem", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="bash",
            description="Run a shell command inside the configured workspace.",
            input_schema={
                "type": "object",
                "required": ["command"],
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
            },
            handler=bash,
            tags=("shell", "exec"),
        )
    )
    registry.register(
        RegisteredTool(
            name="get_current_time",
            description="Get the current UTC date and time.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda: get_current_time(),
            tags=("utility",),
        )
    )
