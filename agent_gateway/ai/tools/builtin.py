from __future__ import annotations

import json
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


def _markdown_section(title: str, content: str) -> str:
    """渲染一个 Markdown 二级章节。"""

    body = content.strip() or "待补充"
    return f"## {title}\n{body}"


def _normalize_document_type(value: str) -> str:
    """归一化文档类型。"""

    document_type = value.strip().lower().replace("_", "-")
    aliases = {
        "tech-report": "technical-report",
        "technical": "technical-report",
        "plan": "proposal",
        "方案": "proposal",
        "复盘": "retrospective",
    }
    return aliases.get(document_type, document_type)


def _extract_markdown_section(content: str, heading: str) -> str:
    """从 Markdown 中提取指定二级标题下的内容。"""

    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def _extract_markdown_list_items(section: str) -> list[str]:
    """从 Markdown 章节中提取一层列表项。"""

    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def _directory_size(path: Path, *, max_files: int = 20_000) -> tuple[int, int, bool]:
    """只读估算目录大小，返回字节数、扫描文件数和是否截断。"""

    if not path.exists():
        return 0, 0, False
    if path.is_file():
        return path.stat().st_size, 1, False

    total = 0
    count = 0
    truncated = False
    for root, _, files in os.walk(path):
        for filename in files:
            count += 1
            if count > max_files:
                truncated = True
                return total, count - 1, truncated
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total, count, truncated


