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
