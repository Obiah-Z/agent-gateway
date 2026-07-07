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


def _markdown_bullets(items: list[object] | None) -> str:
    """把任意列表渲染为 Markdown bullet list。"""

    cleaned = [str(item).strip() for item in items or [] if str(item).strip()]
    if not cleaned:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in cleaned)


def _markdown_table_cell(value: object) -> str:
    """转义 Markdown 表格单元格内容。"""

    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.replace("|", "\\|") or "待补充"


def _markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    """渲染简单 Markdown 表格。"""

    if not rows:
        rows = [["暂无"] * len(headers)]
    header = "| " + " | ".join(_markdown_table_cell(item) for item in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_markdown_table_cell(item) for item in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


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


def _read_jsonl_tail(path: Path, *, limit: int) -> list[dict[str, object]]:
    """读取 JSONL 文件尾部并容错解析。"""

    if not path.exists() or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, object]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


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


def _clean_strings(items: list[str] | None) -> list[str]:
    """清理字符串列表中的空值。"""

    return [str(item).strip() for item in items or [] if str(item).strip()]


def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    """按关键词命中数给意图分类打分。"""

    return sum(1 for keyword in keywords if keyword and keyword in text)


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

    def structure_task_breakdown(
        goal: str,
        scope: str = "",
        phases: list[dict[str, str]] | None = None,
        constraints: list[str] | None = None,
        risks: list[str] | None = None,
        current_state: str = "",
    ) -> str:
        """把计划草稿整理成稳定的阶段、缺口和下一步。"""

        normalized_phases = []
        missing_acceptance = []
        missing_outputs = []
        for index, phase in enumerate(phases or [], start=1):
            name = str(phase.get("name", "") or f"阶段 {index}").strip()
            task = str(phase.get("task", "")).strip()
            output = str(phase.get("output", "")).strip()
            done = str(phase.get("done", "") or phase.get("acceptance", "")).strip()
            row = {
                "name": name,
                "task": task or "待明确",
                "output": output or "待明确",
                "done": done or "待明确",
            }
            if not output:
                missing_outputs.append(name)
            if not done:
                missing_acceptance.append(name)
            normalized_phases.append(row)

        if not normalized_phases:
            normalized_phases = [
                {
                    "name": "阶段一",
                    "task": "明确目标、边界、输入材料和完成标准",
                    "output": "可执行任务定义",
                    "done": "目标、边界、依赖和验收标准均已写清",
                }
            ]

        next_steps = []
        first = normalized_phases[0]
        next_steps.append(f"先执行「{first['name']}」：{first['task']}")
        if missing_outputs:
            next_steps.append(f"补齐这些阶段的输出物：{', '.join(missing_outputs[:3])}")
        if missing_acceptance:
            next_steps.append(f"补齐这些阶段的完成标准：{', '.join(missing_acceptance[:3])}")

        readiness = "ready"
        if missing_acceptance or missing_outputs:
            readiness = "needs_refinement"
        if not goal.strip():
            readiness = "blocked"
            next_steps.insert(0, "先补充明确目标。")

        return json.dumps(
            {
                "type": "task_breakdown",
                "goal": goal.strip(),
                "scope": scope.strip(),
                "current_state": current_state.strip(),
                "constraints": _clean_strings(constraints),
                "risks": _clean_strings(risks),
                "phases": normalized_phases,
                "readiness": readiness,
                "gaps": {
                    "missing_outputs": missing_outputs,
                    "missing_acceptance": missing_acceptance,
                },
                "next_steps": next_steps[:5],
            },
            ensure_ascii=False,
            indent=2,
        )

    def plan_execution_stage(
        objective: str,
        current_state: str = "",
        scope: str = "",
        dependencies: list[str] | None = None,
        risks: list[str] | None = None,
        acceptance_checks: list[str] | None = None,
        commit_strategy: str = "每完成一个可验证小阶段提交一次",
        next_actions: list[str] | None = None,
    ) -> str:
        """生成工程小阶段执行计划，补齐依赖、风险、验收和提交节奏。"""

        objective = objective.strip()
        deps = _clean_strings(dependencies)
        risk_items = _clean_strings(risks)
        checks = _clean_strings(acceptance_checks)
        actions = _clean_strings(next_actions)

        readiness = "ready"
        gaps = []
        if not objective:
            readiness = "blocked"
            gaps.append("缺少 objective。")
        if not checks:
            readiness = "needs_refinement" if readiness != "blocked" else readiness
            gaps.append("缺少 acceptance_checks。")
            checks = ["补充可执行测试或人工验收标准。"]
        if not actions:
            actions = ["确认目标和边界。", "实现最小可验证改动。", "运行相关测试并提交。"]

        return json.dumps(
            {
                "type": "execution_stage_plan",
                "objective": objective,
                "current_state": current_state.strip(),
                "scope": scope.strip() or "待明确",
                "dependencies": deps,
                "risks": risk_items,
                "acceptance_checks": checks,
                "commit_strategy": commit_strategy.strip()
                or "每完成一个可验证小阶段提交一次",
                "readiness": readiness,
                "gaps": gaps,
                "next_actions": actions[:6],
            },
            ensure_ascii=False,
            indent=2,
        )

    def adapt_adoption_plan_to_task_plan(
        adoption_plan_json: str,
        title: str = "",
        scope: str = "",
    ) -> str:
        """把 repo-analyzer 的采纳路线图转换成 planner 阶段计划。"""

        if not adoption_plan_json.strip():
            return "Error: adoption_plan_json is required"
        data = json.loads(adoption_plan_json)
        if not isinstance(data, dict):
            return "Error: adoption_plan_json must be a JSON object"
        if data.get("type") != "github_repo_adoption_plan":
            return "Error: adoption_plan_json type must be github_repo_adoption_plan"

        repository = str(data.get("repository", "")).strip() or "unknown repository"
        decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}
        risk_gates = _clean_strings(data.get("risk_gates") if isinstance(data.get("risk_gates"), list) else [])
        acceptance_checks = _clean_strings(
            data.get("acceptance_checks") if isinstance(data.get("acceptance_checks"), list) else []
        )
        phases = []
        for index, stage in enumerate(data.get("stages") or [], start=1):
            if not isinstance(stage, dict):
                continue
            tasks = _clean_strings(stage.get("tasks") if isinstance(stage.get("tasks"), list) else [])
            objective = str(stage.get("objective", "")).strip()
            phase_title = str(stage.get("title", "") or f"阶段 {index}").strip()
            phases.append(
                {
                    "name": phase_title,
                    "task": "；".join(tasks) if tasks else objective or "待明确",
                    "output": objective or "阶段输出待明确",
                    "done": "；".join(acceptance_checks[:2]) if acceptance_checks else "补充可执行验收标准",
                }
            )
        if not phases:
            phases.append(
                {
                    "name": "阶段一",
                    "task": "复核采纳路线图并补齐阶段任务",
                    "output": "可执行阶段计划",
                    "done": "阶段、输出和验收标准均已明确",
                }
            )

        next_steps = _clean_strings(data.get("acceptance_checks") if isinstance(data.get("acceptance_checks"), list) else [])
        if risk_gates:
            next_steps.insert(0, f"先通过风险门槛：{risk_gates[0]}")
        next_steps.insert(0, "将该计划写入 PROJECT_PLAN 或 reports/plans 后，再进入实现。")

        return json.dumps(
            {
                "type": "task_plan_from_adoption",
                "title": title.strip() or f"{repository} 采纳计划",
                "goal": data.get("adoption_goal") or f"评估并分阶段采纳 {repository} 的可借鉴设计。",
                "scope": scope.strip()
                or "只规划采纳路线和最小验证阶段，不直接引入依赖或修改生产配置。",
                "repository": repository,
                "decision": decision,
                "phases": phases[:6],
                "risks": risk_gates,
                "next_steps": next_steps[:6],
                "save_task_plan_args": {
                    "title": title.strip() or f"{repository} 采纳计划",
                    "goal": data.get("adoption_goal") or f"评估并分阶段采纳 {repository} 的可借鉴设计。",
                    "scope": scope.strip()
                    or "只规划采纳路线和最小验证阶段，不直接引入依赖或修改生产配置。",
                    "phases": phases[:6],
                    "risks": risk_gates,
                    "next_steps": next_steps[:6],
                },
                "note": "这是从 repo-analyzer 采纳路线图转换出的 planner 阶段计划草案，可直接传给 save_task_plan。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def compose_repo_review_task_plan(
        repo_analysis_json: str,
        risk_gate_json: str = "",
        adoption_plan_json: str = "",
        title: str = "",
        scope: str = "",
    ) -> str:
        """把仓库分析、风险门禁和可选采纳路线整合成 planner 阶段计划。"""

        if not repo_analysis_json.strip():
            return "Error: repo_analysis_json is required"
        analysis = json.loads(repo_analysis_json)
        if not isinstance(analysis, dict):
            return "Error: repo_analysis_json must be a JSON object"
        if analysis.get("type") != "github_repo_analysis":
            return "Error: repo_analysis_json type must be github_repo_analysis"

        risk_gate: dict[str, Any] = {}
        if risk_gate_json.strip():
            parsed_gate = json.loads(risk_gate_json)
            if not isinstance(parsed_gate, dict):
                return "Error: risk_gate_json must be a JSON object"
            if parsed_gate.get("type") != "github_repo_risk_gate_review":
                return "Error: risk_gate_json type must be github_repo_risk_gate_review"
            risk_gate = parsed_gate

        adoption_plan: dict[str, Any] = {}
        if adoption_plan_json.strip():
            parsed_adoption = json.loads(adoption_plan_json)
            if not isinstance(parsed_adoption, dict):
                return "Error: adoption_plan_json must be a JSON object"
            if parsed_adoption.get("type") != "github_repo_adoption_plan":
                return "Error: adoption_plan_json type must be github_repo_adoption_plan"
            adoption_plan = parsed_adoption

        repository = str(analysis.get("repository") or risk_gate.get("review_target") or "unknown repository")
        gateway_fit = analysis.get("gateway_fit") if isinstance(analysis.get("gateway_fit"), dict) else {}
        gate_decision = str(risk_gate.get("decision") or "").strip()
        adoption_decision = (
            adoption_plan.get("decision") if isinstance(adoption_plan.get("decision"), dict) else {}
        )
        source_decision = str(
            adoption_decision.get("action") or risk_gate.get("source_decision") or "unknown"
        ).strip()
        risk_actions = _clean_strings(
            risk_gate.get("next_actions") if isinstance(risk_gate.get("next_actions"), list) else []
        )
        analysis_recommendations = _clean_strings(
            analysis.get("recommendations") if isinstance(analysis.get("recommendations"), list) else []
        )
        reuse_ideas = _clean_strings(
            analysis.get("gateway_reuse_ideas") if isinstance(analysis.get("gateway_reuse_ideas"), list) else []
        )
        risk_items = _clean_strings(analysis.get("risks") if isinstance(analysis.get("risks"), list) else [])
        risk_items.extend(risk_actions[:4])

        phases = [
            {
                "name": "证据与门禁复核",
                "task": "复核仓库分析、许可证、维护状态、风险门禁结论和预期用途。",
                "output": "可追溯的采纳证据摘要和 go / conditional-go / no-go 判断记录。",
                "done": "仓库分析、风险扫描和门禁结论均已落盘，未通过项已有处理动作。",
            }
        ]
        if gate_decision == "no-go" or source_decision in {"hold", "block"}:
            phases.append(
                {
                    "name": "阻塞项处理",
                    "task": "处理许可证、归档状态、高危风险或缺失证据等阻塞项；未解除前不进入实现。",
                    "output": "阻塞项处理记录和是否继续推进的人工确认。",
                    "done": "高危阻塞项已关闭，或明确记录为暂缓采纳。",
                }
            )
        else:
            seeds = reuse_ideas or analysis_recommendations or ["选择一个最小可验证借鉴点。"]
            phases.append(
                {
                    "name": "最小实验拆解",
                    "task": f"围绕「{seeds[0]}」拆成不超过半天的 Gateway 小实验。",
                    "output": "最小实验设计、影响范围和回滚方式。",
                    "done": "实验边界、测试命令和回滚路径已明确。",
                }
            )
            phases.append(
                {
                    "name": "实现与验收",
                    "task": "按最小实验实现改动，补充聚焦测试或人工验收记录。",
                    "output": "代码或文档改动、测试结果和阶段总结。",
                    "done": "相关测试通过，变更已按阶段提交。",
                }
            )

        next_steps = []
        if gate_decision == "no-go":
            next_steps.append("先处理仓库风险门禁未通过项，不进入实现。")
        elif gate_decision == "conditional-go":
            next_steps.append("只允许进入最小验证，不直接引入生产依赖。")
        elif gate_decision == "go":
            next_steps.append("可以进入最小实验拆解，仍需保留回滚路径。")
        next_steps.extend(risk_actions[:3])
        next_steps.extend(analysis_recommendations[:3])
        if not next_steps:
            next_steps.append("先把计划写入 reports/plans，再选择第一阶段执行。")

        acceptance_checks = [
            "形成一份包含分析、风险门禁和采纳判断的执行记录。",
            "每个实现阶段必须有聚焦测试、人工验收或回滚说明。",
        ]
        if gate_decision:
            acceptance_checks.insert(0, f"风险门禁结论已处理：{gate_decision}。")

        plan_title = title.strip() or f"{repository} 仓库采纳执行计划"
        plan_scope = scope.strip() or "只把已分析和已审查的仓库结论转成执行计划，不直接引入依赖或修改生产配置。"
        result = {
            "type": "task_plan_from_repo_review",
            "title": plan_title,
            "goal": f"基于仓库分析和风险门禁，判断并分阶段采纳 {repository} 的可借鉴设计。",
            "scope": plan_scope,
            "repository": repository,
            "fit": {
                "score": gateway_fit.get("score", 0),
                "priority": gateway_fit.get("priority", "unknown"),
                "signals": gateway_fit.get("signals") or [],
            },
            "decision": {
                "risk_gate": gate_decision or "missing",
                "source_adoption": source_decision,
                "recommended_action": "hold"
                if gate_decision == "no-go" or source_decision in {"hold", "block"}
                else "pilot",
            },
            "phases": phases[:5],
            "risks": list(dict.fromkeys(risk_items))[:8],
            "acceptance_checks": acceptance_checks,
            "next_steps": list(dict.fromkeys(next_steps))[:6],
            "save_task_plan_args": {
                "title": plan_title,
                "goal": f"基于仓库分析和风险门禁，判断并分阶段采纳 {repository} 的可借鉴设计。",
                "scope": plan_scope,
                "phases": phases[:5],
                "risks": list(dict.fromkeys(risk_items))[:8],
                "next_steps": list(dict.fromkeys(next_steps))[:6],
            },
            "note": "这是 planner 基于 repo-analyzer/reviewer 结构化结果生成的执行计划草案，不代表已经开始实现。",
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def compose_research_option_validation_plan(
        comparison_json: str,
        gate_review_json: str = "",
        title: str = "",
        scope: str = "",
    ) -> str:
        """把 research 方案对比和 reviewer 门禁转换成最小验证计划。"""

        if not comparison_json.strip():
            return "Error: comparison_json is required"
        comparison = json.loads(comparison_json)
        if not isinstance(comparison, dict):
            return "Error: comparison_json must be a JSON object"
        if comparison.get("type") != "research_option_comparison":
            return "Error: comparison_json type must be research_option_comparison"

        gate_review: dict[str, Any] = {}
        if gate_review_json.strip():
            parsed_gate = json.loads(gate_review_json)
            if not isinstance(parsed_gate, dict):
                return "Error: gate_review_json must be a JSON object"
            if parsed_gate.get("type") != "research_option_comparison_gate_review":
                return "Error: gate_review_json type must be research_option_comparison_gate_review"
            gate_review = parsed_gate

        decision_question = str(comparison.get("decision_question") or comparison.get("topic") or "").strip()
        recommended_option = str(comparison.get("recommended_option") or "").strip()
        criteria = _clean_strings(comparison.get("criteria") if isinstance(comparison.get("criteria"), list) else [])
        uncertainty = _clean_strings(
            comparison.get("uncertainty") if isinstance(comparison.get("uncertainty"), list) else []
        )
        next_actions = _clean_strings(
            comparison.get("next_actions") if isinstance(comparison.get("next_actions"), list) else []
        )
        gate_actions = _clean_strings(
            gate_review.get("next_actions") if isinstance(gate_review.get("next_actions"), list) else []
        )
        gate_decision = str(gate_review.get("decision") or "missing").strip()
        options = [item for item in comparison.get("options") or [] if isinstance(item, dict)]
        option_names = [str(item.get("name") or "").strip() for item in options if str(item.get("name") or "").strip()]
        recommended = recommended_option or (option_names[0] if option_names else "待确认方案")

        risks = []
        risks.extend(uncertainty[:6])
        risks.extend(gate_actions[:4])
        if gate_decision == "missing":
            risks.append("缺少 reviewer 方案对比门禁，进入实现前需先完成审查。")
        elif gate_decision == "no-go":
            risks.append("方案对比门禁未通过，不能直接进入实现或生产落地。")
        if not criteria:
            risks.append("缺少评价维度，验证计划可能无法判断优劣。")

        phases = [
            {
                "name": "证据与门禁复核",
                "task": "复核方案对比、评价维度、来源依据和 reviewer 门禁结论。",
                "output": "可追溯的选型输入、门禁状态和未决问题清单。",
                "done": "方案对比 JSON、门禁结论和阻塞项均已确认；no-go 项未关闭前不进入实现。",
            }
        ]
        if gate_decision == "no-go":
            phases.append(
                {
                    "name": "阻塞项补证",
                    "task": "按门禁 next_actions 补充候选方案、来源、一手资料、推荐理由或不确定点说明。",
                    "output": "补证后的 research_option_comparison 和重新审查结果。",
                    "done": "重新运行 review_research_option_comparison_gate 后至少达到 conditional-go。",
                }
            )
            recommended_action = "hold"
        else:
            verification_focus = "、".join(criteria[:3]) if criteria else "可靠性、成本和复杂度"
            phases.extend(
                [
                    {
                        "name": "最小验证设计",
                        "task": f"围绕推荐方案「{recommended}」设计最小可回滚实验，并覆盖 {verification_focus}。",
                        "output": "实验范围、对照方案、测试指标、回滚方式和预期结论。",
                        "done": "实验能在不影响生产数据的前提下独立验证推荐方案的关键假设。",
                    },
                    {
                        "name": "实验实现与记录",
                        "task": "实现最小实验，补齐聚焦测试、压测或人工验收记录。",
                        "output": "代码/配置改动、测试结果、性能或稳定性观察记录。",
                        "done": "实验结果可复现，失败时可回滚，相关变更已按阶段提交。",
                    },
                    {
                        "name": "采纳决策沉淀",
                        "task": "根据实验结果决定采纳、暂缓或换方案，并交给 doc-writer 成文。",
                        "output": "最终选型结论、残余风险、后续实施计划或暂缓原因。",
                        "done": "结论已落盘，后续动作明确到下一阶段任务。",
                    },
                ]
            )
            recommended_action = "pilot" if gate_decision in {"go", "conditional-go"} else "review-first"

        plan_next_steps = []
        if gate_decision == "missing":
            plan_next_steps.append("先让 reviewer 使用 review_research_option_comparison_gate 审查方案对比。")
        elif gate_decision == "no-go":
            plan_next_steps.append("先处理门禁未通过项，不进入实现。")
        elif gate_decision == "conditional-go":
            plan_next_steps.append("只进入最小验证，不直接做生产化改造。")
        elif gate_decision == "go":
            plan_next_steps.append("进入最小验证设计，保留对照和回滚路径。")
        plan_next_steps.extend(gate_actions[:3])
        plan_next_steps.extend(next_actions[:3])
        if not plan_next_steps:
            plan_next_steps.append("把计划落盘后执行第一阶段证据复核。")

        plan_title = title.strip() or f"{recommended} 方案验证计划"
        plan_scope = scope.strip() or "只把已调研和已审查的方案对比转成最小验证计划，不直接进行生产落地。"
        result = {
            "type": "task_plan_from_research_option_comparison",
            "title": plan_title,
            "goal": f"验证「{recommended}」是否能解决：{decision_question or '当前选型问题'}。",
            "scope": plan_scope,
            "decision": {
                "gate": gate_decision,
                "recommended_option": recommended,
                "recommended_action": recommended_action,
            },
            "criteria": criteria,
            "candidate_options": option_names,
            "phases": phases[:6],
            "risks": list(dict.fromkeys(risks))[:8],
            "next_steps": list(dict.fromkeys(plan_next_steps))[:6],
            "save_task_plan_args": {
                "title": plan_title,
                "goal": f"验证「{recommended}」是否能解决：{decision_question or '当前选型问题'}。",
                "scope": plan_scope,
                "phases": phases[:6],
                "risks": list(dict.fromkeys(risks))[:8],
                "next_steps": list(dict.fromkeys(plan_next_steps))[:6],
            },
            "note": "这是 planner 基于 research/reviewer 结构化结果生成的验证计划草案，不代表已经开始实施。",
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def adapt_collaboration_plan_to_task_plan(
        collaboration_json: str,
        title: str = "",
        scope: str = "",
    ) -> str:
        """把入口 Agent 的多 Agent 协作路线转换成 planner 阶段计划。"""

        if not collaboration_json.strip():
            return "Error: collaboration_json is required"
        data = json.loads(collaboration_json)
        if not isinstance(data, dict):
            return "Error: collaboration_json must be a JSON object"
        if data.get("type") != "agent_collaboration_plan":
            return "Error: collaboration_json type must be agent_collaboration_plan"

        task_type = str(data.get("task_type") or "agent-collaboration").strip()
        user_goal = str(data.get("user_goal") or "按协作路线完成任务。").strip()
        expected_output = str(data.get("expected_output") or "多 Agent 协作产物。").strip()
        constraints = _clean_strings(data.get("constraints") if isinstance(data.get("constraints"), list) else [])
        next_actions = _clean_strings(data.get("next_actions") if isinstance(data.get("next_actions"), list) else [])
        phases = []
        for index, stage in enumerate(data.get("handoff_sequence") or [], start=1):
            if not isinstance(stage, dict):
                continue
            agent_id = str(stage.get("agent_id") or "待指定 Agent").strip()
            purpose = str(stage.get("purpose") or "按协作路线执行本阶段。").strip()
            output = str(stage.get("expected_output") or "阶段输出待明确").strip()
            input_contract = stage.get("input_contract")
            if isinstance(input_contract, dict):
                upstream = str(input_contract.get("upstream_result") or input_contract.get("user_goal") or "").strip()
            else:
                upstream = str(input_contract or "").strip()
            task_parts = [purpose]
            if upstream:
                task_parts.append(f"输入依据：{upstream}")
            phases.append(
                {
                    "name": f"阶段 {stage.get('step') or index}：{agent_id}",
                    "task": "；".join(task_parts),
                    "output": output,
                    "done": f"产出 {output}，并作为后续阶段的结构化输入。",
                }
            )
        if not phases:
            phases.append(
                {
                    "name": "阶段一：补齐协作路线",
                    "task": "补充 handoff_sequence、目标 Agent、输入契约和阶段输出。",
                    "output": "可执行协作阶段计划",
                    "done": "协作路线、输入、输出和边界均已明确。",
                }
            )

        plan_title = title.strip() or f"{task_type} 协作执行计划"
        plan_scope = scope.strip() or "只把多 Agent 协作路线转成可执行阶段计划，不自动调用任何 Agent。"
        plan_next_steps = ["先执行第一阶段，并保留结构化输出作为下一阶段输入。"]
        plan_next_steps.extend(next_actions[:4])
        if data.get("should_persist"):
            plan_next_steps.append("按需使用 save_task_plan 将本计划落盘到 reports/plans/。")

        return json.dumps(
            {
                "type": "task_plan_from_collaboration",
                "title": plan_title,
                "goal": user_goal,
                "scope": plan_scope,
                "task_type": task_type,
                "expected_output": expected_output,
                "phases": phases[:8],
                "risks": constraints,
                "next_steps": plan_next_steps[:6],
                "save_task_plan_args": {
                    "title": plan_title,
                    "goal": user_goal,
                    "scope": plan_scope,
                    "phases": phases[:8],
                    "risks": constraints,
                    "next_steps": plan_next_steps[:6],
                },
                "note": "这是从多 Agent 协作路线转换出的 planner 阶段计划草案，不会自动调用任何 Agent。",
            },
            ensure_ascii=False,
            indent=2,
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

    def review_release_gate(
        change_summary: str,
        risk_items: list[dict[str, str]] | None = None,
        test_evidence: list[str] | None = None,
        unresolved_items: list[str] | None = None,
        rollback_plan: str = "",
    ) -> str:
        """生成发布前风险门禁判定和检查清单。"""

        risks = risk_items or []
        evidence = _clean_strings(test_evidence)
        unresolved = _clean_strings(unresolved_items)
        normalized_risks = []
        critical_or_high = 0
        blocker_count = 0
        for item in risks:
            severity = _normalize_severity(str(item.get("severity", "")))
            status = str(item.get("status", "") or item.get("state", "")).strip().lower()
            issue = str(item.get("issue", "")).strip()
            mitigation = str(item.get("mitigation", "") or item.get("suggestion", "")).strip()
            if severity in {"critical", "high"}:
                critical_or_high += 1
            if severity == "critical" or status in {"open", "unresolved", "blocked", "未解决"}:
                blocker_count += 1
            normalized_risks.append(
                {
                    "severity": severity,
                    "issue": issue or "未说明风险项",
                    "status": status or "unknown",
                    "mitigation": mitigation or "补充缓解措施。",
                }
            )

        checklist = [
            {
                "item": "变更范围已说明",
                "passed": bool(change_summary.strip()),
                "evidence": change_summary.strip() or "缺少变更摘要。",
            },
            {
                "item": "测试证据已提供",
                "passed": bool(evidence),
                "evidence": "; ".join(evidence) if evidence else "缺少测试证据。",
            },
            {
                "item": "无未解决阻塞项",
                "passed": not unresolved and blocker_count == 0,
                "evidence": "; ".join(unresolved) if unresolved else "未发现显式未决项。",
            },
            {
                "item": "回滚或恢复方案已说明",
                "passed": bool(rollback_plan.strip()),
                "evidence": rollback_plan.strip() or "缺少回滚/恢复方案。",
            },
        ]

        if blocker_count > 0 or not change_summary.strip():
            decision = "no-go"
        elif not evidence or unresolved or critical_or_high > 0 or not rollback_plan.strip():
            decision = "conditional-go"
        else:
            decision = "go"

        next_actions = []
        if not evidence:
            next_actions.append("补充可复现测试证据。")
        if unresolved:
            next_actions.extend(unresolved[:3])
        if not rollback_plan.strip():
            next_actions.append("补充回滚或恢复方案。")
        for risk in normalized_risks:
            if risk["severity"] in {"critical", "high"} and risk["mitigation"]:
                next_actions.append(risk["mitigation"])
        if not next_actions:
            next_actions.append("保留本次门禁记录，按计划推进。")

        return json.dumps(
            {
                "type": "release_gate_review",
                "change_summary": change_summary.strip(),
                "decision": decision,
                "checklist": checklist,
                "risks": normalized_risks,
                "test_evidence": evidence,
                "unresolved_items": unresolved,
                "rollback_plan": rollback_plan.strip(),
                "next_actions": next_actions[:6],
            },
            ensure_ascii=False,
            indent=2,
        )

    def review_task_plan_gate(
        plan_json: str = "",
        review_target: str = "",
        required_evidence: list[str] | None = None,
        known_risks: list[str] | None = None,
    ) -> str:
        """审查阶段计划是否具备进入执行的基本条件。"""

        plan: dict[str, Any] = {}
        if plan_json.strip():
            parsed = json.loads(plan_json)
            if not isinstance(parsed, dict):
                return "Error: plan_json must be a JSON object"
            plan = parsed
        target = review_target.strip() or str(plan.get("title") or plan.get("goal") or "").strip()
        phases = plan.get("phases") if isinstance(plan.get("phases"), list) else []
        risks = _clean_strings(known_risks)
        risks.extend(_clean_strings(plan.get("risks") if isinstance(plan.get("risks"), list) else []))
        evidence = _clean_strings(required_evidence)
        evidence.extend(_clean_strings(plan.get("acceptance_checks") if isinstance(plan.get("acceptance_checks"), list) else []))
        evidence.extend(_clean_strings(plan.get("next_steps") if isinstance(plan.get("next_steps"), list) else []))
        plan_type = str(plan.get("type") or "").strip()

        checklist = [
            {
                "item": "目标已明确",
                "passed": bool(str(plan.get("goal") or target).strip()),
                "evidence": str(plan.get("goal") or target or "缺少目标。").strip(),
            },
            {
                "item": "边界已说明",
                "passed": bool(str(plan.get("scope", "")).strip()),
                "evidence": str(plan.get("scope") or "缺少 scope / 不做事项。").strip(),
            },
            {
                "item": "阶段计划可执行",
                "passed": bool(phases) and all(isinstance(phase, dict) and phase.get("task") for phase in phases),
                "evidence": f"阶段数量：{len(phases)}" if phases else "缺少 phases。",
            },
            {
                "item": "完成标准已说明",
                "passed": bool(phases)
                and all(
                    isinstance(phase, dict)
                    and str(phase.get("done") or phase.get("acceptance") or "").strip()
                    for phase in phases
                ),
                "evidence": "每个阶段都有完成标准。" if phases else "缺少阶段完成标准。",
            },
            {
                "item": "风险和门槛已列出",
                "passed": bool(risks),
                "evidence": "；".join(risks[:4]) if risks else "缺少风险项。",
            },
            {
                "item": "验收或测试依据已列出",
                "passed": bool(evidence),
                "evidence": "；".join(evidence[:4]) if evidence else "缺少验收或测试依据。",
            },
        ]
        if plan_type == "task_plan_from_research_option_comparison":
            decision_data = plan.get("decision") if isinstance(plan.get("decision"), dict) else {}
            gate_decision = str(decision_data.get("gate") or "").strip()
            recommended_action = str(decision_data.get("recommended_action") or "").strip()
            recommended_option = str(decision_data.get("recommended_option") or "").strip()
            criteria = _clean_strings(plan.get("criteria") if isinstance(plan.get("criteria"), list) else [])
            candidate_options = _clean_strings(
                plan.get("candidate_options") if isinstance(plan.get("candidate_options"), list) else []
            )
            checklist.extend(
                [
                    {
                        "item": "方案验证门禁已通过或有条件通过",
                        "passed": gate_decision in {"go", "conditional-go"},
                        "evidence": gate_decision or "缺少 research_option_comparison_gate_review 结论。",
                    },
                    {
                        "item": "推荐方案已明确",
                        "passed": bool(recommended_option),
                        "evidence": recommended_option or "缺少 recommended_option。",
                    },
                    {
                        "item": "候选方案和评价维度已保留",
                        "passed": bool(candidate_options) and bool(criteria),
                        "evidence": f"候选方案：{len(candidate_options)}；评价维度：{len(criteria)}。",
                    },
                    {
                        "item": "执行动作限制合理",
                        "passed": recommended_action in {"pilot", "review-first"} and gate_decision != "no-go",
                        "evidence": recommended_action or "缺少 recommended_action。",
                    },
                ]
            )

        failed = [item for item in checklist if not item["passed"]]
        if len(failed) >= 3 or not target:
            decision = "no-go"
        elif failed:
            decision = "conditional-go"
        else:
            decision = "go"

        next_actions = []
        for item in failed:
            if item["item"] == "目标已明确":
                next_actions.append("补充明确目标和预期交付物。")
            elif item["item"] == "边界已说明":
                next_actions.append("补充 scope，明确做什么和不做什么。")
            elif item["item"] == "阶段计划可执行":
                next_actions.append("把计划拆成至少一个可执行阶段。")
            elif item["item"] == "完成标准已说明":
                next_actions.append("为每个阶段补齐完成标准。")
            elif item["item"] == "风险和门槛已列出":
                next_actions.append("补充主要风险、风险门槛和规避动作。")
            elif item["item"] == "验收或测试依据已列出":
                next_actions.append("补充测试命令、人工验收或回滚验证依据。")
            elif item["item"] == "方案验证门禁已通过或有条件通过":
                next_actions.append("先让 reviewer 审查 research_option_comparison，并处理 no-go 阻塞项。")
            elif item["item"] == "推荐方案已明确":
                next_actions.append("补充推荐方案，或明确当前只做候选方案对比不进入验证。")
            elif item["item"] == "候选方案和评价维度已保留":
                next_actions.append("补充候选方案和评价维度，确保验证计划能追溯选型依据。")
            elif item["item"] == "执行动作限制合理":
                next_actions.append("将 no-go 计划限制为补证，或把可执行计划降级为 review-first / pilot。")
        if not next_actions:
            next_actions.append("计划具备进入执行的基本条件，执行前保留审查记录。")

        return json.dumps(
            {
                "type": "task_plan_gate_review",
                "review_target": target,
                "decision": decision,
                "checklist": checklist,
                "risks": risks[:8],
                "evidence": evidence[:8],
                "next_actions": next_actions[:6],
                "note": "这是计划进入执行前的门禁审查，不代表代码已经通过发布门禁。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def review_agent_collaboration_gate(
        collaboration_json: str = "",
        review_target: str = "",
        known_risks: list[str] | None = None,
    ) -> str:
        """审查多 Agent 协作路线是否具备安全交接条件。"""

        plan: dict[str, Any] = {}
        if collaboration_json.strip():
            parsed = json.loads(collaboration_json)
            if not isinstance(parsed, dict):
                return "Error: collaboration_json must be a JSON object"
            plan = parsed
        target = review_target.strip() or str(plan.get("task_type") or plan.get("user_goal") or "").strip()
        sequence = plan.get("handoff_sequence") if isinstance(plan.get("handoff_sequence"), list) else []
        risks = _clean_strings(known_risks)
        risks.extend(_clean_strings(plan.get("constraints") if isinstance(plan.get("constraints"), list) else []))
        agent_ids = []
        malformed_steps = 0
        missing_contracts = 0
        missing_outputs = 0
        for index, stage in enumerate(sequence, start=1):
            if not isinstance(stage, dict):
                malformed_steps += 1
                continue
            agent_id = str(stage.get("agent_id") or "").strip()
            if agent_id:
                agent_ids.append(agent_id)
            else:
                malformed_steps += 1
            input_contract = stage.get("input_contract")
            if not isinstance(input_contract, dict) or not input_contract:
                missing_contracts += 1
            if not str(stage.get("expected_output") or "").strip():
                missing_outputs += 1
            try:
                stage_step = int(stage.get("step") or index)
            except (TypeError, ValueError):
                stage_step = -1
            if stage_step != index:
                risks.append(f"步骤序号不连续或不匹配：期望 {index}。")

        note = str(plan.get("note") or "").strip()
        next_actions = _clean_strings(plan.get("next_actions") if isinstance(plan.get("next_actions"), list) else [])
        no_auto_execution_declared = "不代表任何 Agent 已经执行" in note or "不会自动调用任何 Agent" in " ".join(next_actions)
        checklist = [
            {
                "item": "协作目标已明确",
                "passed": bool(str(plan.get("user_goal") or target).strip()) and bool(str(plan.get("expected_output") or "").strip()),
                "evidence": str(plan.get("user_goal") or target or "缺少 user_goal。").strip(),
            },
            {
                "item": "协作路线存在",
                "passed": bool(sequence) and malformed_steps == 0,
                "evidence": f"阶段数量：{len(sequence)}；Agent：{', '.join(agent_ids) or '未指定'}",
            },
            {
                "item": "交接输入契约完整",
                "passed": bool(sequence) and missing_contracts == 0,
                "evidence": "每个阶段都有 input_contract。" if sequence and missing_contracts == 0 else f"缺少输入契约阶段数：{missing_contracts}",
            },
            {
                "item": "阶段输出明确",
                "passed": bool(sequence) and missing_outputs == 0,
                "evidence": "每个阶段都有 expected_output。" if sequence and missing_outputs == 0 else f"缺少输出定义阶段数：{missing_outputs}",
            },
            {
                "item": "边界和约束已说明",
                "passed": bool(risks),
                "evidence": "；".join(risks[:4]) if risks else "缺少 constraints / known_risks。",
            },
            {
                "item": "未自动执行声明明确",
                "passed": no_auto_execution_declared,
                "evidence": note or "缺少协作路线未自动执行声明。",
            },
        ]

        failed = [item for item in checklist if not item["passed"]]
        if len(failed) >= 3 or not target:
            decision = "no-go"
        elif failed:
            decision = "conditional-go"
        else:
            decision = "go"

        remediation = []
        for item in failed:
            if item["item"] == "协作目标已明确":
                remediation.append("补充 user_goal 和 expected_output。")
            elif item["item"] == "协作路线存在":
                remediation.append("补齐 handoff_sequence，并为每个阶段指定 agent_id。")
            elif item["item"] == "交接输入契约完整":
                remediation.append("为每个阶段补充 input_contract，明确上游结果和必要输入。")
            elif item["item"] == "阶段输出明确":
                remediation.append("为每个阶段补充 expected_output，避免交接结果不可用。")
            elif item["item"] == "边界和约束已说明":
                remediation.append("补充协作约束、不可做事项和风险边界。")
            elif item["item"] == "未自动执行声明明确":
                remediation.append("明确说明该结果只生成协作路线，不代表任何 Agent 已经执行。")
        if not remediation:
            remediation.append("协作路线具备交接条件，执行前仍需逐阶段保留产物。")

        return json.dumps(
            {
                "type": "collaboration_gate_review",
                "review_target": target,
                "decision": decision,
                "checklist": checklist,
                "agents": agent_ids,
                "risks": risks[:8],
                "next_actions": remediation[:6],
                "note": "这是多 Agent 协作路线门禁审查，不会自动执行任何 Agent。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def review_research_evidence_gate(
        evidence_json: str = "",
        review_target: str = "",
        min_sources: int = 2,
        require_primary_source: bool = True,
        time_sensitive: bool = False,
    ) -> str:
        """审查 research 证据包是否足够交给下游 Agent 复用。"""

        evidence: dict[str, Any] = {}
        if evidence_json.strip():
            parsed = json.loads(evidence_json)
            if not isinstance(parsed, dict):
                return "Error: evidence_json must be a JSON object"
            evidence = parsed
        target = review_target.strip() or str(evidence.get("topic") or evidence.get("research_question") or "").strip()
        sources = evidence.get("sources") if isinstance(evidence.get("sources"), list) else []
        source_count = len([source for source in sources if isinstance(source, dict)])
        primary_count = int(evidence.get("primary_source_count") or 0)
        if primary_count <= 0:
            primary_count = sum(
                1
                for source in sources
                if isinstance(source, dict)
                and str(source.get("source_type") or "").lower() in {"official", "docs", "paper", "primary", "官方", "论文"}
            )
        urls = [
            str(source.get("url") or "").strip()
            for source in sources
            if isinstance(source, dict) and str(source.get("url") or "").strip()
        ]
        key_facts = _clean_strings(evidence.get("key_facts") if isinstance(evidence.get("key_facts"), list) else [])
        conflicts = _clean_strings(evidence.get("source_conflicts") if isinstance(evidence.get("source_conflicts"), list) else [])
        uncertainty = _clean_strings(evidence.get("uncertainty") if isinstance(evidence.get("uncertainty"), list) else [])
        quality = str(evidence.get("evidence_quality") or "").strip().lower()
        freshness = str(evidence.get("freshness") or "").strip()

        checklist = [
            {
                "item": "调研问题和结论已明确",
                "passed": bool(target) and bool(str(evidence.get("conclusion") or "").strip()),
                "evidence": str(evidence.get("conclusion") or "缺少 conclusion。").strip(),
            },
            {
                "item": "来源数量达到门槛",
                "passed": source_count >= max(1, min_sources),
                "evidence": f"来源数量：{source_count}，要求至少：{max(1, min_sources)}。",
            },
            {
                "item": "来源 URL 可核验",
                "passed": len(urls) == source_count and source_count > 0,
                "evidence": f"可核验 URL 数量：{len(urls)}。",
            },
            {
                "item": "一手来源满足要求",
                "passed": (not require_primary_source) or primary_count > 0,
                "evidence": f"一手来源数量：{primary_count}。",
            },
            {
                "item": "关键事实已摘录",
                "passed": bool(key_facts),
                "evidence": "；".join(key_facts[:3]) if key_facts else "缺少 key_facts。",
            },
            {
                "item": "冲突和不确定点已标注",
                "passed": quality not in {"missing", ""} and (not conflicts or bool(uncertainty) or quality == "limited"),
                "evidence": "；".join([*conflicts[:2], *uncertainty[:2]]) or f"evidence_quality={quality or 'missing'}。",
            },
            {
                "item": "时效信息已说明",
                "passed": (not time_sensitive) or bool(freshness),
                "evidence": freshness or "缺少 freshness。",
            },
        ]

        failed = [item for item in checklist if not item["passed"]]
        if len(failed) >= 3 or not target:
            decision = "no-go"
        elif failed or quality in {"limited", "medium"}:
            decision = "conditional-go"
        else:
            decision = "go"

        next_actions = []
        for item in failed:
            if item["item"] == "时效信息已说明":
                next_actions.append("补充检索日期、发布时间或最后更新时间。")
        for item in failed:
            if item["item"] == "调研问题和结论已明确":
                next_actions.append("补充 research_question/topic 和 conclusion。")
            elif item["item"] == "来源数量达到门槛":
                next_actions.append(f"补充至少 {max(1, min_sources)} 个可交叉验证来源。")
            elif item["item"] == "来源 URL 可核验":
                next_actions.append("为每个来源补充可访问 URL。")
            elif item["item"] == "一手来源满足要求":
                next_actions.append("补充官方文档、论文或一手资料来源。")
            elif item["item"] == "关键事实已摘录":
                next_actions.append("从来源中摘录可复用 key_facts。")
            elif item["item"] == "冲突和不确定点已标注":
                next_actions.append("标注来源冲突、不确定点或证据质量限制。")
        if not next_actions:
            next_actions.append("证据包可交给 doc-writer、planner 或 reviewer 继续复用。")

        return json.dumps(
            {
                "type": "research_evidence_gate_review",
                "review_target": target,
                "decision": decision,
                "checklist": checklist,
                "evidence_quality": quality or "missing",
                "source_count": source_count,
                "primary_source_count": primary_count,
                "risks": [*conflicts[:4], *uncertainty[:4]],
                "next_actions": next_actions[:6],
                "note": "这是 research 证据包复用前门禁审查，不代表事实已经永久有效。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def review_research_option_comparison_gate(
        comparison_json: str = "",
        review_target: str = "",
        min_options: int = 2,
        min_sources: int = 2,
        require_primary_source: bool = True,
        require_recommendation: bool = True,
    ) -> str:
        """审查 research 方案对比是否足够支撑技术选型或实施计划。"""

        comparison: dict[str, Any] = {}
        if comparison_json.strip():
            parsed = json.loads(comparison_json)
            if not isinstance(parsed, dict):
                return "Error: comparison_json must be a JSON object"
            if parsed.get("type") != "research_option_comparison":
                return "Error: comparison_json type must be research_option_comparison"
            comparison = parsed
        target = review_target.strip() or str(
            comparison.get("topic") or comparison.get("decision_question") or ""
        ).strip()
        options = [item for item in comparison.get("options") or [] if isinstance(item, dict)]
        criteria = _clean_strings(comparison.get("criteria") if isinstance(comparison.get("criteria"), list) else [])
        sources = [item for item in comparison.get("sources") or [] if isinstance(item, dict)]
        urls = [str(source.get("url") or "").strip() for source in sources if str(source.get("url") or "").strip()]
        primary_count = int(comparison.get("primary_source_count") or 0)
        if primary_count <= 0:
            primary_count = sum(
                1
                for source in sources
                if str(source.get("source_type") or "").lower()
                in {"official", "docs", "paper", "primary", "官方", "论文"}
            )
        recommendation = str(comparison.get("recommended_option") or "").strip()
        uncertainty = _clean_strings(
            comparison.get("uncertainty") if isinstance(comparison.get("uncertainty"), list) else []
        )
        quality = str(comparison.get("evidence_quality") or "").strip().lower()
        option_evidence_gaps = []
        for option in options:
            evidence = _clean_strings(option.get("evidence") if isinstance(option.get("evidence"), list) else [])
            strengths = _clean_strings(option.get("strengths") if isinstance(option.get("strengths"), list) else [])
            weaknesses = _clean_strings(option.get("weaknesses") if isinstance(option.get("weaknesses"), list) else [])
            if not evidence and not (strengths and weaknesses):
                option_evidence_gaps.append(str(option.get("name") or "未命名方案"))

        checklist = [
            {
                "item": "决策问题已明确",
                "passed": bool(target) and bool(str(comparison.get("decision_question") or "").strip()),
                "evidence": str(comparison.get("decision_question") or "缺少 decision_question。").strip(),
            },
            {
                "item": "候选方案数量达到门槛",
                "passed": len(options) >= max(1, min_options),
                "evidence": f"候选方案数量：{len(options)}，要求至少：{max(1, min_options)}。",
            },
            {
                "item": "评价维度已列出",
                "passed": bool(criteria),
                "evidence": "；".join(criteria[:5]) if criteria else "缺少 criteria。",
            },
            {
                "item": "来源数量达到门槛",
                "passed": len(sources) >= max(1, min_sources),
                "evidence": f"来源数量：{len(sources)}，要求至少：{max(1, min_sources)}。",
            },
            {
                "item": "来源 URL 可核验",
                "passed": len(urls) == len(sources) and len(sources) > 0,
                "evidence": f"可核验 URL 数量：{len(urls)}。",
            },
            {
                "item": "一手来源满足要求",
                "passed": (not require_primary_source) or primary_count > 0,
                "evidence": f"一手来源数量：{primary_count}。",
            },
            {
                "item": "推荐方案已说明",
                "passed": (not require_recommendation) or bool(recommendation),
                "evidence": recommendation or "缺少 recommended_option。",
            },
            {
                "item": "候选方案证据足够",
                "passed": not option_evidence_gaps,
                "evidence": "证据不足方案：" + "、".join(option_evidence_gaps)
                if option_evidence_gaps
                else "每个候选方案都有证据或优劣势说明。",
            },
            {
                "item": "不确定点已标注",
                "passed": quality not in {"missing", ""} and (quality != "limited" or bool(uncertainty)),
                "evidence": "；".join(uncertainty[:4]) or f"evidence_quality={quality or 'missing'}。",
            },
        ]

        failed = [item for item in checklist if not item["passed"]]
        if len(failed) >= 3 or not target:
            decision = "no-go"
        elif failed or quality in {"limited", "medium"}:
            decision = "conditional-go"
        else:
            decision = "go"

        next_actions = []
        for item in failed:
            if item["item"] == "决策问题已明确":
                next_actions.append("补充明确的 decision_question。")
            elif item["item"] == "候选方案数量达到门槛":
                next_actions.append(f"补充至少 {max(1, min_options)} 个候选方案。")
            elif item["item"] == "评价维度已列出":
                next_actions.append("补充评价维度，例如可靠性、成本、复杂度、性能和可运维性。")
            elif item["item"] == "来源数量达到门槛":
                next_actions.append(f"补充至少 {max(1, min_sources)} 个可交叉验证来源。")
            elif item["item"] == "来源 URL 可核验":
                next_actions.append("为每个来源补充可访问 URL。")
            elif item["item"] == "一手来源满足要求":
                next_actions.append("补充官方文档、论文或一手资料来源。")
            elif item["item"] == "推荐方案已说明":
                next_actions.append("补充推荐方案和推荐理由。")
            elif item["item"] == "候选方案证据足够":
                next_actions.append("为每个候选方案补充证据、优势和短板。")
            elif item["item"] == "不确定点已标注":
                next_actions.append("标注证据限制、不确定点或需要压测验证的部分。")
        if not next_actions:
            next_actions.append("方案对比可交给 planner 拆验证计划，或交给 doc-writer 成文。")

        return json.dumps(
            {
                "type": "research_option_comparison_gate_review",
                "review_target": target,
                "decision": decision,
                "checklist": checklist,
                "recommended_option": recommendation,
                "evidence_quality": quality or "missing",
                "option_count": len(options),
                "source_count": len(sources),
                "primary_source_count": primary_count,
                "risks": [*uncertainty[:6], *option_evidence_gaps[:4]],
                "next_actions": list(dict.fromkeys(next_actions))[:8],
                "note": "这是 research 方案对比进入计划或成文前的门禁审查，不代表已经完成实施验证。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def review_github_repo_risk_gate(
        risk_scan_json: str = "",
        review_target: str = "",
        intended_action: str = "",
        require_license_clear: bool = True,
    ) -> str:
        """审查 GitHub 仓库风险扫描是否足够支撑采纳或复用决策。"""

        scan: dict[str, Any] = {}
        if risk_scan_json.strip():
            parsed = json.loads(risk_scan_json)
            if not isinstance(parsed, dict):
                return "Error: risk_scan_json must be a JSON object"
            scan = parsed
        if scan and scan.get("type") != "github_repo_risk_scan":
            return "Error: risk_scan_json type must be github_repo_risk_scan"

        target = review_target.strip() or str(scan.get("repository") or "").strip()
        risk_items = [item for item in scan.get("risk_items") or [] if isinstance(item, dict)]
        summary = scan.get("summary") if isinstance(scan.get("summary"), dict) else {}
        license_id = str(summary.get("license") or "").strip().lower()
        risk_level = str(scan.get("risk_level") or "").strip().lower()
        source_decision = str(scan.get("decision") or "").strip().lower()
        next_actions = _clean_strings(
            scan.get("next_actions") if isinstance(scan.get("next_actions"), list) else []
        )
        high_or_critical = 0
        medium_count = 0
        license_blocked = False
        archived = bool(summary.get("archived"))
        normalized_risks = []

        for item in risk_items:
            severity = _normalize_severity(str(item.get("severity") or ""))
            area = str(item.get("area") or "unknown").strip()
            issue = str(item.get("issue") or "未说明风险项").strip()
            impact = str(item.get("impact") or "待评估").strip()
            mitigation = str(item.get("mitigation") or "补充缓解措施。").strip()
            if severity in {"critical", "high"}:
                high_or_critical += 1
            if severity == "medium":
                medium_count += 1
            if area == "license" and require_license_clear:
                license_blocked = True
            normalized_risks.append(
                {
                    "severity": severity,
                    "area": area,
                    "issue": issue,
                    "impact": impact,
                    "mitigation": mitigation,
                }
            )

        checklist = [
            {
                "item": "风险扫描类型正确",
                "passed": bool(scan) and scan.get("type") == "github_repo_risk_scan",
                "evidence": str(scan.get("type") or "缺少 github_repo_risk_scan。"),
            },
            {
                "item": "仓库和用途已明确",
                "passed": bool(target)
                and bool(str(scan.get("intended_use") or intended_action).strip()),
                "evidence": (
                    f"{target or '缺少仓库'}；用途："
                    f"{scan.get('intended_use') or intended_action or '未说明'}"
                ),
            },
            {
                "item": "许可证风险可接受",
                "passed": (not require_license_clear)
                or (bool(license_id) and license_id != "unknown" and not license_blocked),
                "evidence": license_id or "unknown",
            },
            {
                "item": "维护状态可接受",
                "passed": not archived,
                "evidence": "仓库已归档。" if archived else "仓库未标记为归档。",
            },
            {
                "item": "无高危阻塞风险",
                "passed": high_or_critical == 0 and risk_level not in {"critical", "high"},
                "evidence": f"高危风险数：{high_or_critical}；risk_level={risk_level or 'unknown'}。",
            },
            {
                "item": "缓解动作已给出",
                "passed": bool(next_actions) and all(risk.get("mitigation") for risk in normalized_risks),
                "evidence": "；".join(next_actions[:3]) if next_actions else "缺少 next_actions。",
            },
        ]

        failed = [item for item in checklist if not item["passed"]]
        if license_blocked or archived or high_or_critical > 0 or source_decision in {"hold", "block"}:
            decision = "no-go"
        elif failed or medium_count > 0 or source_decision in {"conditional", "watch"}:
            decision = "conditional-go"
        else:
            decision = "go"

        remediation = []
        for item in failed:
            if item["item"] == "仓库和用途已明确":
                remediation.append(
                    "补充 intended_use，明确是学习、引用文档、复用代码还是作为运行依赖。"
                )
            elif item["item"] == "许可证风险可接受":
                remediation.append("人工复核 LICENSE、README 授权说明或联系作者后再复用。")
            elif item["item"] == "维护状态可接受":
                remediation.append("归档仓库只作为设计参考；如需采用，先寻找活跃 fork 或替代实现。")
            elif item["item"] == "无高危阻塞风险":
                remediation.append("先处理高危风险，未缓解前不要进入采纳或复用。")
            elif item["item"] == "缓解动作已给出":
                remediation.append("为每个风险项补充可执行缓解动作和人工确认点。")
        remediation.extend(next_actions[:3])
        if not remediation:
            remediation.append("风险扫描可进入下一阶段，但仍需保留来源和人工复核记录。")

        return json.dumps(
            {
                "type": "github_repo_risk_gate_review",
                "review_target": target,
                "intended_action": str(scan.get("intended_use") or intended_action).strip(),
                "source_decision": source_decision or "unknown",
                "decision": decision,
                "checklist": checklist,
                "risks": normalized_risks[:12],
                "next_actions": list(dict.fromkeys(remediation))[:8],
                "note": "这是仓库风险扫描门禁审查，不代表已经完成法律、安全或运行验证。",
            },
            ensure_ascii=False,
            indent=2,
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

    def render_repo_analysis_markdown(
        analysis_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 repo-analyzer 的结构化 JSON 渲染成正式 Markdown 文档。"""

        if not analysis_json.strip():
            return "Error: analysis_json is required"
        analysis = json.loads(analysis_json)
        if not isinstance(analysis, dict):
            return "Error: analysis_json must be a JSON object"
        if analysis.get("type") != "github_repo_analysis":
            return "Error: analysis_json type must be github_repo_analysis"

        repository = str(analysis.get("repository") or "unknown/repository")
        document_title = title.strip() or f"仓库分析：{repository}"
        positioning = analysis.get("project_positioning") or {}
        if not isinstance(positioning, dict):
            positioning = {}
        gateway_fit = analysis.get("gateway_fit") or {}
        if not isinstance(gateway_fit, dict):
            gateway_fit = {}

        project_rows = [
            f"- 仓库：{repository}",
            f"- 地址：{analysis.get('url') or '未提供'}",
            f"- 主要语言：{positioning.get('language') or 'unknown'}",
            f"- 许可证：{positioning.get('license') or 'unknown'}",
            f"- 生命周期：{positioning.get('lifecycle') or 'unknown'}",
        ]
        topics = positioning.get("topics") or []
        if topics:
            project_rows.append(f"- Topics：{', '.join(str(item) for item in topics)}")

        fit_lines = [
            f"- 评分：{gateway_fit.get('score', 0)}",
            f"- 优先级：{gateway_fit.get('priority', 'unknown')}",
            "- 信号：",
            *[f"  - {item}" for item in gateway_fit.get("signals") or ["暂无"]],
        ]
        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(analysis, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("仓库结论", str(analysis.get("analysis_goal") or "")),
                _markdown_section(
                    "项目定位",
                    "\n".join(
                        [
                            str(positioning.get("description") or "待补充"),
                            "",
                            *project_rows,
                        ]
                    ),
                ),
                _markdown_section("Gateway 适配评估", "\n".join(fit_lines)),
                _markdown_section("关键发现", _markdown_bullets(analysis.get("key_findings"))),
                _markdown_section(
                    "对 Gateway 的借鉴点",
                    _markdown_bullets(analysis.get("gateway_reuse_ideas")),
                ),
                _markdown_section("风险与不确定点", _markdown_bullets(analysis.get("risks"))),
                _markdown_section("建议下一步", _markdown_bullets(analysis.get("recommendations"))),
            ]
        ) + metadata

    def render_github_repo_risk_markdown(
        risk_scan_json: str,
        gate_review_json: str = "",
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 repo-analyzer 的仓库风险扫描 JSON 渲染成正式 Markdown。"""

        if not risk_scan_json.strip():
            return "Error: risk_scan_json is required"
        scan = json.loads(risk_scan_json)
        if not isinstance(scan, dict):
            return "Error: risk_scan_json must be a JSON object"
        if scan.get("type") != "github_repo_risk_scan":
            return "Error: risk_scan_json type must be github_repo_risk_scan"
        gate_review: dict[str, Any] = {}
        if gate_review_json.strip():
            parsed_review = json.loads(gate_review_json)
            if not isinstance(parsed_review, dict):
                return "Error: gate_review_json must be a JSON object"
            if parsed_review.get("type") != "github_repo_risk_gate_review":
                return "Error: gate_review_json type must be github_repo_risk_gate_review"
            gate_review = parsed_review

        repository = str(scan.get("repository") or "unknown/repository")
        document_title = title.strip() or f"仓库风险扫描：{repository}"
        summary_data = scan.get("summary") if isinstance(scan.get("summary"), dict) else {}
        summary = "\n".join(
            [
                f"- 仓库：{repository}",
                f"- 地址：{scan.get('url') or '未提供'}",
                f"- 预期用途：{scan.get('intended_use') or '未说明'}",
                f"- 风险等级：{scan.get('risk_level') or 'unknown'}",
                f"- 建议决策：{scan.get('decision') or 'unknown'}",
                f"- 许可证：{summary_data.get('license') or 'unknown'}",
                f"- 是否归档：{'是' if summary_data.get('archived') else '否'}",
                f"- Open Issues：{summary_data.get('open_issues', 0)}",
                f"- Stars：{summary_data.get('stars', 0)}",
            ]
        )

        risk_rows = []
        for item in scan.get("risk_items") or []:
            if not isinstance(item, dict):
                continue
            risk_rows.append(
                [
                    item.get("severity") or "unknown",
                    item.get("area") or "unknown",
                    item.get("issue") or "未说明",
                    item.get("impact") or "待评估",
                    item.get("mitigation") or "待补充",
                ]
            )
        dependency_files = _clean_strings(
            scan.get("dependency_files") if isinstance(scan.get("dependency_files"), list) else []
        )
        review_section = ""
        if gate_review:
            checklist_rows = []
            for item in gate_review.get("checklist") or []:
                if not isinstance(item, dict):
                    continue
                checklist_rows.append(
                    [
                        item.get("item") or "未命名检查项",
                        "通过" if item.get("passed") else "未通过",
                        item.get("evidence") or "未提供",
                    ]
                )
            review_section = _markdown_section(
                "门禁审查结论",
                "\n\n".join(
                    [
                        "\n".join(
                            [
                                f"- reviewer 结论：{gate_review.get('decision') or 'unknown'}",
                                f"- 上游建议：{gate_review.get('source_decision') or 'unknown'}",
                                f"- 审查对象：{gate_review.get('review_target') or repository}",
                            ]
                        ),
                        _markdown_table(["检查项", "结果", "依据"], checklist_rows),
                    ]
                ),
            )

        metadata = ""
        if include_raw_metadata:
            raw_payload: dict[str, Any] = {"risk_scan": scan}
            if gate_review:
                raw_payload["gate_review"] = gate_review
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n```",
            )

        sections = [
            f"# {document_title}",
            _markdown_section("摘要", summary),
            _markdown_section(
                "风险项",
                _markdown_table(["级别", "领域", "问题", "影响", "建议"], risk_rows),
            ),
        ]
        if review_section:
            sections.append(review_section)
        sections.extend(
            [
                _markdown_section("依赖文件信号", _markdown_bullets(dependency_files)),
                _markdown_section("建议下一步", _markdown_bullets(scan.get("next_actions"))),
                _markdown_section(
                    "说明",
                    str(scan.get("note") or "这是轻量风险扫描，不代表已经完成法律、安全或运行验证。"),
                ),
            ]
        )

        return "\n\n".join(sections) + metadata

    def render_research_evidence_markdown(
        evidence_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 research 证据包渲染成正式 Markdown 调研记录。"""

        if not evidence_json.strip():
            return "Error: evidence_json is required"
        evidence = json.loads(evidence_json)
        if not isinstance(evidence, dict):
            return "Error: evidence_json must be a JSON object"
        if evidence.get("type") != "research_evidence_pack":
            return "Error: evidence_json type must be research_evidence_pack"

        topic = str(evidence.get("topic") or "调研记录").strip()
        document_title = title.strip() or f"调研证据包：{topic}"
        quality = str(evidence.get("evidence_quality") or "unknown").strip()
        summary = "\n".join(
            [
                f"- 主题：{topic}",
                f"- 问题：{evidence.get('research_question') or '待补充'}",
                f"- 结论：{evidence.get('conclusion') or '待补充'}",
                f"- 证据质量：{quality}",
                f"- 来源数量：{evidence.get('source_count', 0)}，一手来源：{evidence.get('primary_source_count', 0)}",
                f"- 时效说明：{evidence.get('freshness') or '未说明'}",
                f"- 下游用途：{evidence.get('downstream_use') or '未说明'}",
            ]
        )

        source_rows = []
        for index, source in enumerate(evidence.get("sources") or [], start=1):
            if not isinstance(source, dict):
                continue
            source_rows.append(
                [
                    index,
                    source.get("title") or "未命名来源",
                    source.get("source_type") or "unknown",
                    source.get("fact") or "未摘录事实",
                    source.get("url") or "未提供",
                ]
            )

        reusable_payload = evidence.get("reusable_payload") if isinstance(evidence.get("reusable_payload"), dict) else {}
        reusable_lines = [
            f"- topic：{reusable_payload.get('topic') or topic}",
            f"- question：{reusable_payload.get('question') or evidence.get('research_question') or '待补充'}",
            f"- conclusion：{reusable_payload.get('conclusion') or evidence.get('conclusion') or '待补充'}",
        ]

        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(evidence, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", summary),
                _markdown_section("关键事实", _markdown_bullets(evidence.get("key_facts"))),
                _markdown_section(
                    "来源证据",
                    _markdown_table(["序号", "来源", "类型", "事实摘录", "URL"], source_rows),
                ),
                _markdown_section("冲突与不确定点", _markdown_bullets(
                    [
                        *_clean_strings(evidence.get("source_conflicts") if isinstance(evidence.get("source_conflicts"), list) else []),
                        *_clean_strings(evidence.get("uncertainty") if isinstance(evidence.get("uncertainty"), list) else []),
                    ]
                )),
                _markdown_section("可复用摘要", "\n".join(reusable_lines)),
                _markdown_section("建议下一步", _markdown_bullets(evidence.get("next_actions"))),
            ]
        ) + metadata

    def render_research_option_comparison_markdown(
        comparison_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 research 的方案对比 JSON 渲染成正式 Markdown 选型报告。"""

        if not comparison_json.strip():
            return "Error: comparison_json is required"
        comparison = json.loads(comparison_json)
        if not isinstance(comparison, dict):
            return "Error: comparison_json must be a JSON object"
        if comparison.get("type") != "research_option_comparison":
            return "Error: comparison_json type must be research_option_comparison"

        topic = str(comparison.get("topic") or "方案对比").strip()
        document_title = title.strip() or f"方案对比：{topic}"
        summary = "\n".join(
            [
                f"- 主题：{topic}",
                f"- 决策问题：{comparison.get('decision_question') or '待补充'}",
                f"- 推荐方案：{comparison.get('recommended_option') or '待定'}",
                f"- 证据质量：{comparison.get('evidence_quality') or 'unknown'}",
                f"- 来源数量：{comparison.get('source_count', 0)}，一手来源：{comparison.get('primary_source_count', 0)}",
                f"- 时效说明：{comparison.get('freshness') or '未说明'}",
                f"- 下游用途：{comparison.get('downstream_use') or '未说明'}",
            ]
        )

        option_rows = []
        for option in comparison.get("options") or []:
            if not isinstance(option, dict):
                continue
            option_rows.append(
                [
                    option.get("name") or "未命名方案",
                    option.get("score", 0),
                    "；".join(_clean_strings(option.get("strengths") if isinstance(option.get("strengths"), list) else [])) or "待补充",
                    "；".join(_clean_strings(option.get("weaknesses") if isinstance(option.get("weaknesses"), list) else [])) or "待补充",
                    "；".join(_clean_strings(option.get("best_for") if isinstance(option.get("best_for"), list) else [])) or "待补充",
                    "；".join(_clean_strings(option.get("avoid_when") if isinstance(option.get("avoid_when"), list) else [])) or "待补充",
                ]
            )

        source_rows = []
        for index, source in enumerate(comparison.get("sources") or [], start=1):
            if not isinstance(source, dict):
                continue
            source_rows.append(
                [
                    index,
                    source.get("title") or "未命名来源",
                    source.get("source_type") or "unknown",
                    source.get("fact") or "未摘录事实",
                    source.get("url") or "未提供",
                ]
            )

        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(comparison, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", summary),
                _markdown_section("评价维度", _markdown_bullets(comparison.get("criteria"))),
                _markdown_section("约束条件", _markdown_bullets(comparison.get("constraints"))),
                _markdown_section(
                    "候选方案对比",
                    _markdown_table(["方案", "评分", "优势", "短板", "适合场景", "不适合场景"], option_rows),
                ),
                _markdown_section(
                    "来源依据",
                    _markdown_table(["序号", "来源", "类型", "事实摘录", "URL"], source_rows),
                ),
                _markdown_section("不确定点", _markdown_bullets(comparison.get("uncertainty"))),
                _markdown_section("建议下一步", _markdown_bullets(comparison.get("next_actions"))),
            ]
        ) + metadata

    def render_execution_record_markdown(
        task_plan_json: str,
        gate_review_json: str = "",
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 planner/reviewer 的结构化结果渲染成正式执行记录。"""

        if not task_plan_json.strip():
            return "Error: task_plan_json is required"
        plan = json.loads(task_plan_json)
        if not isinstance(plan, dict):
            return "Error: task_plan_json must be a JSON object"
        review: dict[str, object] = {}
        if gate_review_json.strip():
            parsed_review = json.loads(gate_review_json)
            if not isinstance(parsed_review, dict):
                return "Error: gate_review_json must be a JSON object"
            review = parsed_review

        plan_title = str(plan.get("title") or plan.get("objective") or plan.get("goal") or "执行记录")
        document_title = title.strip() or f"执行记录：{plan_title}"
        goal = str(plan.get("goal") or plan.get("objective") or "待明确").strip()
        scope = str(plan.get("scope") or "待明确").strip()
        readiness = str(plan.get("readiness") or "").strip()
        decision_data = plan.get("decision") if isinstance(plan.get("decision"), dict) else {}
        decision = str(review.get("decision") or decision_data.get("action") or readiness or "待审查")
        repository = str(plan.get("repository") or "").strip()

        summary_lines = [
            f"- 目标：{goal}",
            f"- 范围：{scope}",
            f"- 门禁结论：{decision}",
        ]
        if repository:
            summary_lines.append(f"- 来源仓库：{repository}")
        if readiness:
            summary_lines.append(f"- 计划状态：{readiness}")

        phase_rows = []
        for index, phase in enumerate(plan.get("phases") or [], start=1):
            if not isinstance(phase, dict):
                continue
            phase_rows.append(
                [
                    phase.get("name") or phase.get("title") or f"阶段 {index}",
                    phase.get("task") or phase.get("objective") or "待明确",
                    phase.get("output") or phase.get("deliverable") or "待明确",
                    phase.get("done") or phase.get("acceptance") or "待补充",
                ]
            )

        checklist_rows = []
        for item in review.get("checklist") or []:
            if not isinstance(item, dict):
                continue
            checklist_rows.append(
                [
                    item.get("item") or "未命名检查项",
                    "通过" if item.get("passed") else "未通过",
                    item.get("evidence") or "待补充",
                ]
            )
        if checklist_rows:
            gate_content = "\n".join(
                [
                    f"- 审查对象：{review.get('review_target') or plan_title}",
                    f"- 结论：{decision}",
                    "",
                    _markdown_table(["检查项", "结果", "依据"], checklist_rows),
                ]
            )
        else:
            gate_content = f"- 结论：{decision}\n- 检查项：暂无门禁审查记录"

        risk_items = []
        risk_items.extend(_clean_strings(plan.get("risks") if isinstance(plan.get("risks"), list) else []))
        risk_items.extend(_clean_strings(review.get("risks") if isinstance(review.get("risks"), list) else []))
        next_actions = []
        next_actions.extend(_clean_strings(plan.get("next_steps") if isinstance(plan.get("next_steps"), list) else []))
        next_actions.extend(_clean_strings(review.get("next_actions") if isinstance(review.get("next_actions"), list) else []))

        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n"
                + json.dumps({"task_plan": plan, "gate_review": review}, ensure_ascii=False, indent=2)
                + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", "\n".join(summary_lines)),
                _markdown_section("阶段计划", _markdown_table(["阶段", "任务", "输出", "完成标准"], phase_rows)),
                _markdown_section("门禁审查", gate_content),
                _markdown_section("风险与限制", _markdown_bullets(risk_items)),
                _markdown_section("下一步", _markdown_bullets(next_actions)),
            ]
        ) + metadata

    def render_research_option_validation_plan_markdown(
        task_plan_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把 planner 的方案验证计划 JSON 渲染成正式 Markdown。"""

        if not task_plan_json.strip():
            return "Error: task_plan_json is required"
        plan = json.loads(task_plan_json)
        if not isinstance(plan, dict):
            return "Error: task_plan_json must be a JSON object"
        if plan.get("type") != "task_plan_from_research_option_comparison":
            return "Error: task_plan_json type must be task_plan_from_research_option_comparison"

        plan_title = str(plan.get("title") or "方案验证计划").strip()
        document_title = title.strip() or plan_title
        decision = plan.get("decision") if isinstance(plan.get("decision"), dict) else {}
        criteria = _clean_strings(plan.get("criteria") if isinstance(plan.get("criteria"), list) else [])
        candidates = _clean_strings(
            plan.get("candidate_options") if isinstance(plan.get("candidate_options"), list) else []
        )
        summary = "\n".join(
            [
                f"- 目标：{plan.get('goal') or '待明确'}",
                f"- 范围：{plan.get('scope') or '待明确'}",
                f"- 推荐方案：{decision.get('recommended_option') or '待确认'}",
                f"- 门禁结论：{decision.get('gate') or 'missing'}",
                f"- 建议动作：{decision.get('recommended_action') or 'review-first'}",
            ]
        )

        phase_rows = []
        for index, phase in enumerate(plan.get("phases") or [], start=1):
            if not isinstance(phase, dict):
                continue
            phase_rows.append(
                [
                    phase.get("name") or f"阶段 {index}",
                    phase.get("task") or "待明确",
                    phase.get("output") or "待明确",
                    phase.get("done") or "待补充",
                ]
            )

        risks = _clean_strings(plan.get("risks") if isinstance(plan.get("risks"), list) else [])
        next_steps = _clean_strings(plan.get("next_steps") if isinstance(plan.get("next_steps"), list) else [])
        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", summary),
                _markdown_section("候选方案", _markdown_bullets(candidates)),
                _markdown_section("评价维度", _markdown_bullets(criteria)),
                _markdown_section("验证阶段", _markdown_table(["阶段", "任务", "输出", "完成标准"], phase_rows)),
                _markdown_section("风险与限制", _markdown_bullets(risks)),
                _markdown_section("下一步", _markdown_bullets(next_steps)),
                _markdown_section(
                    "说明",
                    str(plan.get("note") or "这是方案验证计划，不代表已经完成实施或生产落地。"),
                ),
            ]
        ) + metadata

    def render_agent_collaboration_markdown(
        collaboration_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把入口 Agent 的协作路线 JSON 渲染成正式 Markdown。"""

        if not collaboration_json.strip():
            return "Error: collaboration_json is required"
        plan = json.loads(collaboration_json)
        if not isinstance(plan, dict):
            return "Error: collaboration_json must be a JSON object"
        if plan.get("type") != "agent_collaboration_plan":
            return "Error: collaboration_json type must be agent_collaboration_plan"

        task_type = str(plan.get("task_type") or "未分类任务").strip()
        user_goal = str(plan.get("user_goal") or "待明确").strip()
        expected_output = str(plan.get("expected_output") or "待明确").strip()
        should_persist = "是" if plan.get("should_persist") else "否"
        document_title = title.strip() or f"Agent 协作方案：{task_type}"

        summary = "\n".join(
            [
                f"- 用户目标：{user_goal}",
                f"- 任务类型：{task_type}",
                f"- 预期产物：{expected_output}",
                f"- 是否建议落盘：{should_persist}",
                f"- 执行状态：仅生成协作路线，尚未自动调用任何 Agent。",
            ]
        )

        route_rows = []
        for index, stage in enumerate(plan.get("handoff_sequence") or [], start=1):
            if not isinstance(stage, dict):
                continue
            input_contract = stage.get("input_contract")
            if isinstance(input_contract, dict):
                input_summary = input_contract.get("user_goal") or input_contract.get("upstream_result") or "按上游结果交接"
            else:
                input_summary = input_contract or "按上游结果交接"
            route_rows.append(
                [
                    stage.get("step") or index,
                    stage.get("agent_id") or "待指定",
                    stage.get("purpose") or "待明确",
                    input_summary,
                    stage.get("expected_output") or "待明确",
                ]
            )

        note = str(plan.get("note") or "这是多 Agent 协作路线规划，不代表任何 Agent 已经执行。").strip()
        constraints = _clean_strings(plan.get("constraints") if isinstance(plan.get("constraints"), list) else [])
        next_actions = _clean_strings(plan.get("next_actions") if isinstance(plan.get("next_actions"), list) else [])
        execution_notes = [
            "按协作路线逐阶段交接，每个阶段的结构化结果作为下一阶段输入。",
            "入口 Agent 负责判断路线和交接，不直接替代专业 Agent 执行。",
            note,
        ]

        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", summary),
                _markdown_section("协作路线", _markdown_table(["步骤", "Agent", "目的", "输入", "输出"], route_rows)),
                _markdown_section("约束", _markdown_bullets(constraints)),
                _markdown_section("执行说明", _markdown_bullets(execution_notes)),
                _markdown_section("下一步", _markdown_bullets(next_actions)),
            ]
        ) + metadata

    def render_agent_collaboration_progress_markdown(
        progress_json: str,
        title: str = "",
        include_raw_metadata: bool = False,
    ) -> str:
        """把入口 Agent 的协作进度 JSON 渲染成正式 Markdown。"""

        if not progress_json.strip():
            return "Error: progress_json is required"
        progress = json.loads(progress_json)
        if not isinstance(progress, dict):
            return "Error: progress_json must be a JSON object"
        if progress.get("type") != "agent_collaboration_progress":
            return "Error: progress_json type must be agent_collaboration_progress"

        task_type = str(progress.get("task_type") or "unknown").strip()
        status = str(progress.get("status") or "unknown").strip()
        document_title = title.strip() or f"Agent 协作进度：{task_type}"
        summary = "\n".join(
            [
                f"- 协作类型：{task_type}",
                f"- 当前状态：{status}",
                f"- 已完成阶段：{progress.get('completed_stage_count', 0)} / {progress.get('total_stage_count', 0)}",
                "- 执行边界：这是协作进度摘要，不代表任何 Agent 已经自动执行。",
            ]
        )

        stage_rows = []
        for item in progress.get("stages") or []:
            if not isinstance(item, dict):
                continue
            stage_rows.append(
                [
                    item.get("step") or "待定",
                    item.get("agent_id") or "unknown",
                    item.get("status") or "unknown",
                    item.get("expected_output") or "结构化结果",
                    item.get("output_summary") or "暂无",
                ]
            )

        next_stage = progress.get("next_stage") if isinstance(progress.get("next_stage"), dict) else {}
        if next_stage:
            next_stage_section = "\n".join(
                [
                    f"- 下一阶段：{next_stage.get('step')}",
                    f"- 目标 Agent：{next_stage.get('agent_id') or 'unknown'}",
                    f"- 任务：{next_stage.get('purpose') or '按职责处理'}",
                    f"- 预期输出：{next_stage.get('expected_output') or '结构化结果'}",
                ]
            )
        else:
            next_stage_section = "协作路线已完成，没有待交接的下一阶段。"

        handoff_args = progress.get("next_handoff_args") if isinstance(progress.get("next_handoff_args"), dict) else {}
        handoff_section = (
            "```json\n" + json.dumps(handoff_args, ensure_ascii=False, indent=2) + "\n```"
            if handoff_args
            else "暂无下一阶段 handoff 参数。"
        )
        next_actions = _clean_strings(progress.get("next_actions") if isinstance(progress.get("next_actions"), list) else [])

        metadata = ""
        if include_raw_metadata:
            metadata = "\n\n" + _markdown_section(
                "结构化元数据",
                "```json\n" + json.dumps(progress, ensure_ascii=False, indent=2) + "\n```",
            )

        return "\n\n".join(
            [
                f"# {document_title}",
                _markdown_section("摘要", summary),
                _markdown_section(
                    "阶段进度",
                    _markdown_table(["步骤", "Agent", "状态", "预期输出", "输出摘要"], stage_rows),
                ),
                _markdown_section("下一阶段", next_stage_section),
                _markdown_section("下一阶段交接参数", handoff_section),
                _markdown_section("下一步", _markdown_bullets(next_actions)),
            ]
        ) + metadata

    def outline_structured_document(
        title: str,
        document_type: str,
        target_audience: str = "",
        source_material_summary: str = "",
        required_sections: list[str] | None = None,
        missing_materials: list[str] | None = None,
        tone: str = "正式、清晰",
    ) -> str:
        """生成文档写作前的大纲和材料缺口检查。"""

        normalized_type = _normalize_document_type(document_type)
        default_sections = {
            "readme": ["项目简介", "核心能力", "架构预览", "快速开始", "配置说明", "运行与部署", "限制与注意事项"],
            "proposal": ["摘要", "背景", "目标", "方案", "实施计划", "风险与限制", "下一步"],
            "retrospective": ["摘要", "完成情况", "问题与卡点", "经验沉淀", "后续行动"],
            "technical-report": ["摘要", "背景", "技术分析", "权衡取舍", "结论", "风险与限制", "下一步"],
        }
        sections = _clean_strings(required_sections) or default_sections.get(
            normalized_type,
            ["摘要", "背景", "主要内容", "结论", "下一步"],
        )
        missing = _clean_strings(missing_materials)
        readiness = "ready" if not missing and source_material_summary.strip() else "needs_material"
        if not title.strip() or not normalized_type:
            readiness = "blocked"
        recommended_tool = (
            "save_structured_document"
            if normalized_type in {"readme", "proposal", "retrospective", "technical-report"}
            else "save_markdown_report"
        )
        return json.dumps(
            {
                "type": "document_outline",
                "title": title.strip(),
                "document_type": normalized_type,
                "target_audience": target_audience.strip(),
                "tone": tone.strip(),
                "source_material_summary": source_material_summary.strip(),
                "sections": sections,
                "missing_materials": missing,
                "readiness": readiness,
                "recommended_tool": recommended_tool,
                "next_steps": (
                    ["补齐缺失材料后再成文。", *missing[:4]]
                    if readiness == "needs_material"
                    else [f"按大纲成文，并使用 {recommended_tool} 落盘。"]
                ),
            },
            ensure_ascii=False,
            indent=2,
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

    def build_agent_handoff_prompt(
        user_goal: str,
        target_agent_id: str,
        context_summary: str,
        constraints: list[str] | None = None,
        expected_output: str = "",
        source_platform: str = "",
        should_persist: bool = False,
        known_inputs: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> str:
        """构造入口 Agent 交给专用 Agent 的标准 handoff_prompt。"""

        goal = user_goal.strip()
        target = target_agent_id.strip() or "unknown-agent"
        context = context_summary.strip()
        if not goal:
            return "Error: user_goal is required"
        if not context:
            return "Error: context_summary is required"
        sections = [
            f"目标 Agent：{target}",
            "",
            "## 用户原始目标",
            goal,
            "",
            "## 关键上下文",
            context,
            "",
            "## 已知输入",
            _markdown_bullets(known_inputs),
            "",
            "## 已知约束",
            _markdown_bullets(constraints),
            "",
            "## 期望输出",
            expected_output.strip() or "给出清晰、可执行的中文结果；如需落盘，请说明文件路径。",
            "",
            "## 落盘要求",
            "需要落盘" if should_persist else "未明确要求落盘；如生成正式报告或计划，先询问或说明默认保存位置。",
            "",
            "## 来源平台",
            source_platform.strip() or "unknown",
            "",
            "## 待确认问题",
            _markdown_bullets(open_questions),
        ]
        return "\n".join(sections).strip()

    def build_collaboration_stage_handoff(
        collaboration_plan_json: str,
        stage: int = 1,
        upstream_result_summary: str = "",
        upstream_result_json: str = "",
        additional_context: str = "",
    ) -> str:
        """为多 Agent 协作路线中的某个阶段生成标准交接提示。"""

        if not collaboration_plan_json.strip():
            return "Error: collaboration_plan_json is required"
        plan = json.loads(collaboration_plan_json)
        if not isinstance(plan, dict):
            return "Error: collaboration_plan_json must be a JSON object"
        if plan.get("type") != "agent_collaboration_plan":
            return "Error: collaboration_plan_json type must be agent_collaboration_plan"
        handoffs = [item for item in plan.get("handoff_sequence") or [] if isinstance(item, dict)]
        if not handoffs:
            return "Error: collaboration_plan_json has no handoff_sequence"
        if stage < 1 or stage > len(handoffs):
            return f"Error: stage must be between 1 and {len(handoffs)}"

        current = handoffs[stage - 1]
        previous = handoffs[stage - 2] if stage > 1 else {}
        input_contract = current.get("input_contract") if isinstance(current.get("input_contract"), dict) else {}
        constraints = _clean_strings(input_contract.get("constraints") if isinstance(input_contract.get("constraints"), list) else [])
        upstream_text = upstream_result_summary.strip()
        if not upstream_text and upstream_result_json.strip():
            try:
                parsed_upstream = json.loads(upstream_result_json)
                upstream_text = json.dumps(parsed_upstream, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                upstream_text = upstream_result_json.strip()

        sections = [
            f"目标 Agent：{current.get('agent_id') or 'unknown'}",
            f"协作类型：{plan.get('task_type') or 'unknown'}",
            f"阶段：{stage}/{len(handoffs)}",
            "",
            "## 用户原始目标",
            str(plan.get("user_goal") or input_contract.get("user_goal") or "待补充").strip(),
            "",
            "## 本阶段任务",
            str(current.get("purpose") or "按目标 Agent 职责处理。").strip(),
            "",
            "## 期望输出",
            str(current.get("expected_output") or "结构化阶段输出。").strip(),
            "",
            "## 上游阶段",
            (
                f"{previous.get('step') or stage - 1}. `{previous.get('agent_id')}`："
                f"{previous.get('expected_output') or previous.get('purpose') or '上游结果'}"
                if previous
                else "这是第一阶段，无上游阶段。"
            ),
            "",
            "## 上游结果摘要",
            upstream_text or "暂无上游结果；如果这是第一阶段，请基于用户原始目标开始。",
            "",
            "## 已知约束",
            _markdown_bullets(constraints),
            "",
            "## 额外上下文",
            additional_context.strip() or "无。",
            "",
            "## 边界",
            "这是阶段交接提示，不代表目标 Agent 已经执行；完成后请输出结构化结果，供下一阶段继续使用。",
        ]
        return "\n".join(sections).strip()

    def summarize_collaboration_progress(
        collaboration_plan_json: str,
        completed_stage_outputs: list[dict[str, Any]] | None = None,
        current_stage: int = 0,
    ) -> str:
        """汇总多 Agent 协作路线进度，并给出下一阶段交接参数。"""

        if not collaboration_plan_json.strip():
            return "Error: collaboration_plan_json is required"
        plan = json.loads(collaboration_plan_json)
        if not isinstance(plan, dict):
            return "Error: collaboration_plan_json must be a JSON object"
        if plan.get("type") != "agent_collaboration_plan":
            return "Error: collaboration_plan_json type must be agent_collaboration_plan"
        handoffs = [item for item in plan.get("handoff_sequence") or [] if isinstance(item, dict)]
        if not handoffs:
            return "Error: collaboration_plan_json has no handoff_sequence"

        outputs = [item for item in completed_stage_outputs or [] if isinstance(item, dict)]
        completed_by_step: dict[int, dict[str, Any]] = {}
        for item in outputs:
            try:
                step = int(item.get("step") or item.get("stage") or 0)
            except (TypeError, ValueError):
                step = 0
            if 1 <= step <= len(handoffs):
                completed_by_step[step] = item
        completed_count = max(completed_by_step.keys(), default=0)
        if current_stage > 0:
            completed_count = min(max(0, current_stage - 1), len(handoffs))
        next_stage = completed_count + 1 if completed_count < len(handoffs) else None
        status = "completed" if next_stage is None else ("not-started" if completed_count == 0 else "in-progress")

        stage_rows = []
        for index, stage in enumerate(handoffs, start=1):
            output = completed_by_step.get(index, {})
            stage_rows.append(
                {
                    "step": index,
                    "agent_id": stage.get("agent_id") or "unknown",
                    "expected_output": stage.get("expected_output") or "结构化结果",
                    "status": "completed" if index <= completed_count else ("next" if index == next_stage else "pending"),
                    "output_summary": str(output.get("summary") or output.get("output_summary") or "").strip(),
                }
            )

        latest_output = completed_by_step.get(completed_count, {}) if completed_count else {}
        upstream_summary = str(
            latest_output.get("summary")
            or latest_output.get("output_summary")
            or latest_output.get("result")
            or ""
        ).strip()
        upstream_json = ""
        if latest_output:
            raw_payload = latest_output.get("json") or latest_output.get("payload")
            if raw_payload is not None:
                upstream_json = (
                    raw_payload
                    if isinstance(raw_payload, str)
                    else json.dumps(raw_payload, ensure_ascii=False, indent=2)
                )

        next_stage_data = handoffs[next_stage - 1] if next_stage is not None else {}
        next_handoff_args = None
        if next_stage is not None:
            next_handoff_args = {
                "collaboration_plan_json": collaboration_plan_json,
                "stage": next_stage,
                "upstream_result_summary": upstream_summary,
                "upstream_result_json": upstream_json,
            }

        return json.dumps(
            {
                "type": "agent_collaboration_progress",
                "task_type": plan.get("task_type") or "unknown",
                "status": status,
                "completed_stage_count": completed_count,
                "total_stage_count": len(handoffs),
                "next_stage": (
                    {
                        "step": next_stage,
                        "agent_id": next_stage_data.get("agent_id") or "unknown",
                        "purpose": next_stage_data.get("purpose") or "按职责处理",
                        "expected_output": next_stage_data.get("expected_output") or "结构化结果",
                    }
                    if next_stage is not None
                    else None
                ),
                "stages": stage_rows,
                "next_handoff_args": next_handoff_args,
                "next_actions": (
                    ["协作路线已完成，可交给 doc-writer 汇总或向用户报告结果。"]
                    if next_stage is None
                    else [
                        f"调用 build_collaboration_stage_handoff 生成第 {next_stage} 阶段交接提示。",
                        "把上一阶段结构化输出作为 upstream_result_summary 或 upstream_result_json。",
                    ]
                ),
                "boundary": "这是协作进度摘要，不代表任何 Agent 已经自动执行。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def _agent_collaboration_route_templates() -> dict[str, list[dict[str, str]]]:
        """返回入口层可规划的多 Agent 协作路线模板。"""

        return {
            "repo-adoption": [
                {
                    "agent_id": "repo-analyzer",
                    "purpose": "分析仓库用途、结构、质量、Gateway 可借鉴点，并输出仓库分析、风险扫描和可选采纳路线。",
                    "expected_output": "github_repo_analysis、github_repo_risk_scan 和可选 github_repo_adoption_plan JSON。",
                },
                {
                    "agent_id": "reviewer",
                    "purpose": "审查仓库风险扫描是否足够支撑采纳、引用或复用，给出 go / conditional-go / no-go 门禁结论。",
                    "expected_output": "github_repo_risk_gate_review JSON。",
                },
                {
                    "agent_id": "planner",
                    "purpose": "把仓库分析、风险门禁和可选采纳路线整合为阶段计划、验收标准和下一步动作。",
                    "expected_output": "task_plan_from_repo_review JSON。",
                },
                {
                    "agent_id": "doc-writer",
                    "purpose": "把仓库分析、风险扫描、门禁结论和阶段计划整理成正式 Markdown 报告或执行记录。",
                    "expected_output": "正式 Markdown 报告或报告路径。",
                },
            ],
            "research-document": [
                {
                    "agent_id": "research",
                    "purpose": "联网检索、核验来源、整理可复用证据包。",
                    "expected_output": "research_evidence_pack JSON。",
                },
                {
                    "agent_id": "doc-writer",
                    "purpose": "把证据包整理成正式报告、方案或说明文档。",
                    "expected_output": "正式 Markdown 文档。",
                },
            ],
            "research-option-validation": [
                {
                    "agent_id": "research",
                    "purpose": "围绕技术选型、方案对比或中间件取舍做来源核验，并输出结构化方案对比。",
                    "expected_output": "research_option_comparison JSON。",
                },
                {
                    "agent_id": "reviewer",
                    "purpose": "审查方案对比是否具备决策问题、候选方案、评价维度、来源、推荐项和不确定点。",
                    "expected_output": "research_option_comparison_gate_review JSON。",
                },
                {
                    "agent_id": "planner",
                    "purpose": "把方案对比和门禁结论转成最小验证计划，no-go 时只安排补证。",
                    "expected_output": "task_plan_from_research_option_comparison JSON。",
                },
                {
                    "agent_id": "reviewer",
                    "purpose": "审查方案验证计划是否具备执行前条件，并阻止 no-go 计划直接进入实现。",
                    "expected_output": "task_plan_gate_review JSON。",
                },
                {
                    "agent_id": "doc-writer",
                    "purpose": "把方案对比、门禁结论和验证计划整理成正式 Markdown 方案验证文档。",
                    "expected_output": "正式 Markdown 方案验证计划或报告路径。",
                },
            ],
            "plan-review-document": [
                {
                    "agent_id": "planner",
                    "purpose": "拆解目标、阶段、依赖、风险和验收标准。",
                    "expected_output": "结构化任务计划 JSON。",
                },
                {
                    "agent_id": "reviewer",
                    "purpose": "对任务计划做门禁审查。",
                    "expected_output": "task_plan_gate_review JSON。",
                },
                {
                    "agent_id": "doc-writer",
                    "purpose": "渲染执行记录或计划文档。",
                    "expected_output": "正式 Markdown 文档。",
                },
            ],
            "ops-diagnosis": [
                {
                    "agent_id": "ops",
                    "purpose": "只读采集运行事件、失败投递、告警和健康状态。",
                    "expected_output": "ops_runtime_diagnostics 或 ops_health_summary JSON。",
                },
                {
                    "agent_id": "doc-writer",
                    "purpose": "必要时把诊断结论整理成排障记录。",
                    "expected_output": "排障 Markdown 记录。",
                },
            ],
        }

    def _agent_collaboration_route_aliases() -> dict[str, str]:
        """返回协作路线别名，便于入口层接受更自然的 task_type。"""

        return {
            "repo-analysis": "repo-adoption",
            "github": "repo-adoption",
            "document": "research-document",
            "research": "research-document",
            "option-validation": "research-option-validation",
            "technology-selection": "research-option-validation",
            "tech-selection": "research-option-validation",
            "planning": "plan-review-document",
            "review": "plan-review-document",
            "ops": "ops-diagnosis",
        }

    def list_agent_collaboration_routes(
        task_types: list[str] | None = None,
        include_stages: bool = True,
    ) -> str:
        """列出入口层支持的多 Agent 协作路线，不执行任何 Agent。"""

        templates = _agent_collaboration_route_templates()
        aliases = _agent_collaboration_route_aliases()
        wanted = {
            aliases.get(item.strip().lower().replace("_", "-"), item.strip().lower().replace("_", "-"))
            for item in task_types or []
            if item.strip()
        }
        routes = []
        for route_type, stages in templates.items():
            if wanted and route_type not in wanted:
                continue
            row: dict[str, Any] = {
                "task_type": route_type,
                "agent_sequence": [stage["agent_id"] for stage in stages],
                "stage_count": len(stages),
                "aliases": sorted(alias for alias, target in aliases.items() if target == route_type),
                "note": "该路线只表示建议协作顺序，不会自动调用任何 Agent。",
            }
            if include_stages:
                row["stages"] = [
                    {
                        "step": index,
                        "agent_id": stage["agent_id"],
                        "purpose": stage["purpose"],
                        "expected_output": stage["expected_output"],
                    }
                    for index, stage in enumerate(stages, start=1)
                ]
            routes.append(row)

        return json.dumps(
            {
                "type": "agent_collaboration_route_catalog",
                "count": len(routes),
                "routes": routes,
                "aliases": aliases,
                "boundary": "这是协作路线目录，不代表任何 Agent 已经自动执行。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def plan_agent_collaboration(
        user_goal: str,
        task_type: str = "",
        preferred_agents: list[str] | None = None,
        constraints: list[str] | None = None,
        expected_output: str = "",
        should_persist: bool = False,
    ) -> str:
        """为复杂任务生成多 Agent 协作路线，不执行任何 Agent。"""

        goal = user_goal.strip()
        if not goal:
            return "Error: user_goal is required"
        normalized_type = task_type.strip().lower().replace("_", "-")
        requested = [agent.strip() for agent in preferred_agents or [] if agent.strip()]
        constraints_clean = _clean_strings(constraints)

        templates = _agent_collaboration_route_templates()
        aliases = _agent_collaboration_route_aliases()
        selected_type = aliases.get(normalized_type, normalized_type) or "plan-review-document"
        stages = templates.get(selected_type, templates["plan-review-document"])
        if requested:
            stages = [stage for stage in stages if stage["agent_id"] in requested] or [
                {
                    "agent_id": agent_id,
                    "purpose": "按用户指定参与协作。",
                    "expected_output": "按该 Agent 职责输出结构化结果。",
                }
                for agent_id in requested
            ]

        handoff_sequence = []
        for index, stage in enumerate(stages, start=1):
            handoff_sequence.append(
                {
                    "step": index,
                    "agent_id": stage["agent_id"],
                    "purpose": stage["purpose"],
                    "input_contract": {
                        "user_goal": goal,
                        "constraints": constraints_clean,
                        "upstream_result": "上一阶段结构化输出；第一阶段为空。",
                    },
                    "expected_output": stage["expected_output"],
                }
            )

        return json.dumps(
            {
                "type": "agent_collaboration_plan",
                "task_type": selected_type,
                "user_goal": goal,
                "expected_output": expected_output.strip() or handoff_sequence[-1]["expected_output"],
                "should_persist": bool(should_persist),
                "constraints": constraints_clean,
                "handoff_sequence": handoff_sequence,
                "next_actions": [
                    "先把第一阶段 handoff_prompt 交给对应 Agent。",
                    "每个阶段完成后，把结构化输出作为下一阶段 upstream_result。",
                    "当前工具只生成协作路线，不会自动调用任何 Agent。",
                ],
                "note": "这是多 Agent 协作路线规划，不代表任何 Agent 已经执行。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def classify_task_intent(user_text: str, context_hint: str = "") -> str:
        """把用户输入归类到主入口可处理或更适合交给的专用 Agent。"""

        text = f"{user_text} {context_hint}".strip()
        normalized = text.lower()
        catalog = [
            {
                "intent": "research-option-validation",
                "agent": "research",
                "keywords": (
                    "技术选型",
                    "方案对比",
                    "中间件取舍",
                    "为什么选择",
                    "为什么选",
                    "选 rabbitmq",
                    "选 redis",
                    "选 kafka",
                    "对比",
                    "验证计划",
                    "最小验证",
                    "落地计划",
                    "正式报告",
                    "selection",
                    "comparison",
                    "validation plan",
                ),
                "reason": "用户不只是要资料对比，还需要方案门禁、验证计划或正式文档，适合 research → reviewer → planner → reviewer → doc-writer 协作。",
                "next": "调用 plan_agent_collaboration，task_type 使用 research-option-validation，按 research → reviewer → planner → reviewer → doc-writer 串联。",
                "direct": False,
                "requires_collaboration": True,
                "collaboration_task_type": "research-option-validation",
            },
            {
                "intent": "repo-adoption",
                "agent": "repo-analyzer",
                "keywords": (
                    "github.com/",
                    "gitlab.com/",
                    "仓库",
                    "repo",
                    "repository",
                    "采纳",
                    "引入",
                    "接入",
                    "复用",
                    "风险",
                    "计划",
                    "报告",
                    "是否值得",
                    "adoption",
                ),
                "reason": "用户同时关注仓库分析、风险判断、采纳计划或正式报告，需要多 Agent 协作路线。",
                "next": "调用 plan_agent_collaboration，task_type 使用 repo-adoption，按 repo-analyzer → reviewer → planner → doc-writer 串联。",
                "direct": False,
                "requires_collaboration": True,
                "collaboration_task_type": "repo-adoption",
            },
            {
                "intent": "repo-analysis",
                "agent": "repo-analyzer",
                "keywords": (
                    "github.com/",
                    "gitlab.com/",
                    "仓库",
                    "repo",
                    "repository",
                    "项目分析",
                    "代码库",
                ),
                "reason": "用户关注代码仓库、项目结构或可借鉴点，repo-analyzer 更适合处理。",
                "next": "提取仓库链接、分析目标和输出格式后，建议交给 repo-analyzer。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "planning",
                "agent": "planner",
                "keywords": (
                    "规划",
                    "计划",
                    "阶段",
                    "路线图",
                    "roadmap",
                    "拆解",
                    "任务清单",
                ),
                "reason": "用户需要拆目标、排阶段或明确验收标准，planner 更适合处理。",
                "next": "先明确目标、边界、约束和完成标准，再建议交给 planner。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "document",
                "agent": "doc-writer",
                "keywords": (
                    "readme",
                    "文档",
                    "报告",
                    "手册",
                    "整理成md",
                    "markdown",
                    "写成",
                ),
                "reason": "用户需要沉淀正式 Markdown 或结构化文档，doc-writer 更适合处理。",
                "next": "确认文档类型、读者、材料来源和保存位置，再建议交给 doc-writer。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "review",
                "agent": "reviewer",
                "keywords": (
                    "审查",
                    "review",
                    "风险",
                    "漏洞",
                    "问题",
                    "隐患",
                    "是否合理",
                ),
                "reason": "用户需要发现风险、缺口或边界问题，reviewer 更适合处理。",
                "next": "整理审查对象、风险关注点和证据范围后，建议交给 reviewer。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "research",
                "agent": "research",
                "keywords": (
                    "搜索",
                    "调研",
                    "查一下",
                    "最新",
                    "资料",
                    "来源",
                    "新闻",
                    "对比",
                ),
                "reason": "用户需要联网检索、来源核验或资料对比，research 更适合处理。",
                "next": "明确检索问题、时间范围和需要的来源粒度，再建议交给 research。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "ops",
                "agent": "ops",
                "keywords": (
                    "docker",
                    "容器",
                    "redis",
                    "rabbitmq",
                    "postgres",
                    "报错",
                    "日志",
                    "磁盘",
                    "运维",
                    "健康检查",
                ),
                "reason": "用户关注系统运行、容器、中间件或只读排障，ops 更适合处理。",
                "next": "先确认环境、错误现象和只读排查范围，再建议交给 ops。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "diet",
                "agent": "diet-assistant-zhanghaibo",
                "keywords": (
                    "饮食",
                    "减肥",
                    "减脂",
                    "热量",
                    "体重",
                    "早餐",
                    "午餐",
                    "晚餐",
                    "蛋白质",
                ),
                "reason": "用户关注个人饮食、体重或减脂记录，饮食助手更适合处理。",
                "next": "确认用户身份和餐食/体重数据后，建议交给 diet-assistant-zhanghaibo。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
            {
                "intent": "personal",
                "agent": "personal-secretary-zhanghaibo",
                "keywords": (
                    "待办",
                    "提醒",
                    "复盘",
                    "日程",
                    "明天",
                    "今天安排",
                    "个人计划",
                    "时间块",
                ),
                "reason": "用户关注个人计划、待办、提醒或复盘，个人秘书更适合处理。",
                "next": "确认用户身份、时间范围和需要记录的事项后，建议交给 personal-secretary-zhanghaibo。",
                "direct": False,
                "requires_collaboration": False,
                "collaboration_task_type": "",
            },
        ]

        repo_triggers = ("github.com/", "gitlab.com/", "仓库", "repo", "repository", "代码库")
        adoption_triggers = (
            "采纳",
            "引入",
            "接入",
            "复用",
            "风险",
            "计划",
            "报告",
            "是否值得",
            "adoption",
        )
        option_triggers = (
            "技术选型",
            "方案对比",
            "中间件取舍",
            "为什么选择",
            "为什么选",
            "选型",
            "对比",
            "selection",
            "comparison",
        )
        option_workflow_triggers = (
            "验证计划",
            "最小验证",
            "落地计划",
            "计划",
            "报告",
            "文档",
            "审查",
            "门禁",
            "风险",
            "validation",
            "report",
            "document",
            "review",
        )

        def intent_score(row: dict[str, Any]) -> int:
            score_value = _keyword_score(normalized, row["keywords"])
            if row.get("intent") == "repo-adoption":
                has_repo = _keyword_score(normalized, repo_triggers) > 0
                has_adoption = _keyword_score(normalized, adoption_triggers) > 0
                if not (has_repo and has_adoption):
                    return 0
            if row.get("intent") == "research-option-validation":
                has_option = _keyword_score(normalized, option_triggers) > 0
                has_workflow = _keyword_score(normalized, option_workflow_triggers) > 0
                if not (has_option and has_workflow):
                    return 0
            return score_value

        best = max(catalog, key=intent_score)
        score = intent_score(best)
        if not text:
            intent = "unknown"
            agent = "main"
            confidence = 0.0
            reason = "用户输入为空，无法判断任务意图。"
            next_step = "请补充要处理的问题或目标。"
            can_answer_directly = False
            requires_collaboration = False
            collaboration_task_type = ""
        elif score <= 0:
            intent = "chat"
            agent = "main"
            confidence = 0.55
            reason = "未命中专用 Agent 的明显触发词，main 可以先直接回答。"
            next_step = "直接回答；如果后续出现复杂目标，再重新分类。"
            can_answer_directly = True
            requires_collaboration = False
            collaboration_task_type = ""
        else:
            intent = str(best["intent"])
            agent = str(best["agent"])
            confidence = min(0.95, 0.55 + score * 0.12)
            reason = str(best["reason"])
            next_step = str(best["next"])
            can_answer_directly = bool(best["direct"])
            requires_collaboration = bool(best.get("requires_collaboration", False))
            collaboration_task_type = str(best.get("collaboration_task_type", ""))

        return json.dumps(
            {
                "type": "task_intent_classification",
                "intent": intent,
                "confidence": round(confidence, 2),
                "recommended_agent_id": agent,
                "requires_collaboration": requires_collaboration,
                "collaboration_task_type": collaboration_task_type,
                "reason": reason,
                "can_answer_directly": can_answer_directly,
                "suggested_next_step": next_step,
                "note": "这是任务意图识别结果，不会自动调用目标 Agent。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def format_entry_response(
        intent: str,
        recommended_agent_id: str,
        reason: str,
        current_reply: str,
        context_summary: str = "",
        handoff_prompt: str = "",
        can_answer_directly: bool = False,
        requires_collaboration: bool = False,
        collaboration_task_type: str = "",
        collaboration_plan_json: str = "",
    ) -> str:
        """把入口 Agent 的分类和委派结论格式化为稳定中文回复。"""

        normalized_intent = intent.strip() or "unknown"
        agent_id = recommended_agent_id.strip() or "main"
        collaboration_plan: dict[str, Any] = {}
        if collaboration_plan_json.strip():
            parsed_plan = json.loads(collaboration_plan_json)
            if not isinstance(parsed_plan, dict):
                return "Error: collaboration_plan_json must be a JSON object"
            if parsed_plan.get("type") != "agent_collaboration_plan":
                return "Error: collaboration_plan_json type must be agent_collaboration_plan"
            collaboration_plan = parsed_plan

        if requires_collaboration or collaboration_plan:
            task_type = (
                collaboration_task_type.strip()
                or str(collaboration_plan.get("task_type") or "").strip()
                or normalized_intent
            )
            route_rows = []
            for stage in collaboration_plan.get("handoff_sequence") or []:
                if not isinstance(stage, dict):
                    continue
                route_rows.append(
                    f"{stage.get('step') or len(route_rows) + 1}. "
                    f"`{stage.get('agent_id') or 'unknown'}`："
                    f"{stage.get('purpose') or '按职责处理'}"
                )
            if not route_rows:
                route_rows.append("1. 调用 `plan_agent_collaboration` 生成协作路线。")
            lines = [
                f"判断：这属于 {normalized_intent}，需要多 Agent 协作。",
                f"协作类型：`{task_type}`。",
                f"原因：{reason.strip() or '该任务需要多个能力 Agent 串联处理。'}",
                f"交接摘要：{context_summary.strip() or '请保留用户原始目标、关键上下文和期望输出。'}",
                "",
                "建议路线：",
                *route_rows,
                "",
                f"当前简要回复：{current_reply.strip() or '我已识别为复杂任务，会先生成协作路线，再按阶段推进。'}",
                "",
                "说明：这是协作路线说明，不代表这些 Agent 已经自动执行。",
            ]
            if handoff_prompt.strip():
                lines.extend(["", "可复制交接提示：", handoff_prompt.strip()])
            return "\n".join(lines)

        if can_answer_directly or agent_id == "main":
            return "\n".join(
                [
                    current_reply.strip() or "我可以先直接处理这个问题。",
                    "",
                    f"判断：这属于 {normalized_intent}，当前由 `main` 直接处理。",
                ]
            ).strip()

        lines = [
            f"判断：这属于 {normalized_intent}。",
            f"建议交给：`{agent_id}`。",
            f"原因：{reason.strip() or '该任务更适合专用 Agent 处理。'}",
            f"交接摘要：{context_summary.strip() or '请保留用户原始目标、关键上下文和期望输出。'}",
            f"当前简要回复：{current_reply.strip() or '我已识别任务类型，可以按上述上下文继续推进。'}",
        ]
        if handoff_prompt.strip():
            lines.extend(["", "可复制交接提示：", handoff_prompt.strip()])
        lines.extend(["", "说明：这是入口回复，不代表目标 Agent 已经自动执行。"])
        return "\n".join(lines)

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

    def explain_agent_route(
        user_goal: str,
        intent: str,
        recommended_agent_id: str = "",
        reason: str = "",
        requires_collaboration: bool = False,
        collaboration_task_type: str = "",
        collaboration_plan_json: str = "",
        next_step: str = "",
    ) -> str:
        """解释入口 Agent 为什么选择单 Agent 委派或多 Agent 协作路线。"""

        goal = user_goal.strip()
        normalized_intent = intent.strip() or "unknown"
        collaboration_plan: dict[str, Any] = {}
        if collaboration_plan_json.strip():
            parsed_plan = json.loads(collaboration_plan_json)
            if not isinstance(parsed_plan, dict):
                return "Error: collaboration_plan_json must be a JSON object"
            if parsed_plan.get("type") != "agent_collaboration_plan":
                return "Error: collaboration_plan_json type must be agent_collaboration_plan"
            collaboration_plan = parsed_plan

        route_type = "collaboration" if requires_collaboration or collaboration_plan else "single-agent"
        stages = []
        for stage in collaboration_plan.get("handoff_sequence") or []:
            if not isinstance(stage, dict):
                continue
            stages.append(
                {
                    "step": int(stage.get("step") or len(stages) + 1),
                    "agent_id": str(stage.get("agent_id") or "unknown"),
                    "purpose": str(stage.get("purpose") or "按职责处理"),
                    "expected_output": str(stage.get("expected_output") or "结构化结果"),
                }
            )
        if route_type == "single-agent":
            agent_id = recommended_agent_id.strip() or "main"
            stages = [
                {
                    "step": 1,
                    "agent_id": agent_id,
                    "purpose": reason.strip() or "该任务可由单个 Agent 处理。",
                    "expected_output": "直接回复或目标 Agent 的结构化结果。",
                }
            ]

        if not goal:
            readiness = "needs_context"
            route_reason = "缺少用户原始目标，无法稳定解释路由。"
        elif route_type == "collaboration" and not stages:
            readiness = "needs_collaboration_plan"
            route_reason = "任务需要协作，但缺少 agent_collaboration_plan。"
        else:
            readiness = "ready"
            route_reason = reason.strip() or "已根据任务意图和 Agent 能力边界选择路线。"

        return json.dumps(
            {
                "type": "agent_route_explanation",
                "user_goal": goal,
                "intent": normalized_intent,
                "route_type": route_type,
                "collaboration_task_type": collaboration_task_type.strip()
                or str(collaboration_plan.get("task_type") or ""),
                "recommended_agent_id": recommended_agent_id.strip() or (stages[0]["agent_id"] if stages else "main"),
                "reason": route_reason,
                "readiness": readiness,
                "stages": stages,
                "next_step": next_step.strip()
                or (
                    "先调用 plan_agent_collaboration 生成协作路线。"
                    if readiness == "needs_collaboration_plan"
                    else "按第一阶段交接提示继续推进。"
                ),
                "boundary": "这是路由解释，不代表任何目标 Agent 已经自动执行。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def prepare_entry_route_response(
        user_text: str,
        context_hint: str = "",
        source_platform: str = "",
        should_persist: bool = False,
        constraints: list[str] | None = None,
        expected_output: str = "",
    ) -> str:
        """一站式准备入口层路由响应，不执行任何目标 Agent。"""

        goal = user_text.strip()
        if not goal:
            return "Error: user_text is required"
        classification = json.loads(classify_task_intent(goal, context_hint=context_hint))
        if not isinstance(classification, dict):
            return "Error: classify_task_intent returned non-object"

        constraints_clean = _clean_strings(constraints)
        collaboration_plan_json = ""
        handoff_prompt = ""
        if classification.get("requires_collaboration"):
            collaboration_plan_json = plan_agent_collaboration(
                user_goal=goal,
                task_type=str(classification.get("collaboration_task_type") or ""),
                constraints=constraints_clean,
                expected_output=expected_output,
                should_persist=should_persist,
            )
        elif classification.get("recommended_agent_id") not in {"", "main"}:
            handoff_prompt = build_agent_handoff_prompt(
                user_goal=goal,
                target_agent_id=str(classification.get("recommended_agent_id") or "main"),
                context_summary=str(classification.get("reason") or context_hint or "入口层识别到专用 Agent 更适合处理。"),
                constraints=constraints_clean,
                expected_output=expected_output,
                source_platform=source_platform,
                should_persist=should_persist,
                known_inputs=[context_hint] if context_hint.strip() else [],
            )

        route_explanation_json = explain_agent_route(
            user_goal=goal,
            intent=str(classification.get("intent") or "unknown"),
            recommended_agent_id=str(classification.get("recommended_agent_id") or "main"),
            reason=str(classification.get("reason") or ""),
            requires_collaboration=bool(classification.get("requires_collaboration")),
            collaboration_task_type=str(classification.get("collaboration_task_type") or ""),
            collaboration_plan_json=collaboration_plan_json,
            next_step=str(classification.get("suggested_next_step") or ""),
        )
        formatted_response = format_entry_response(
            intent=str(classification.get("intent") or "unknown"),
            recommended_agent_id=str(classification.get("recommended_agent_id") or "main"),
            reason=str(classification.get("reason") or ""),
            context_summary=context_hint.strip() or goal,
            handoff_prompt=handoff_prompt,
            current_reply="我已完成入口路由判断，可以按下面路线继续推进。",
            can_answer_directly=bool(classification.get("can_answer_directly")),
            requires_collaboration=bool(classification.get("requires_collaboration")),
            collaboration_task_type=str(classification.get("collaboration_task_type") or ""),
            collaboration_plan_json=collaboration_plan_json,
        )

        return json.dumps(
            {
                "type": "entry_route_preparation",
                "classification": classification,
                "collaboration_plan": json.loads(collaboration_plan_json) if collaboration_plan_json else None,
                "route_explanation": json.loads(route_explanation_json),
                "handoff_prompt": handoff_prompt,
                "formatted_response": formatted_response,
                "boundary": "这是入口层路由准备结果，不代表任何目标 Agent 已经自动执行。",
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

    def summarize_ops_health(health_json: str) -> str:
        """把只读健康采集结果整理为风险等级、关键发现和安全建议。"""

        if not health_json.strip():
            return "Error: health_json is required"
        data = json.loads(health_json)
        if not isinstance(data, dict):
            return "Error: health_json must be a JSON object"
        if data.get("type") != "ops_readonly_health":
            return "Error: health_json type must be ops_readonly_health"

        disk = data.get("disk") or {}
        if not isinstance(disk, dict):
            disk = {}
        paths = data.get("paths") or []
        if not isinstance(paths, list):
            paths = []
        flags = _clean_strings(data.get("risk_flags") if isinstance(data.get("risk_flags"), list) else [])
        usage_percent = float(disk.get("usage_percent") or 0)
        missing_paths = [
            str(row.get("name", "unknown"))
            for row in paths
            if isinstance(row, dict) and not row.get("exists")
        ]
        large_paths = [
            {
                "name": str(row.get("name", "unknown")),
                "size": str(row.get("size", "0 B")),
                "file_count": int(row.get("file_count") or 0),
            }
            for row in paths
            if isinstance(row, dict) and int(row.get("size_bytes") or 0) >= 1024 * 1024 * 1024
        ]

        if "disk_critical" in flags or usage_percent >= 90 or missing_paths:
            risk_level = "critical" if usage_percent >= 90 else "warning"
        elif "disk_warning" in flags or usage_percent >= 80 or large_paths:
            risk_level = "warning"
        else:
            risk_level = "normal"

        findings = [
            f"磁盘使用率 {usage_percent:.1f}%，可用空间 {disk.get('free', 'unknown')}。",
        ]
        if missing_paths:
            findings.append(f"关键路径缺失：{', '.join(missing_paths)}。")
        if large_paths:
            findings.append(
                "较大目录："
                + "；".join(
                    f"{row['name']} {row['size']} ({row['file_count']} files)"
                    for row in large_paths[:4]
                )
                + "。"
            )
        if not flags and not missing_paths:
            findings.append("未发现关键路径缺失或磁盘告警标记。")

        recommendations = []
        if usage_percent >= 90:
            recommendations.append("优先做只读空间定位，确认大文件、日志和缓存来源。")
        elif usage_percent >= 80:
            recommendations.append("持续观察磁盘趋势，优先确认日志和构建产物增长。")
        else:
            recommendations.append("保持当前巡检频率，暂无紧急处理需求。")
        if missing_paths:
            recommendations.append("确认缺失路径是否由配置、挂载或部署模式变更导致。")
        recommendations.append("所有清理、重启、改配置动作都需要用户手动确认。")

        manual = [
            "删除文件",
            "清空日志",
            "重启服务",
            "修改配置",
            "修改权限或提权",
        ]

        return json.dumps(
            {
                "type": "ops_health_summary",
                "risk_level": risk_level,
                "generated_at": data.get("generated_at", ""),
                "project_root": data.get("project_root", ""),
                "findings": findings,
                "safe_recommendations": recommendations,
                "manual_confirmation_required": manual,
                "source_note": data.get("note", "只读采集结果。"),
            },
            ensure_ascii=False,
            indent=2,
        )

    def ops_runtime_diagnostics(event_limit: int = 200) -> str:
        """只读汇总最近运行事件、错误和本地失败投递线索。"""

        project_root = workspace_root.parent
        data_root = project_root / "data"
        events_dir = data_root / "events"
        alerts_dir = data_root / "alerts"
        delivery_failed_dir = data_root / "delivery-queue" / "failed"
        limit = max(20, min(int(event_limit or 200), 1000))

        event_files = sorted(events_dir.glob("runtime-events*.jsonl")) if events_dir.exists() else []
        latest_event_file = event_files[-1] if event_files else events_dir / "runtime-events.jsonl"
        events = _read_jsonl_tail(latest_event_file, limit=limit)
        error_events = [
            row
            for row in events
            if str(row.get("error") or "").strip()
            or str(row.get("status") or "").lower() in {"error", "failed", "rejected"}
            or str(row.get("type") or "").endswith(".failed")
        ]
        by_component: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for row in error_events:
            component = str(row.get("component") or "unknown")
            event_type = str(row.get("type") or "unknown")
            by_component[component] = by_component.get(component, 0) + 1
            by_type[event_type] = by_type.get(event_type, 0) + 1

        recent_errors = []
        for row in error_events[-6:]:
            recent_errors.append(
                {
                    "time": row.get("time", ""),
                    "component": row.get("component", ""),
                    "type": row.get("type", ""),
                    "status": row.get("status", ""),
                    "message": row.get("message", ""),
                    "error": row.get("error", ""),
                    "channel": row.get("channel", ""),
                    "agent_id": row.get("agent_id", ""),
                    "correlation_id": row.get("correlation_id", ""),
                }
            )

        alert_files = sorted(alerts_dir.glob("*.jsonl")) if alerts_dir.exists() else []
        alert_rows: list[dict[str, object]] = []
        for path in alert_files[-3:]:
            alert_rows.extend(_read_jsonl_tail(path, limit=20))
        failed_delivery_count = (
            len([path for path in delivery_failed_dir.glob("*.json") if path.is_file()])
            if delivery_failed_dir.exists()
            else 0
        )

        risk_level = "normal"
        if failed_delivery_count > 0 or any(
            str(row.get("status") or "").lower() in {"failed", "error"} for row in error_events
        ):
            risk_level = "warning"
        if len(error_events) >= 10 or failed_delivery_count >= 10:
            risk_level = "critical"

        findings = [
            f"最近扫描事件 {len(events)} 条，识别错误/拒绝/失败事件 {len(error_events)} 条。",
            f"本地失败投递文件 {failed_delivery_count} 个。",
        ]
        if by_component:
            findings.append(
                "错误模块分布："
                + "；".join(f"{name}={count}" for name, count in sorted(by_component.items()))
                + "。"
            )
        if alert_rows:
            findings.append(f"最近告警历史样本 {len(alert_rows)} 条。")

        recommendations = []
        if failed_delivery_count:
            recommendations.append("先查看失败投递详情，确认是通道、权限、网络还是消息格式问题。")
        if by_component.get("feishu"):
            recommendations.append("飞书拒绝事件优先检查回调路径、请求方法、验签和加密配置。")
        if by_component.get("delivery"):
            recommendations.append("投递错误优先检查通道 token、receive_id、队列积压和重试状态。")
        if by_component.get("agent_loop") or by_component.get("task_worker"):
            recommendations.append("执行错误优先检查模型返回、工具调用闭环、session 历史和 worker 日志。")
        if not recommendations:
            recommendations.append("未发现明确运行错误，保持事件流和告警巡检即可。")
        recommendations.append("该诊断只读，不会清理队列、重启服务或修改配置。")

        return json.dumps(
            {
                "type": "ops_runtime_diagnostics",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "risk_level": risk_level,
                "event_file": str(latest_event_file),
                "event_count": len(events),
                "error_event_count": len(error_events),
                "failed_delivery_count": failed_delivery_count,
                "error_by_component": by_component,
                "error_by_type": by_type,
                "recent_errors": recent_errors,
                "alert_sample_count": len(alert_rows),
                "findings": findings,
                "safe_recommendations": recommendations,
                "manual_confirmation_required": [
                    "清空失败投递",
                    "重启服务",
                    "修改通道配置",
                    "删除事件或告警历史",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    def ops_troubleshooting_plan(
        health_summary_json: str = "",
        runtime_diagnostics_json: str = "",
        focus: str = "",
    ) -> str:
        """把健康摘要和运行诊断整理成只读排障行动清单。"""

        health: dict[str, Any] = {}
        runtime: dict[str, Any] = {}
        if health_summary_json.strip():
            parsed_health = json.loads(health_summary_json)
            if not isinstance(parsed_health, dict):
                return "Error: health_summary_json must be a JSON object"
            health = parsed_health
        if runtime_diagnostics_json.strip():
            parsed_runtime = json.loads(runtime_diagnostics_json)
            if not isinstance(parsed_runtime, dict):
                return "Error: runtime_diagnostics_json must be a JSON object"
            runtime = parsed_runtime

        health_risk = str(health.get("risk_level") or "unknown").lower()
        runtime_risk = str(runtime.get("risk_level") or "unknown").lower()
        risk_order = {"critical": 3, "warning": 2, "normal": 1, "unknown": 0}
        overall_risk = max([health_risk, runtime_risk], key=lambda item: risk_order.get(item, 0))
        if overall_risk == "unknown":
            overall_risk = "normal"

        findings = []
        findings.extend(_clean_strings(health.get("findings") if isinstance(health.get("findings"), list) else []))
        findings.extend(_clean_strings(runtime.get("findings") if isinstance(runtime.get("findings"), list) else []))
        recommendations = []
        recommendations.extend(
            _clean_strings(health.get("safe_recommendations") if isinstance(health.get("safe_recommendations"), list) else [])
        )
        recommendations.extend(
            _clean_strings(runtime.get("safe_recommendations") if isinstance(runtime.get("safe_recommendations"), list) else [])
        )
        manual = []
        manual.extend(
            _clean_strings(
                health.get("manual_confirmation_required")
                if isinstance(health.get("manual_confirmation_required"), list)
                else []
            )
        )
        manual.extend(
            _clean_strings(
                runtime.get("manual_confirmation_required")
                if isinstance(runtime.get("manual_confirmation_required"), list)
                else []
            )
        )
        manual = list(dict.fromkeys(manual))

        error_by_component = runtime.get("error_by_component") if isinstance(runtime.get("error_by_component"), dict) else {}
        failed_delivery_count = int(runtime.get("failed_delivery_count") or 0)
        steps = []
        if health_risk in {"critical", "warning"}:
            steps.append(
                {
                    "priority": "P0" if health_risk == "critical" else "P1",
                    "area": "磁盘与关键路径",
                    "reason": "健康摘要显示磁盘或关键路径存在风险。",
                    "safe_check": "先复核 ops_readonly_health 输出中的 disk、paths 和较大目录。",
                    "do_not_auto": ["删除文件", "清空日志", "修改挂载或权限"],
                }
            )
        if failed_delivery_count:
            steps.append(
                {
                    "priority": "P0" if failed_delivery_count >= 10 else "P1",
                    "area": "可靠投递",
                    "reason": f"失败投递文件数量为 {failed_delivery_count}。",
                    "safe_check": "只读查看失败投递样本，确认通道、receive_id、token、网络或消息格式问题。",
                    "do_not_auto": ["清空失败投递", "重放消息", "修改通道配置"],
                }
            )
        for component, count in sorted(error_by_component.items(), key=lambda item: str(item[0])):
            component_name = str(component)
            if component_name == "feishu":
                area = "飞书接入"
                safe_check = "检查回调路径、请求方法、验签、加密 key 和机器人可见范围。"
            elif component_name == "delivery":
                area = "出站投递"
                safe_check = "检查通道 token、receive_id、队列积压和重试状态。"
            elif component_name in {"agent_loop", "task_worker"}:
                area = "Agent 执行"
                safe_check = "检查模型错误、工具调用闭环、session 历史和 worker 日志。"
            else:
                area = component_name or "未知模块"
                safe_check = "按 runtime event 的 type、correlation_id 和 error 字段定位上下游。"
            steps.append(
                {
                    "priority": "P1" if int(count or 0) >= 3 else "P2",
                    "area": area,
                    "reason": f"最近错误中 {component_name} 出现 {count} 次。",
                    "safe_check": safe_check,
                    "do_not_auto": ["重启服务", "清空事件", "修改配置"],
                }
            )
        if not steps:
            steps.append(
                {
                    "priority": "P3",
                    "area": "例行观察",
                    "reason": "当前健康摘要和运行诊断没有明显高风险信号。",
                    "safe_check": "保持事件流、告警历史、失败投递和磁盘趋势的定期巡检。",
                    "do_not_auto": ["无用户确认时执行清理或重启"],
                }
            )

        safe_commands = [
            "agent-gateway doctor",
            "docker compose ps",
            "docker compose logs --tail=200 gateway",
            "df -h",
        ]
        if focus.strip():
            safe_commands.insert(0, f"优先围绕「{focus.strip()}」复核相关事件和日志。")

        return json.dumps(
            {
                "type": "ops_troubleshooting_plan",
                "risk_level": overall_risk,
                "focus": focus.strip(),
                "findings": findings[:8],
                "ordered_steps": steps[:8],
                "safe_recommendations": list(dict.fromkeys(recommendations))[:8],
                "safe_readonly_commands": safe_commands,
                "manual_confirmation_required": manual
                or ["删除文件", "清空日志", "重启服务", "修改配置", "修改权限或提权"],
                "note": "这是只读排障行动清单，不会自动执行清理、重启、重放、删除或改配置。",
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

    def compose_research_brief(
        topic: str,
        conclusion: str,
        sources: list[dict[str, str]] | None = None,
        uncertainty: list[str] | None = None,
        reusable_summary: str = "",
        freshness: str = "",
        next_steps: list[str] | None = None,
    ) -> str:
        """把联网调研结果整理成结构化简报。"""

        normalized_sources = []
        for source in sources or []:
            title = str(source.get("title", "")).strip()
            url = str(source.get("url", "")).strip()
            fact = str(source.get("fact", "") or source.get("evidence", "")).strip()
            if title or url or fact:
                normalized_sources.append({"title": title, "url": url, "fact": fact})
        gaps = _clean_strings(uncertainty)
        steps = _clean_strings(next_steps)
        evidence_level = "strong" if len(normalized_sources) >= 2 and not gaps else "limited"
        if not normalized_sources:
            evidence_level = "missing"
            gaps.append("缺少可核验来源 URL。")
        if not conclusion.strip():
            evidence_level = "missing"
            gaps.append("缺少明确结论。")
        return json.dumps(
            {
                "type": "research_brief",
                "topic": topic.strip(),
                "conclusion": conclusion.strip(),
                "freshness": freshness.strip(),
                "evidence_level": evidence_level,
                "sources": normalized_sources,
                "uncertainty": gaps,
                "reusable_summary": reusable_summary.strip() or conclusion.strip(),
                "next_steps": steps or ["如需复用，请用 memory_write 保存摘要、来源 URL 和检索日期。"],
            },
            ensure_ascii=False,
            indent=2,
        )

    def assess_research_confidence(
        topic: str,
        conclusion: str,
        sources: list[dict[str, str]] | None = None,
        uncertainty: list[str] | None = None,
        source_conflicts: list[str] | None = None,
        time_sensitive: bool = False,
    ) -> str:
        """评估调研来源质量、结论置信度和后续验证动作。"""

        normalized_sources = []
        score = 0
        for source in sources or []:
            title = str(source.get("title", "")).strip()
            url = str(source.get("url", "")).strip()
            source_type = str(source.get("source_type", "") or source.get("type", "")).strip().lower()
            fact = str(source.get("fact", "") or source.get("evidence", "")).strip()
            quality = "unknown"
            if source_type in {"official", "docs", "paper", "primary", "官方", "论文"}:
                quality = "high"
                score += 25
            elif source_type in {"news", "reputable", "secondary", "媒体"}:
                quality = "medium"
                score += 15
            elif source_type in {"blog", "forum", "social", "community", "博客", "社区"}:
                quality = "low"
                score += 8
            elif url:
                quality = "medium"
                score += 12
            if fact:
                score += 5
            if title or url or fact:
                normalized_sources.append(
                    {
                        "title": title,
                        "url": url,
                        "source_type": source_type or "unknown",
                        "quality": quality,
                        "fact": fact,
                    }
                )

        gaps = _clean_strings(uncertainty)
        conflicts = _clean_strings(source_conflicts)
        if len(normalized_sources) >= 2:
            score += 15
        if len(normalized_sources) >= 3:
            score += 10
        if not conclusion.strip():
            score -= 30
            gaps.append("缺少明确结论。")
        if not normalized_sources:
            score = 0
            gaps.append("缺少可核验来源。")
        if conflicts:
            score -= 25
        if time_sensitive and len(normalized_sources) < 2:
            score -= 15
            gaps.append("时效敏感问题需要至少两个近期来源交叉验证。")

        score = max(0, min(score, 100))
        if score >= 75:
            confidence = "high"
        elif score >= 45:
            confidence = "medium"
        elif score > 0:
            confidence = "low"
        else:
            confidence = "missing"

        actions = []
        if not normalized_sources:
            actions.append("补充至少一个可核验来源 URL。")
        if time_sensitive:
            actions.append("确认来源发布时间或最后更新时间。")
        if conflicts:
            actions.append("对冲突来源做逐条比对，并优先采用一手来源。")
        if gaps:
            actions.append("补齐不确定点对应的证据。")
        if not actions:
            actions.append("可以基于当前证据形成可复用摘要。")

        return json.dumps(
            {
                "type": "research_confidence_assessment",
                "topic": topic.strip(),
                "conclusion": conclusion.strip(),
                "confidence": confidence,
                "confidence_score": score,
                "source_count": len(normalized_sources),
                "sources": normalized_sources,
                "uncertainty": gaps,
                "source_conflicts": conflicts,
                "time_sensitive": bool(time_sensitive),
                "recommended_next_actions": actions[:6],
            },
            ensure_ascii=False,
            indent=2,
        )

    def compose_research_evidence_pack(
        topic: str,
        research_question: str,
        conclusion: str,
        sources: list[dict[str, str]] | None = None,
        key_facts: list[str] | None = None,
        source_conflicts: list[str] | None = None,
        uncertainty: list[str] | None = None,
        freshness: str = "",
        downstream_use: str = "",
    ) -> str:
        """把核验后的来源整理成可交给下游 Agent 的证据包。"""

        normalized_sources = []
        primary_count = 0
        for source in sources or []:
            title = str(source.get("title", "")).strip()
            url = str(source.get("url", "")).strip()
            source_type = str(source.get("source_type", "") or source.get("type", "")).strip().lower()
            fact = str(source.get("fact", "") or source.get("evidence", "")).strip()
            if source_type in {"official", "docs", "paper", "primary", "官方", "论文"}:
                primary_count += 1
            if title or url or fact:
                normalized_sources.append(
                    {
                        "title": title,
                        "url": url,
                        "source_type": source_type or "unknown",
                        "fact": fact,
                    }
                )

        facts = _clean_strings(key_facts)
        conflicts = _clean_strings(source_conflicts)
        gaps = _clean_strings(uncertainty)
        if not normalized_sources:
            gaps.append("缺少可核验来源 URL。")
        if not conclusion.strip():
            gaps.append("缺少明确结论。")

        evidence_quality = "strong"
        if not normalized_sources or not conclusion.strip():
            evidence_quality = "missing"
        elif conflicts or gaps:
            evidence_quality = "limited"
        elif len(normalized_sources) < 2 or primary_count == 0:
            evidence_quality = "medium"

        reusable_payload = {
            "topic": topic.strip(),
            "question": research_question.strip(),
            "conclusion": conclusion.strip(),
            "key_facts": facts,
            "sources": normalized_sources,
            "freshness": freshness.strip(),
        }
        next_actions = []
        if evidence_quality in {"missing", "limited"}:
            next_actions.append("补充一手来源或交叉来源后再沉淀为长期结论。")
        if conflicts:
            next_actions.append("对冲突来源做逐条比对，并标注采用依据。")
        if not next_actions:
            next_actions.append("可把 reusable_payload 交给 repo-analyzer、planner 或 doc-writer 复用。")

        return json.dumps(
            {
                "type": "research_evidence_pack",
                "topic": topic.strip(),
                "research_question": research_question.strip(),
                "conclusion": conclusion.strip(),
                "evidence_quality": evidence_quality,
                "source_count": len(normalized_sources),
                "primary_source_count": primary_count,
                "sources": normalized_sources,
                "key_facts": facts,
                "source_conflicts": conflicts,
                "uncertainty": gaps,
                "freshness": freshness.strip(),
                "downstream_use": downstream_use.strip()
                or "供 repo-analyzer、planner、reviewer 或 doc-writer 复用。",
                "reusable_payload": reusable_payload,
                "next_actions": next_actions,
            },
            ensure_ascii=False,
            indent=2,
        )

    def compose_research_option_comparison(
        topic: str,
        decision_question: str,
        options: list[dict[str, Any]] | None = None,
        criteria: list[str] | None = None,
        sources: list[dict[str, str]] | None = None,
        recommendation: str = "",
        constraints: list[str] | None = None,
        uncertainty: list[str] | None = None,
        freshness: str = "",
    ) -> str:
        """把调研证据整理成多方案选型对比，供 planner/reviewer/doc-writer 复用。"""

        criteria_clean = _clean_strings(criteria)
        constraints_clean = _clean_strings(constraints)
        uncertainty_clean = _clean_strings(uncertainty)
        normalized_sources = []
        primary_count = 0
        for source in sources or []:
            title = str(source.get("title", "")).strip()
            url = str(source.get("url", "")).strip()
            source_type = str(source.get("source_type", "") or source.get("type", "")).strip().lower()
            fact = str(source.get("fact", "") or source.get("evidence", "")).strip()
            if source_type in {"official", "docs", "paper", "primary", "官方", "论文"}:
                primary_count += 1
            if title or url or fact:
                normalized_sources.append(
                    {
                        "title": title,
                        "url": url,
                        "source_type": source_type or "unknown",
                        "fact": fact,
                    }
                )

        normalized_options = []
        for option in options or []:
            name = str(option.get("name", "")).strip()
            if not name:
                continue
            strengths = _clean_strings(option.get("strengths") if isinstance(option.get("strengths"), list) else [])
            weaknesses = _clean_strings(option.get("weaknesses") if isinstance(option.get("weaknesses"), list) else [])
            best_for = _clean_strings(option.get("best_for") if isinstance(option.get("best_for"), list) else [])
            avoid_when = _clean_strings(option.get("avoid_when") if isinstance(option.get("avoid_when"), list) else [])
            evidence = _clean_strings(option.get("evidence") if isinstance(option.get("evidence"), list) else [])
            try:
                score = int(option.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            normalized_options.append(
                {
                    "name": name,
                    "score": max(0, min(score, 100)),
                    "strengths": strengths,
                    "weaknesses": weaknesses,
                    "best_for": best_for,
                    "avoid_when": avoid_when,
                    "evidence": evidence,
                }
            )

        if normalized_options:
            winner = max(normalized_options, key=lambda item: item["score"])
            recommended_option = recommendation.strip() or str(winner["name"])
        else:
            recommended_option = recommendation.strip()
            uncertainty_clean.append("缺少候选方案。")
        if not normalized_sources:
            uncertainty_clean.append("缺少可核验来源 URL。")
        if not criteria_clean:
            uncertainty_clean.append("缺少评价维度。")
        evidence_quality = "strong"
        if not normalized_options or not normalized_sources:
            evidence_quality = "missing"
        elif uncertainty_clean:
            evidence_quality = "limited"
        elif len(normalized_sources) < 2 or primary_count == 0:
            evidence_quality = "medium"

        next_actions = []
        if evidence_quality in {"missing", "limited"}:
            next_actions.append("补充候选方案、评价维度和一手来源后再进入最终选型。")
        if recommended_option:
            next_actions.append(f"把推荐方案「{recommended_option}」交给 planner 拆成验证计划。")
        next_actions.append("将主要风险交给 reviewer 做门禁审查。")

        return json.dumps(
            {
                "type": "research_option_comparison",
                "topic": topic.strip(),
                "decision_question": decision_question.strip(),
                "criteria": criteria_clean,
                "constraints": constraints_clean,
                "recommended_option": recommended_option,
                "evidence_quality": evidence_quality,
                "source_count": len(normalized_sources),
                "primary_source_count": primary_count,
                "options": normalized_options,
                "sources": normalized_sources,
                "uncertainty": list(dict.fromkeys(uncertainty_clean)),
                "freshness": freshness.strip(),
                "next_actions": list(dict.fromkeys(next_actions))[:6],
                "downstream_use": "供 planner 拆验证计划、reviewer 做门禁审查、doc-writer 生成选型报告。",
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
            name="structure_task_breakdown",
            description=(
                "Normalize a planning draft into phases, outputs, acceptance "
                "criteria, gaps, readiness, and next steps."
            ),
            input_schema={
                "type": "object",
                "required": ["goal"],
                "properties": {
                    "goal": {"type": "string"},
                    "scope": {"type": "string"},
                    "current_state": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
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
                },
            },
            handler=structure_task_breakdown,
            tags=("plan", "structure", "task"),
        )
    )
    registry.register(
        RegisteredTool(
            name="plan_execution_stage",
            description=(
                "Create a focused engineering execution-stage plan with objective, "
                "scope, dependencies, risks, acceptance checks, commit strategy, "
                "readiness, and next actions."
            ),
            input_schema={
                "type": "object",
                "required": ["objective"],
                "properties": {
                    "objective": {"type": "string"},
                    "current_state": {"type": "string"},
                    "scope": {"type": "string"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "acceptance_checks": {"type": "array", "items": {"type": "string"}},
                    "commit_strategy": {"type": "string"},
                    "next_actions": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=plan_execution_stage,
            tags=("plan", "execution", "engineering"),
        )
    )
    registry.register(
        RegisteredTool(
            name="adapt_adoption_plan_to_task_plan",
            description=(
                "Convert a github_repo_adoption_plan JSON from repo-analyzer into "
                "a planner task plan draft with phases, risks, next steps, and save_task_plan args."
            ),
            input_schema={
                "type": "object",
                "required": ["adoption_plan_json"],
                "properties": {
                    "adoption_plan_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_github_repo_adoption.",
                    },
                    "title": {"type": "string"},
                    "scope": {"type": "string"},
                },
            },
            handler=adapt_adoption_plan_to_task_plan,
            tags=("plan", "repository", "adoption"),
        )
    )
    registry.register(
        RegisteredTool(
            name="compose_repo_review_task_plan",
            description=(
                "Compose a planner task plan from github_repo_analysis, optional "
                "github_repo_risk_gate_review, and optional github_repo_adoption_plan. "
                "Produces phases, risks, acceptance checks, next steps, and save_task_plan args."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_analysis_json"],
                "properties": {
                    "repo_analysis_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_github_repo_analysis.",
                    },
                    "risk_gate_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by review_github_repo_risk_gate.",
                    },
                    "adoption_plan_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by plan_github_repo_adoption.",
                    },
                    "title": {"type": "string"},
                    "scope": {"type": "string"},
                },
            },
            handler=compose_repo_review_task_plan,
            tags=("plan", "repository", "review", "adoption"),
        )
    )
    registry.register(
        RegisteredTool(
            name="compose_research_option_validation_plan",
            description=(
                "Compose a planner validation task plan from a research_option_comparison "
                "and optional research_option_comparison_gate_review. Produces phases, "
                "risks, next steps, decision, and save_task_plan args."
            ),
            input_schema={
                "type": "object",
                "required": ["comparison_json"],
                "properties": {
                    "comparison_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_option_comparison.",
                    },
                    "gate_review_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by review_research_option_comparison_gate.",
                    },
                    "title": {"type": "string"},
                    "scope": {"type": "string"},
                },
            },
            handler=compose_research_option_validation_plan,
            tags=("plan", "research", "comparison", "validation"),
        )
    )
    registry.register(
        RegisteredTool(
            name="adapt_collaboration_plan_to_task_plan",
            description=(
                "Convert an agent_collaboration_plan JSON from an entry agent into "
                "a planner task plan draft with staged handoffs, risks, next steps, "
                "and save_task_plan args."
            ),
            input_schema={
                "type": "object",
                "required": ["collaboration_json"],
                "properties": {
                    "collaboration_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_agent_collaboration.",
                    },
                    "title": {"type": "string"},
                    "scope": {"type": "string"},
                },
            },
            handler=adapt_collaboration_plan_to_task_plan,
            tags=("plan", "agent", "collaboration"),
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
            name="review_release_gate",
            description=(
                "Create a pre-release risk gate review with checklist, go/"
                "conditional-go/no-go decision, risks, evidence, and next actions."
            ),
            input_schema={
                "type": "object",
                "required": ["change_summary"],
                "properties": {
                    "change_summary": {"type": "string"},
                    "risk_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {"type": "string"},
                                "issue": {"type": "string"},
                                "status": {"type": "string"},
                                "state": {"type": "string"},
                                "mitigation": {"type": "string"},
                                "suggestion": {"type": "string"},
                            },
                        },
                    },
                    "test_evidence": {"type": "array", "items": {"type": "string"}},
                    "unresolved_items": {"type": "array", "items": {"type": "string"}},
                    "rollback_plan": {"type": "string"},
                },
            },
            handler=review_release_gate,
            tags=("review", "release", "gate", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="review_task_plan_gate",
            description=(
                "Review whether a task plan is ready to enter execution: goal, scope, "
                "phases, acceptance criteria, risks, evidence, and go/conditional-go/no-go decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "plan_json": {
                        "type": "string",
                        "description": "JSON string from structure_task_breakdown, adapt_adoption_plan_to_task_plan, or a compatible task plan.",
                    },
                    "review_target": {"type": "string"},
                    "required_evidence": {"type": "array", "items": {"type": "string"}},
                    "known_risks": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=review_task_plan_gate,
            tags=("review", "plan", "gate", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="review_agent_collaboration_gate",
            description=(
                "Review whether an agent_collaboration_plan is safe and complete enough "
                "for staged handoff: goal, route, input contracts, outputs, constraints, "
                "and explicit non-execution statement."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "collaboration_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_agent_collaboration.",
                    },
                    "review_target": {"type": "string"},
                    "known_risks": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=review_agent_collaboration_gate,
            tags=("review", "agent", "collaboration", "gate", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="review_research_evidence_gate",
            description=(
                "Review whether a research_evidence_pack is reliable enough for "
                "downstream reuse: question, conclusion, source count, URLs, primary "
                "sources, key facts, uncertainty, freshness, and go/conditional-go/no-go decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "evidence_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_evidence_pack.",
                    },
                    "review_target": {"type": "string"},
                    "min_sources": {"type": "integer"},
                    "require_primary_source": {"type": "boolean"},
                    "time_sensitive": {"type": "boolean"},
                },
            },
            handler=review_research_evidence_gate,
            tags=("review", "research", "evidence", "gate", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="review_research_option_comparison_gate",
            description=(
                "Review whether a research_option_comparison is complete enough for "
                "planning or documentation: decision question, options, criteria, "
                "sources, primary sources, recommendation, uncertainty, and "
                "go/conditional-go/no-go decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "comparison_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_option_comparison.",
                    },
                    "review_target": {"type": "string"},
                    "min_options": {"type": "integer"},
                    "min_sources": {"type": "integer"},
                    "require_primary_source": {"type": "boolean"},
                    "require_recommendation": {"type": "boolean"},
                },
            },
            handler=review_research_option_comparison_gate,
            tags=("review", "research", "comparison", "gate", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="review_github_repo_risk_gate",
            description=(
                "Review whether a github_repo_risk_scan is acceptable for repository "
                "adoption or reuse: license, maintenance, high-risk blockers, "
                "mitigations, and go/conditional-go/no-go decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "risk_scan_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_risk_scan.",
                    },
                    "review_target": {"type": "string"},
                    "intended_action": {
                        "type": "string",
                        "description": "Fallback intended use if the scan does not include intended_use.",
                    },
                    "require_license_clear": {
                        "type": "boolean",
                        "description": "Whether unknown or blocked license risk should fail the gate.",
                    },
                },
            },
            handler=review_github_repo_risk_gate,
            tags=("review", "github", "repository", "gate", "risk"),
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
            name="render_repo_analysis_markdown",
            description=(
                "Render a github_repo_analysis JSON object from repo-analyzer into "
                "a formal Chinese Markdown repository analysis document."
            ),
            input_schema={
                "type": "object",
                "required": ["analysis_json"],
                "properties": {
                    "analysis_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_github_repo_analysis.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw JSON analysis metadata.",
                    },
                },
            },
            handler=render_repo_analysis_markdown,
            tags=("document", "markdown", "github", "report"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_github_repo_risk_markdown",
            description=(
                "Render a github_repo_risk_scan JSON object from repo-analyzer into "
                "a formal Chinese Markdown repository risk scan document."
            ),
            input_schema={
                "type": "object",
                "required": ["risk_scan_json"],
                "properties": {
                    "risk_scan_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_risk_scan.",
                    },
                    "gate_review_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by review_github_repo_risk_gate.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw risk scan metadata.",
                    },
                },
            },
            handler=render_github_repo_risk_markdown,
            tags=("document", "markdown", "github", "risk"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_research_evidence_markdown",
            description=(
                "Render a research_evidence_pack JSON object from research into "
                "a formal Chinese Markdown research evidence document."
            ),
            input_schema={
                "type": "object",
                "required": ["evidence_json"],
                "properties": {
                    "evidence_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_evidence_pack.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw evidence metadata.",
                    },
                },
            },
            handler=render_research_evidence_markdown,
            tags=("document", "markdown", "research", "evidence"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_research_option_comparison_markdown",
            description=(
                "Render a research_option_comparison JSON object from research into "
                "a formal Chinese Markdown option comparison or technology selection report."
            ),
            input_schema={
                "type": "object",
                "required": ["comparison_json"],
                "properties": {
                    "comparison_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_option_comparison.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw comparison metadata.",
                    },
                },
            },
            handler=render_research_option_comparison_markdown,
            tags=("document", "markdown", "research", "comparison"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_execution_record_markdown",
            description=(
                "Render planner task-plan JSON and optional reviewer gate-review JSON "
                "into a formal Chinese Markdown execution record."
            ),
            input_schema={
                "type": "object",
                "required": ["task_plan_json"],
                "properties": {
                    "task_plan_json": {
                        "type": "string",
                        "description": "JSON string from adapt_adoption_plan_to_task_plan, structure_task_breakdown, or a compatible task plan.",
                    },
                    "gate_review_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by review_task_plan_gate.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append raw plan and review JSON metadata.",
                    },
                },
            },
            handler=render_execution_record_markdown,
            tags=("document", "markdown", "plan", "review"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_research_option_validation_plan_markdown",
            description=(
                "Render a task_plan_from_research_option_comparison JSON object from "
                "planner into a formal Chinese Markdown validation plan."
            ),
            input_schema={
                "type": "object",
                "required": ["task_plan_json"],
                "properties": {
                    "task_plan_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_research_option_validation_plan.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw validation plan metadata.",
                    },
                },
            },
            handler=render_research_option_validation_plan_markdown,
            tags=("document", "markdown", "research", "plan", "validation"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_agent_collaboration_markdown",
            description=(
                "Render an agent_collaboration_plan JSON object from an entry agent "
                "into a formal Chinese Markdown collaboration plan."
            ),
            input_schema={
                "type": "object",
                "required": ["collaboration_json"],
                "properties": {
                    "collaboration_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_agent_collaboration.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw collaboration plan metadata.",
                    },
                },
            },
            handler=render_agent_collaboration_markdown,
            tags=("document", "markdown", "agent", "collaboration"),
        )
    )
    registry.register(
        RegisteredTool(
            name="render_agent_collaboration_progress_markdown",
            description=(
                "Render an agent_collaboration_progress JSON object into a formal "
                "Chinese Markdown collaboration progress report."
            ),
            input_schema={
                "type": "object",
                "required": ["progress_json"],
                "properties": {
                    "progress_json": {
                        "type": "string",
                        "description": "JSON string returned by summarize_collaboration_progress.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional Markdown H1 title.",
                    },
                    "include_raw_metadata": {
                        "type": "boolean",
                        "description": "Whether to append the raw progress metadata.",
                    },
                },
            },
            handler=render_agent_collaboration_progress_markdown,
            tags=("document", "markdown", "agent", "collaboration", "progress"),
        )
    )
    registry.register(
        RegisteredTool(
            name="outline_structured_document",
            description=(
                "Create a writing outline and material gap check before saving "
                "a structured document."
            ),
            input_schema={
                "type": "object",
                "required": ["title", "document_type"],
                "properties": {
                    "title": {"type": "string"},
                    "document_type": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "source_material_summary": {"type": "string"},
                    "required_sections": {"type": "array", "items": {"type": "string"}},
                    "missing_materials": {"type": "array", "items": {"type": "string"}},
                    "tone": {"type": "string"},
                },
            },
            handler=outline_structured_document,
            tags=("document", "outline", "write-planning"),
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
            name="build_agent_handoff_prompt",
            description=(
                "Build a standardized handoff prompt from an entry agent to a "
                "specialized capability or personal agent."
            ),
            input_schema={
                "type": "object",
                "required": ["user_goal", "target_agent_id", "context_summary"],
                "properties": {
                    "user_goal": {
                        "type": "string",
                        "description": "Original user goal or request.",
                    },
                    "target_agent_id": {
                        "type": "string",
                        "description": "Target agent id that should receive the handoff.",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Relevant context already known to the entry agent.",
                    },
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "expected_output": {"type": "string"},
                    "source_platform": {"type": "string"},
                    "should_persist": {"type": "boolean"},
                    "known_inputs": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=build_agent_handoff_prompt,
            tags=("agent", "delegation", "handoff"),
        )
    )
    registry.register(
        RegisteredTool(
            name="build_collaboration_stage_handoff",
            description=(
                "Build a standardized handoff prompt for one stage inside an "
                "agent_collaboration_plan. This does not execute the target agent."
            ),
            input_schema={
                "type": "object",
                "required": ["collaboration_plan_json", "stage"],
                "properties": {
                    "collaboration_plan_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_agent_collaboration.",
                    },
                    "stage": {
                        "type": "integer",
                        "description": "1-based stage number to prepare handoff for.",
                    },
                    "upstream_result_summary": {
                        "type": "string",
                        "description": "Short summary of the previous stage output.",
                    },
                    "upstream_result_json": {
                        "type": "string",
                        "description": "Optional raw JSON output from the previous stage.",
                    },
                    "additional_context": {"type": "string"},
                },
            },
            handler=build_collaboration_stage_handoff,
            tags=("agent", "collaboration", "handoff"),
        )
    )
    registry.register(
        RegisteredTool(
            name="summarize_collaboration_progress",
            description=(
                "Summarize progress for an agent_collaboration_plan and return the "
                "next stage plus ready-to-use build_collaboration_stage_handoff args."
            ),
            input_schema={
                "type": "object",
                "required": ["collaboration_plan_json"],
                "properties": {
                    "collaboration_plan_json": {
                        "type": "string",
                        "description": "JSON string returned by plan_agent_collaboration.",
                    },
                    "completed_stage_outputs": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Completed stage summaries with step/stage and summary/output payload.",
                    },
                    "current_stage": {
                        "type": "integer",
                        "description": "Optional 1-based current stage; completed count is current_stage - 1.",
                    },
                },
            },
            handler=summarize_collaboration_progress,
            tags=("agent", "collaboration", "progress", "handoff"),
        )
    )
    registry.register(
        RegisteredTool(
            name="list_agent_collaboration_routes",
            description=(
                "List available multi-agent collaboration route templates, aliases, "
                "agent sequences, and optional stage details. This does not execute agents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional task types or aliases to filter, for example repo-adoption or research-option-validation.",
                    },
                    "include_stages": {
                        "type": "boolean",
                        "description": "Whether to include per-stage purpose and expected output.",
                    },
                },
            },
            handler=list_agent_collaboration_routes,
            tags=("agent", "collaboration", "routing", "catalog"),
        )
    )
    registry.register(
        RegisteredTool(
            name="plan_agent_collaboration",
            description=(
                "Plan a structured multi-agent collaboration route for complex tasks. "
                "This only returns the handoff sequence and does not execute agents."
            ),
            input_schema={
                "type": "object",
                "required": ["user_goal"],
                "properties": {
                    "user_goal": {"type": "string"},
                    "task_type": {
                        "type": "string",
                        "description": "repo-adoption/research-document/research-option-validation/plan-review-document/ops-diagnosis or common aliases.",
                    },
                    "preferred_agents": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "expected_output": {"type": "string"},
                    "should_persist": {"type": "boolean"},
                },
            },
            handler=plan_agent_collaboration,
            tags=("agent", "collaboration", "routing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="classify_task_intent",
            description=(
                "Classify a user request into chat, research, planning, document, "
                "review, repo-analysis, repo-adoption, personal, diet, ops, or unknown; "
                "recommend the best configured agent and indicate whether a multi-agent "
                "collaboration route is needed. This does not execute handoff."
            ),
            input_schema={
                "type": "object",
                "required": ["user_text"],
                "properties": {
                    "user_text": {
                        "type": "string",
                        "description": "Original user request to classify.",
                    },
                    "context_hint": {
                        "type": "string",
                        "description": "Optional channel/session context that may help routing.",
                    },
                },
            },
            handler=classify_task_intent,
            tags=("agent", "routing", "classification"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_entry_response",
            description=(
                "Format an entry agent response after task classification and optional "
                "delegation suggestion into stable Chinese Markdown."
            ),
            input_schema={
                "type": "object",
                "required": ["intent", "recommended_agent_id", "reason", "current_reply"],
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Task intent from classify_task_intent or equivalent reasoning.",
                    },
                    "recommended_agent_id": {
                        "type": "string",
                        "description": "Recommended agent id, or main when answering directly.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this agent should handle the task.",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Short handoff summary for the target agent.",
                    },
                    "handoff_prompt": {
                        "type": "string",
                        "description": "Optional ready-to-send handoff prompt.",
                    },
                    "requires_collaboration": {
                        "type": "boolean",
                        "description": "Whether this response should explain a multi-agent route.",
                    },
                    "collaboration_task_type": {
                        "type": "string",
                        "description": "Task type passed to plan_agent_collaboration, for example repo-adoption.",
                    },
                    "collaboration_plan_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by plan_agent_collaboration.",
                    },
                    "current_reply": {
                        "type": "string",
                        "description": "Short answer or interim response to show to the user.",
                    },
                    "can_answer_directly": {
                        "type": "boolean",
                        "description": "Whether the current entry agent can answer directly.",
                    },
                },
            },
            handler=format_entry_response,
            tags=("agent", "routing", "format"),
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
            name="explain_agent_route",
            description=(
                "Explain why an entry agent selected a single-agent delegation or "
                "multi-agent collaboration route. Returns route type, stages, next step, "
                "readiness, and non-execution boundary."
            ),
            input_schema={
                "type": "object",
                "required": ["user_goal", "intent"],
                "properties": {
                    "user_goal": {"type": "string"},
                    "intent": {"type": "string"},
                    "recommended_agent_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "requires_collaboration": {"type": "boolean"},
                    "collaboration_task_type": {"type": "string"},
                    "collaboration_plan_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by plan_agent_collaboration.",
                    },
                    "next_step": {"type": "string"},
                },
            },
            handler=explain_agent_route,
            tags=("agent", "routing", "explanation"),
        )
    )
    registry.register(
        RegisteredTool(
            name="prepare_entry_route_response",
            description=(
                "Prepare a full entry-agent routing response in one step: classify the "
                "request, create a collaboration plan when needed, explain the route, "
                "and format the user-facing response. This does not execute target agents."
            ),
            input_schema={
                "type": "object",
                "required": ["user_text"],
                "properties": {
                    "user_text": {"type": "string"},
                    "context_hint": {"type": "string"},
                    "source_platform": {"type": "string"},
                    "should_persist": {"type": "boolean"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "expected_output": {"type": "string"},
                },
            },
            handler=prepare_entry_route_response,
            tags=("agent", "routing", "entry", "format"),
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
            name="summarize_ops_health",
            description=(
                "Summarize ops_readonly_health JSON into risk level, findings, "
                "safe recommendations, and actions requiring manual confirmation."
            ),
            input_schema={
                "type": "object",
                "required": ["health_json"],
                "properties": {
                    "health_json": {
                        "type": "string",
                        "description": "JSON string returned by ops_readonly_health.",
                    }
                },
            },
            handler=summarize_ops_health,
            tags=("ops", "health", "summary", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="ops_runtime_diagnostics",
            description=(
                "Read recent local runtime event JSONL files and summarize errors, "
                "failed deliveries, alert samples, and safe troubleshooting steps."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_limit": {
                        "type": "integer",
                        "description": "How many recent event rows to inspect, capped at 1000.",
                    }
                },
            },
            handler=ops_runtime_diagnostics,
            tags=("ops", "events", "errors", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="ops_troubleshooting_plan",
            description=(
                "Combine ops_health_summary and ops_runtime_diagnostics JSON into "
                "a read-only ordered troubleshooting plan with safe checks and manual-confirmation actions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "health_summary_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by summarize_ops_health.",
                    },
                    "runtime_diagnostics_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by ops_runtime_diagnostics.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Optional troubleshooting focus, for example feishu, delivery, disk, worker.",
                    },
                },
            },
            handler=ops_troubleshooting_plan,
            tags=("ops", "troubleshooting", "plan", "read"),
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
            name="compose_research_brief",
            description=(
                "Compose verified research findings into a structured brief: "
                "conclusion, sources, uncertainty, reusable summary, and next steps."
            ),
            input_schema={
                "type": "object",
                "required": ["topic", "conclusion"],
                "properties": {
                    "topic": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "fact": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "uncertainty": {"type": "array", "items": {"type": "string"}},
                    "reusable_summary": {"type": "string"},
                    "freshness": {"type": "string"},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=compose_research_brief,
            tags=("research", "brief", "evidence"),
        )
    )
    registry.register(
        RegisteredTool(
            name="assess_research_confidence",
            description=(
                "Assess research source quality and conclusion confidence from "
                "sources, uncertainty, conflicts, and time-sensitivity."
            ),
            input_schema={
                "type": "object",
                "required": ["topic", "conclusion"],
                "properties": {
                    "topic": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "source_type": {
                                    "type": "string",
                                    "description": "official/docs/paper/news/blog/forum/community/etc.",
                                },
                                "type": {"type": "string"},
                                "fact": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "uncertainty": {"type": "array", "items": {"type": "string"}},
                    "source_conflicts": {"type": "array", "items": {"type": "string"}},
                    "time_sensitive": {"type": "boolean"},
                },
            },
            handler=assess_research_confidence,
            tags=("research", "confidence", "evidence"),
        )
    )
    registry.register(
        RegisteredTool(
            name="compose_research_evidence_pack",
            description=(
                "Compose verified sources into a reusable evidence pack for "
                "downstream agents such as repo-analyzer, planner, reviewer, or doc-writer."
            ),
            input_schema={
                "type": "object",
                "required": ["topic", "research_question", "conclusion"],
                "properties": {
                    "topic": {"type": "string"},
                    "research_question": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "source_type": {"type": "string"},
                                "type": {"type": "string"},
                                "fact": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "key_facts": {"type": "array", "items": {"type": "string"}},
                    "source_conflicts": {"type": "array", "items": {"type": "string"}},
                    "uncertainty": {"type": "array", "items": {"type": "string"}},
                    "freshness": {"type": "string"},
                    "downstream_use": {"type": "string"},
                },
            },
            handler=compose_research_evidence_pack,
            tags=("research", "evidence", "handoff"),
        )
    )
    registry.register(
        RegisteredTool(
            name="compose_research_option_comparison",
            description=(
                "Compose a structured option comparison from verified research evidence: "
                "criteria, candidate options, scores, sources, recommendation, uncertainty, "
                "and downstream handoff actions."
            ),
            input_schema={
                "type": "object",
                "required": ["topic", "decision_question"],
                "properties": {
                    "topic": {"type": "string"},
                    "decision_question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "score": {"type": "integer"},
                                "strengths": {"type": "array", "items": {"type": "string"}},
                                "weaknesses": {"type": "array", "items": {"type": "string"}},
                                "best_for": {"type": "array", "items": {"type": "string"}},
                                "avoid_when": {"type": "array", "items": {"type": "string"}},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "criteria": {"type": "array", "items": {"type": "string"}},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "source_type": {"type": "string"},
                                "type": {"type": "string"},
                                "fact": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "recommendation": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "uncertainty": {"type": "array", "items": {"type": "string"}},
                    "freshness": {"type": "string"},
                },
            },
            handler=compose_research_option_comparison,
            tags=("research", "comparison", "decision", "handoff"),
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
