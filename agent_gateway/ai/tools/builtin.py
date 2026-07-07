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

    def classify_task_intent(user_text: str, context_hint: str = "") -> str:
        """把用户输入归类到主入口可处理或更适合交给的专用 Agent。"""

        text = f"{user_text} {context_hint}".strip()
        normalized = text.lower()
        catalog = [
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
            },
        ]

        best = max(
            catalog,
            key=lambda row: _keyword_score(normalized, row["keywords"]),
        )
        score = _keyword_score(normalized, best["keywords"])
        if not text:
            intent = "unknown"
            agent = "main"
            confidence = 0.0
            reason = "用户输入为空，无法判断任务意图。"
            next_step = "请补充要处理的问题或目标。"
            can_answer_directly = False
        elif score <= 0:
            intent = "chat"
            agent = "main"
            confidence = 0.55
            reason = "未命中专用 Agent 的明显触发词，main 可以先直接回答。"
            next_step = "直接回答；如果后续出现复杂目标，再重新分类。"
            can_answer_directly = True
        else:
            intent = str(best["intent"])
            agent = str(best["agent"])
            confidence = min(0.95, 0.55 + score * 0.12)
            reason = str(best["reason"])
            next_step = str(best["next"])
            can_answer_directly = bool(best["direct"])

        return json.dumps(
            {
                "type": "task_intent_classification",
                "intent": intent,
                "confidence": round(confidence, 2),
                "recommended_agent_id": agent,
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
    ) -> str:
        """把入口 Agent 的分类和委派结论格式化为稳定中文回复。"""

        normalized_intent = intent.strip() or "unknown"
        agent_id = recommended_agent_id.strip() or "main"
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
            name="classify_task_intent",
            description=(
                "Classify a user request into chat, research, planning, document, "
                "review, repo-analysis, personal, diet, ops, or unknown, and "
                "recommend the best configured agent. This does not execute handoff."
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
