from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    candidate = (workspace_root / raw_path).resolve()
    candidate.relative_to(workspace_root.resolve())
    return candidate


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


def register_builtin_tools(
    registry: ToolRegistry,
    workspace_root: Path,
    *,
    max_output_chars: int = 50_000,
    default_timeout: int = 30,
) -> None:
    def read_file(file_path: str) -> str:
        path = _resolve_workspace_path(workspace_root, file_path)
        if not path.exists():
            return f"Error: file not found: {file_path}"
        return _truncate(path.read_text(encoding="utf-8"), max_output_chars)

    def write_file(file_path: str, content: str) -> str:
        path = _resolve_workspace_path(workspace_root, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path.relative_to(workspace_root)}"

    def list_directory(directory: str = ".") -> str:
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
                    "content": {"type": "string"},
                },
            },
            handler=write_file,
            tags=("filesystem", "write"),
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