def _format_bytes(value: int | float) -> str:
    """把字节数格式化为易读字符串。"""

    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _normalize_severity(value: str) -> str:
    """归一化风险严重级别。"""

    severity = str(value or "").strip().lower()
    aliases = {
        "critical": "critical",
        "blocker": "critical",
        "致命": "critical",
        "严重": "critical",
        "高": "high",
        "high": "high",
        "中": "medium",
        "medium": "medium",
        "低": "low",
        "low": "low",
    }
    return aliases.get(severity, "medium")


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

    def save_structured_document(
        title: str,
        document_type: str,
        summary: str,
        background: str = "",
        content: str = "",
        conclusions: list[str] | None = None,
        next_steps: list[str] | None = None,
        risks: list[str] | None = None,
        file_name: str = "",
    ) -> str:
        """按文档类型保存结构化 Markdown 文档，供 doc-writer 固定产出。"""

        normalized_type = _normalize_document_type(document_type)
        category_by_type = {
            "readme": "docs",
            "proposal": "proposals",
            "retrospective": "retrospectives",
            "technical-report": "technical-reports",
        }
        category = category_by_type.get(normalized_type, "general")
        sections_by_type = {
            "readme": [
                _markdown_section("项目简介", summary),
                _markdown_section("核心能力", content),
                _markdown_section("使用方式", "\n".join(next_steps or [])),
                _markdown_section("限制与注意事项", _markdown_list(risks or [])),
            ],
            "proposal": [
                _markdown_section("摘要", summary),
                _markdown_section("背景", background),
                _markdown_section("方案", content),
                _markdown_section("风险与限制", _markdown_list(risks or [])),
                _markdown_section("下一步", _markdown_list(next_steps or [])),
            ],
            "retrospective": [
                _markdown_section("摘要", summary),
                _markdown_section("完成情况", content),
                _markdown_section("问题与卡点", _markdown_list(risks or [])),
                _markdown_section("后续行动", _markdown_list(next_steps or [])),
            ],
            "technical-report": [
                _markdown_section("摘要", summary),
                _markdown_section("背景", background),
                _markdown_section("技术分析", content),
                _markdown_section("结论", _markdown_list(conclusions or [])),
                _markdown_section("风险与限制", _markdown_list(risks or [])),
                _markdown_section("下一步", _markdown_list(next_steps or [])),
            ],
        }
        sections = sections_by_type.get(
            normalized_type,
            [
                _markdown_section("摘要", summary),
                _markdown_section("背景", background),
                _markdown_section("主要内容", content),
                _markdown_section("结论", _markdown_list(conclusions or [])),
                _markdown_section("下一步", _markdown_list(next_steps or [])),
            ],
        )
        return save_markdown_report(
            title=title,
            content="\n\n".join(sections),
            category=category,
            file_name=file_name or title,
        )

    def suggest_agent_delegation(
        task_type: str,
        target_agent_id: str,
        reason: str,
        context_summary: str,
        handoff_prompt: str,
        confidence: float = 0.7,
        can_answer_briefly: bool = True,
    ) -> str:
        """生成入口 Agent 到能力 Agent 的结构化委派建议。"""

        normalized_confidence = max(0.0, min(1.0, float(confidence)))
        suggestion = {
            "type": "agent_delegation_suggestion",
            "task_type": task_type.strip(),
            "target_agent_id": target_agent_id.strip(),
            "reason": reason.strip(),
            "context_summary": context_summary.strip(),
            "handoff_prompt": handoff_prompt.strip(),
            "confidence": normalized_confidence,
            "can_answer_briefly": bool(can_answer_briefly),
            "status": "suggested",
            "note": "这是委派建议，不会自动调用目标 Agent。",
        }
        return json.dumps(suggestion, ensure_ascii=False, indent=2)

    def list_agent_capabilities(
        include_tools: bool = False,
        agent_ids: list[str] | None = None,
    ) -> str:
        """读取当前配置中的 Agent 能力目录，供入口 Agent 做委派判断。"""

        config_path = workspace_root.parent / "config" / "agents.json"
        if not config_path.exists():
            return "Error: config/agents.json not found"

        data = json.loads(config_path.read_text(encoding="utf-8"))
        wanted = {agent_id.strip() for agent_id in agent_ids or [] if agent_id.strip()}
        capabilities = []
        for agent in data.get("agents", []):
            agent_id = str(agent.get("id", "")).strip()
            if wanted and agent_id not in wanted:
                continue
            prompt_dir = str(agent.get("prompt_policy", {}).get("prompt_dir", "")).strip()
            identity_path = workspace_root / prompt_dir / "IDENTITY.md" if prompt_dir else None
            identity = ""
            if identity_path and identity_path.exists():
                identity = identity_path.read_text(encoding="utf-8")
            duties = _extract_markdown_list_items(_extract_markdown_section(identity, "职责"))
            handoff_inputs = _extract_markdown_list_items(
                _extract_markdown_section(identity, "委派输入")
            )
            row = {
                "id": agent_id,
                "name": agent.get("name", ""),
                "personality": agent.get("personality", ""),
                "prompt_dir": prompt_dir,
                "duties": duties[:8],
                "handoff_inputs": handoff_inputs[:8],
            }
            if include_tools:
                row["tools"] = agent.get("tool_policy", {}).get("tool_names", [])
            capabilities.append(row)

        return json.dumps(
            {
                "type": "agent_capability_catalog",
                "count": len(capabilities),
                "agents": capabilities,
            },
            ensure_ascii=False,
            indent=2,
        )

    def ops_readonly_health(include_sizes: bool = True) -> str:
        """生成只读运维健康简报，不执行 shell 命令。"""

        project_root = workspace_root.parent
        monitored_paths = {
            "project": project_root,
            "workspace": workspace_root,
            "data": project_root / "data",
            "config": project_root / "config",
        }
        disk = os.statvfs(project_root)
        total = disk.f_frsize * disk.f_blocks
        free = disk.f_frsize * disk.f_bavail
        used = max(0, total - free)
        usage_percent = round((used / total) * 100, 1) if total else 0.0

        paths = []
        for name, path in monitored_paths.items():
            exists = path.exists()
            row = {
                "name": name,
                "path": str(path),
                "exists": exists,
                "is_dir": path.is_dir() if exists else False,
                "size_bytes": 0,
                "size": "0 B",
                "file_count": 0,
                "truncated": False,
            }
            if exists and include_sizes:
                size_bytes, file_count, truncated = _directory_size(path)
                row.update(
                    {
                        "size_bytes": size_bytes,
                        "size": _format_bytes(size_bytes),
                        "file_count": file_count,
                        "truncated": truncated,
                    }
                )
            paths.append(row)

        risk_flags = []
        if usage_percent >= 90:
            risk_flags.append("disk_critical")
        elif usage_percent >= 80:
            risk_flags.append("disk_warning")
        missing = [row["name"] for row in paths if not row["exists"]]
        if missing:
            risk_flags.append("missing_paths")

        return json.dumps(
            {
                "type": "ops_readonly_health",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "project_root": str(project_root),
                "disk": {
                    "total_bytes": total,
                    "used_bytes": used,
                    "free_bytes": free,
                    "total": _format_bytes(total),
                    "used": _format_bytes(used),
                    "free": _format_bytes(free),
                    "usage_percent": usage_percent,
                },
                "paths": paths,
                "risk_flags": risk_flags,
                "note": "只读采集结果；未执行 shell 命令，未修改文件。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def assess_risk_decision(
        review_target: str,
        findings: list[dict[str, str]] | None = None,
        test_gaps: list[str] | None = None,
        residual_risks: list[str] | None = None,
        evidence_level: str = "medium",
    ) -> str:
        """把审查发现转换为稳定的风险评分和通过判定。"""

        findings = findings or []
        test_gaps = test_gaps or []
        residual_risks = residual_risks or []
        severity_weights = {"critical": 40, "high": 25, "medium": 12, "low": 5}
        normalized_findings = []
        score = 0
        for finding in findings:
            severity = _normalize_severity(str(finding.get("severity", "")))
            score += severity_weights[severity]
            normalized_findings.append(
                {
                    "severity": severity,
                    "issue": str(finding.get("issue", "")).strip(),
                    "impact": str(finding.get("impact", "")).strip(),
                    "suggestion": str(finding.get("suggestion", "")).strip(),
                }
            )
        score += min(len(test_gaps) * 8, 24)
        score += min(len(residual_risks) * 5, 15)
        if evidence_level.strip().lower() in {"low", "弱", "insufficient", "不足"}:
            score += 15
        score = min(score, 100)
        severities = {row["severity"] for row in normalized_findings}
        if "critical" in severities or score >= 75:
            decision = "不建议继续"
        elif "high" in severities or score >= 35 or test_gaps:
            decision = "有条件通过"
        else:
            decision = "通过"
        priority_actions = []
        for row in normalized_findings:
            if row["severity"] in {"critical", "high"} and row["suggestion"]:
                priority_actions.append(row["suggestion"])
        priority_actions.extend(str(item).strip() for item in test_gaps if str(item).strip())
        if not priority_actions and residual_risks:
            priority_actions.extend(str(item).strip() for item in residual_risks if str(item).strip())
        if not priority_actions:
            priority_actions.append("按现有方案推进，并保留回归验证记录。")

        return json.dumps(
            {
                "type": "risk_decision_assessment",
                "review_target": review_target.strip(),
                "risk_score": score,
                "decision": decision,
                "evidence_level": evidence_level.strip().lower() or "medium",
                "findings": normalized_findings,
                "test_gaps": [str(item).strip() for item in test_gaps if str(item).strip()],
                "residual_risks": [str(item).strip() for item in residual_risks if str(item).strip()],
                "priority_actions": priority_actions[:5],
            },
            ensure_ascii=False,
            indent=2,
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
            name="save_structured_document",
            description=(
                "Save a structured Markdown document for README, proposal, "
                "retrospective, or technical-report use cases."
            ),
            input_schema={
                "type": "object",
                "required": ["title", "document_type", "summary"],
                "properties": {
                    "title": {"type": "string"},
                    "document_type": {
                        "type": "string",
                        "enum": ["readme", "proposal", "retrospective", "technical-report"],
                    },
                    "summary": {"type": "string"},
                    "background": {"type": "string"},
                    "content": {"type": "string"},
                    "conclusions": {"type": "array", "items": {"type": "string"}},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "file_name": {"type": "string"},
                },
            },
            handler=save_structured_document,
            tags=("filesystem", "write", "report", "document"),
        )
    )
    registry.register(
        RegisteredTool(
            name="suggest_agent_delegation",
            description=(
                "Create a structured delegation suggestion from an entry agent "
                "to a specialized capability or personal agent. This does not "
                "execute the target agent."
            ),
            input_schema={
                "type": "object",
                "required": [
                    "task_type",
                    "target_agent_id",
                    "reason",
                    "context_summary",
                    "handoff_prompt",
                ],
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Task category, for example repo-analysis, planning, review, personal-secretary.",
                    },
                    "target_agent_id": {
                        "type": "string",
                        "description": "Recommended target agent id.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason why the target agent is better suited.",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Relevant user intent and context to pass to the target agent.",
                    },
                    "handoff_prompt": {
                        "type": "string",
                        "description": "A ready-to-send prompt for the target agent.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Confidence of the routing suggestion.",
                    },
                    "can_answer_briefly": {
                        "type": "boolean",
                        "description": "Whether the entry agent can provide a short interim answer.",
                    },
                },
            },
            handler=suggest_agent_delegation,
            tags=("agent", "delegation", "routing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="list_agent_capabilities",
            description=(
                "List configured agent capabilities and handoff input fields "
                "from config/agents.json and workspace/agents/* prompts."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_tools": {
                        "type": "boolean",
                        "description": "Whether to include each agent's allowed tool names.",
                    },
                    "agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional agent ids to filter.",
                    },
                },
            },
            handler=list_agent_capabilities,
            tags=("agent", "catalog", "routing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="ops_readonly_health",
            description=(
                "Collect a read-only Gateway health summary using Python file "
                "metadata only: disk usage and key project directory sizes."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_sizes": {
                        "type": "boolean",
                        "description": "Whether to scan key directories for size and file counts.",
                    }
                },
            },
            handler=ops_readonly_health,
            tags=("ops", "health", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="assess_risk_decision",
            description=(
                "Assess risk findings into a stable risk score, release decision, "
                "and prioritized mitigation actions."
            ),
            input_schema={
                "type": "object",
                "required": ["review_target"],
                "properties": {
                    "review_target": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {
                                    "type": "string",
                                    "description": "critical/high/medium/low, or Chinese equivalents.",
                                },
                                "issue": {"type": "string"},
                                "impact": {"type": "string"},
                                "suggestion": {"type": "string"},
                            },
                        },
                    },
                    "test_gaps": {"type": "array", "items": {"type": "string"}},
                    "residual_risks": {"type": "array", "items": {"type": "string"}},
                    "evidence_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "insufficient"],
                    },
                },
            },
            handler=assess_risk_decision,
            tags=("review", "risk", "decision"),
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
