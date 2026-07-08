from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional during bootstrap
    httpx = None  # type: ignore[assignment]

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


_GITHUB_REPO_RE = re.compile(
    r"(?:https?://github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
)


@dataclass(slots=True)
class GitHubRepoClient:
    """GitHub 公共仓库只读客户端。"""

    base_url: str = "https://api.github.com"
    timeout_seconds: float = 15.0
    token: str = ""
    _http: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if httpx is None:
            raise RuntimeError("github repo tools require httpx")
        self.base_url = self.base_url.rstrip("/")
        self._http = httpx.Client(timeout=self.timeout_seconds, trust_env=False)

    def summarize(self, repo_url: str, *, max_tree_items: int = 80) -> dict[str, Any]:
        """读取仓库元数据、README 和目录树摘要。"""

        owner, repo = parse_github_repo(repo_url)
        repo_data = self._get(f"/repos/{owner}/{repo}")
        readme = self._get_readme(owner, repo)
        tree = self._get_tree(owner, repo, repo_data.get("default_branch") or "main", max_tree_items)
        return normalize_github_repo_summary(
            owner=owner,
            repo=repo,
            repo_data=repo_data,
            readme=readme,
            tree=tree,
        )

    def _get(self, path: str) -> dict[str, Any]:
        response = self._http.get(f"{self.base_url}{path}", headers=self._headers())
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"GitHub API returned non-object for {path}")
        return data

    def _get_readme(self, owner: str, repo: str) -> dict[str, str]:
        try:
            data = self._get(f"/repos/{owner}/{repo}/readme")
        except Exception as exc:  # pragma: no cover - exercised through tool output
            return {"name": "", "content": "", "error": str(exc)}
        raw = str(data.get("content") or "")
        encoding = str(data.get("encoding") or "")
        content = ""
        if raw and encoding == "base64":
            content = base64.b64decode(raw).decode("utf-8", errors="replace")
        return {
            "name": str(data.get("name") or "README"),
            "path": str(data.get("path") or ""),
            "content": content,
            "error": "",
        }

    def _get_tree(
        self,
        owner: str,
        repo: str,
        branch: str,
        max_tree_items: int,
    ) -> list[dict[str, Any]]:
        try:
            data = self._get(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
        except Exception:
            return []
        items = data.get("tree")
        if not isinstance(items, list):
            return []
        normalized = []
        for item in items[: max(1, min(max_tree_items, 300))]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "path": item.get("path", ""),
                    "type": item.get("type", ""),
                    "size": item.get("size"),
                }
            )
        return normalized

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-gateway-repo-analyzer",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def parse_github_repo(repo_url: str) -> tuple[str, str]:
    """从 GitHub URL 或 owner/repo 字符串解析仓库坐标。"""

    value = repo_url.strip().removesuffix(".git").strip("/")
    match = _GITHUB_REPO_RE.search(value)
    if not match:
        raise ValueError("repo_url must be a GitHub URL or owner/repo")
    return match.group("owner"), match.group("repo")


def normalize_github_repo_summary(
    *,
    owner: str,
    repo: str,
    repo_data: dict[str, Any],
    readme: dict[str, str],
    tree: list[dict[str, Any]],
) -> dict[str, Any]:
    """把 GitHub API 原始响应压缩成 Agent 易分析的结构。"""

    readme_content = readme.get("content", "")
    readme_excerpt = readme_content[:8_000]
    return {
        "repository": f"{owner}/{repo}",
        "url": repo_data.get("html_url") or f"https://github.com/{owner}/{repo}",
        "description": repo_data.get("description") or "",
        "homepage": repo_data.get("homepage") or "",
        "language": repo_data.get("language") or "",
        "topics": repo_data.get("topics") or [],
        "stars": repo_data.get("stargazers_count") or 0,
        "forks": repo_data.get("forks_count") or 0,
        "open_issues": repo_data.get("open_issues_count") or 0,
        "license": (repo_data.get("license") or {}).get("spdx_id") or "",
        "default_branch": repo_data.get("default_branch") or "",
        "pushed_at": repo_data.get("pushed_at") or "",
        "created_at": repo_data.get("created_at") or "",
        "updated_at": repo_data.get("updated_at") or "",
        "archived": bool(repo_data.get("archived")),
        "readme": {
            "name": readme.get("name", ""),
            "path": readme.get("path", ""),
            "excerpt": readme_excerpt,
            "truncated": len(readme_content) > len(readme_excerpt),
            "error": readme.get("error", ""),
        },
        "tree": tree,
        "tree_count": len(tree),
    }


def assess_gateway_repo_fit(
    repo_summary: dict[str, Any],
    focus: list[str] | None = None,
) -> dict[str, Any]:
    """评估一个仓库对 Gateway 的借鉴价值和适配优先级。"""

    focus_items = [str(item).strip().lower() for item in focus or [] if str(item).strip()]
    topics = [str(item).lower() for item in repo_summary.get("topics") or []]
    text = " ".join(
        [
            str(repo_summary.get("description", "")),
            str(repo_summary.get("language", "")),
            " ".join(topics),
            str(repo_summary.get("readme", {}).get("excerpt", ""))[:2000],
        ]
    ).lower()
    tree_paths = [str(item.get("path", "")).lower() for item in repo_summary.get("tree") or [] if isinstance(item, dict)]

    signals: list[str] = []
    score = 0
    if any(word in text for word in ("agent", "agents", "skill", "tool calling", "workflow")):
        score += 25
        signals.append("包含 Agent / Skill / Tool / Workflow 相关信号。")
    if any(word in text for word in ("gateway", "queue", "rabbitmq", "redis", "worker", "scheduler")):
        score += 20
        signals.append("包含网关、队列、Worker 或调度相关信号。")
    if any(path.endswith(("skill.md", "agents.md", "soul.md")) or "/skills/" in path for path in tree_paths):
        score += 20
        signals.append("目录结构中存在 Skill / Agent 提示词资产。")
    if repo_summary.get("stars", 0) >= 1000:
        score += 10
        signals.append("仓库关注度较高，可优先研究社区沉淀。")
    if repo_summary.get("license"):
        score += 5
        signals.append("仓库包含许可证信息，便于判断复用边界。")
    if repo_summary.get("archived"):
        score -= 25
        signals.append("仓库已归档，维护风险较高。")
    if repo_summary.get("readme", {}).get("error"):
        score -= 10
        signals.append("README 读取失败，证据不足。")
    if focus_items and any(item in text for item in focus_items):
        score += 10
        signals.append("仓库内容命中用户关注点。")

    score = max(0, min(score, 100))
    if score >= 70:
        priority = "high"
    elif score >= 40:
        priority = "medium"
    else:
        priority = "low"

    risks = []
    if repo_summary.get("archived"):
        risks.append("仓库已归档，不宜直接依赖。")
    if not repo_summary.get("license"):
        risks.append("许可证不明确，复用前需要人工确认。")
    if repo_summary.get("open_issues", 0) > 100:
        risks.append("未关闭 issue 较多，需要判断维护质量。")
    if not signals:
        risks.append("未发现与 Gateway 强相关的结构化信号，可能只适合泛读。")

    next_steps = [
        "优先阅读 README、目录结构和最近提交，确认项目边界。",
        "提取可借鉴的提示词、工具接口、调度模式或文档结构。",
    ]
    if priority == "high":
        next_steps.append("建议生成正式仓库分析报告，并列出可迁移到 Gateway 的小任务。")
    elif priority == "medium":
        next_steps.append("建议先做轻量对比，不急于纳入实现计划。")
    else:
        next_steps.append("建议仅保留为素材，不进入近期实现计划。")

    return {
        "repository": repo_summary.get("repository", ""),
        "fit_score": score,
        "priority": priority,
        "signals": signals,
        "risks": risks,
        "gateway_reuse_ideas": _gateway_reuse_ideas(text, tree_paths),
        "next_steps": next_steps,
    }


def scan_github_repo_risks(
    repo_summary: dict[str, Any],
    *,
    intended_use: str = "",
) -> dict[str, Any]:
    """基于仓库摘要生成引入前风险清单。"""

    repository = str(repo_summary.get("repository") or "").strip()
    license_id = str(repo_summary.get("license") or "").strip()
    open_issues = _as_int(repo_summary.get("open_issues"))
    stars = _as_int(repo_summary.get("stars"))
    archived = bool(repo_summary.get("archived"))
    readme = repo_summary.get("readme") if isinstance(repo_summary.get("readme"), dict) else {}
    tree_paths = [
        str(item.get("path") or "").lower()
        for item in repo_summary.get("tree") or []
        if isinstance(item, dict)
    ]

    risk_items: list[dict[str, str]] = []
    if archived:
        risk_items.append(
            {
                "severity": "high",
                "area": "maintenance",
                "issue": "仓库已归档。",
                "impact": "不适合直接作为 Gateway 运行依赖，后续安全修复和兼容性维护不可预期。",
                "mitigation": "只作为设计参考；如需采用，先寻找活跃 fork 或替代实现。",
            }
        )
    if not license_id:
        risk_items.append(
            {
                "severity": "high",
                "area": "license",
                "issue": "许可证缺失或未识别。",
                "impact": "代码、提示词或文档片段的复用边界不清晰。",
                "mitigation": "复用前人工确认 LICENSE、README 授权说明或联系作者。",
            }
        )
    if readme.get("error") or not str(readme.get("excerpt") or "").strip():
        risk_items.append(
            {
                "severity": "medium",
                "area": "evidence",
                "issue": "README 读取失败或内容不足。",
                "impact": "项目目标、使用方式和边界证据不足，容易误判仓库价值。",
                "mitigation": "补充读取 README、docs 或关键源码后再形成结论。",
            }
        )
    if open_issues >= 100:
        risk_items.append(
            {
                "severity": "medium",
                "area": "maintenance",
                "issue": f"未关闭 issue 较多：{open_issues}。",
                "impact": "可能存在维护压力、质量问题或用户反馈积压。",
                "mitigation": "抽样查看最近 issue、PR 和 release，判断是否仍在维护。",
            }
        )
    dependency_files = [
        path
        for path in tree_paths
        if path.endswith(
            (
                "requirements.txt",
                "pyproject.toml",
                "package.json",
                "pnpm-lock.yaml",
                "package-lock.json",
                "go.mod",
                "cargo.toml",
            )
        )
    ]
    if dependency_files:
        risk_items.append(
            {
                "severity": "low",
                "area": "dependencies",
                "issue": "仓库包含依赖清单：" + "、".join(dependency_files[:4]) + "。",
                "impact": "如要运行或移植，需要额外评估依赖体积、许可证和安全更新。",
                "mitigation": "先只阅读设计；运行前用隔离环境和锁定依赖做验证。",
            }
        )
    if stars < 10 and not archived:
        risk_items.append(
            {
                "severity": "low",
                "area": "adoption",
                "issue": f"社区信号较少：{stars} stars。",
                "impact": "项目可能较新或使用者较少，资料和问题反馈有限。",
                "mitigation": "降低采纳优先级，先做轻量阅读和小样本验证。",
            }
        )

    high_count = sum(1 for item in risk_items if item["severity"] == "high")
    medium_count = sum(1 for item in risk_items if item["severity"] == "medium")
    if high_count:
        decision = "hold"
        risk_level = "high"
    elif medium_count:
        decision = "conditional"
        risk_level = "medium"
    elif risk_items:
        decision = "watch"
        risk_level = "low"
    else:
        decision = "pass"
        risk_level = "low"

    next_actions = [
        "复核许可证和 README，区分可直接复用、仅可学习和不可复用内容。",
        "只把明确安全的设计思想转成 Gateway 小实验，不直接复制未知依赖。",
    ]
    if dependency_files:
        next_actions.append("如需运行仓库，先在隔离环境检查依赖和启动成本。")
    if intended_use.strip():
        next_actions.insert(0, f"围绕预期用途「{intended_use.strip()}」复核风险是否可接受。")

    return {
        "type": "github_repo_risk_scan",
        "repository": repository,
        "url": repo_summary.get("url", ""),
        "intended_use": intended_use.strip(),
        "risk_level": risk_level,
        "decision": decision,
        "risk_items": risk_items,
        "dependency_files": dependency_files[:12],
        "summary": {
            "license": license_id or "unknown",
            "archived": archived,
            "open_issues": open_issues,
            "stars": stars,
            "pushed_at": repo_summary.get("pushed_at", ""),
            "updated_at": repo_summary.get("updated_at", ""),
        },
        "next_actions": next_actions[:6],
        "note": "这是基于 github_repo_summary 的轻量风险扫描，不代表已经完成法律、安全或运行验证。",
    }


def compose_repo_analysis(
    repo_summary: dict[str, Any],
    gateway_fit: dict[str, Any] | None = None,
    *,
    analysis_goal: str = "",
    key_findings: list[str] | None = None,
    risks: list[str] | None = None,
    recommendations: list[str] | None = None,
) -> dict[str, Any]:
    """把仓库摘要和 Gateway fit 评估组合成稳定的仓库分析结论。"""

    fit = gateway_fit or assess_gateway_repo_fit(repo_summary)
    finding_items = [str(item).strip() for item in key_findings or [] if str(item).strip()]
    risk_items = [str(item).strip() for item in risks or [] if str(item).strip()]
    recommendation_items = [
        str(item).strip() for item in recommendations or [] if str(item).strip()
    ]
    summary_risks = [str(item).strip() for item in fit.get("risks") or [] if str(item).strip()]
    reuse_ideas = [
        str(item).strip() for item in fit.get("gateway_reuse_ideas") or [] if str(item).strip()
    ]
    next_steps = [str(item).strip() for item in fit.get("next_steps") or [] if str(item).strip()]

    position = repo_summary.get("description") or "仓库描述不足，需要结合 README 和目录继续确认。"
    if repo_summary.get("archived"):
        lifecycle = "archived"
    elif repo_summary.get("pushed_at") or repo_summary.get("updated_at"):
        lifecycle = "active-or-recent"
    else:
        lifecycle = "unknown"

    if not finding_items:
        finding_items = [
            f"主要语言：{repo_summary.get('language') or 'unknown'}。",
            f"关注度：{repo_summary.get('stars', 0)} stars，{repo_summary.get('forks', 0)} forks。",
            f"适配优先级：{fit.get('priority', 'unknown')}，fit_score={fit.get('fit_score', 0)}。",
        ]
    if not risk_items:
        risk_items = summary_risks or ["暂未从结构化摘要中发现明确风险，仍需人工复核 README 和关键代码。"]
    if not recommendation_items:
        recommendation_items = reuse_ideas + next_steps

    return {
        "type": "github_repo_analysis",
        "repository": repo_summary.get("repository", ""),
        "url": repo_summary.get("url", ""),
        "analysis_goal": analysis_goal.strip() or "说明项目用途、价值、风险和 Gateway 可借鉴点。",
        "project_positioning": {
            "description": position,
            "language": repo_summary.get("language") or "unknown",
            "topics": repo_summary.get("topics") or [],
            "license": repo_summary.get("license") or "unknown",
            "lifecycle": lifecycle,
        },
        "gateway_fit": {
            "score": fit.get("fit_score", 0),
            "priority": fit.get("priority", "unknown"),
            "signals": fit.get("signals") or [],
        },
        "key_findings": finding_items[:8],
        "gateway_reuse_ideas": reuse_ideas[:8],
        "risks": risk_items[:8],
        "recommendations": recommendation_items[:8],
        "suggested_report_sections": [
            "仓库结论",
            "项目定位",
            "技术栈与结构",
            "对 Gateway 的借鉴点",
            "风险与不确定点",
            "建议下一步",
        ],
    }


def plan_repo_adoption(
    repo_analysis: dict[str, Any],
    *,
    adoption_goal: str = "",
    max_stages: int = 4,
) -> dict[str, Any]:
    """把仓库分析结论转成 Gateway 可执行的采纳路线图。"""

    if repo_analysis.get("type") != "github_repo_analysis":
        raise ValueError("repo_analysis type must be github_repo_analysis")
    gateway_fit = repo_analysis.get("gateway_fit") if isinstance(repo_analysis.get("gateway_fit"), dict) else {}
    score = _as_int(gateway_fit.get("score"))
    priority = str(gateway_fit.get("priority") or "unknown")
    risks = [str(item).strip() for item in repo_analysis.get("risks") or [] if str(item).strip()]
    reuse_ideas = [
        str(item).strip()
        for item in repo_analysis.get("gateway_reuse_ideas") or []
        if str(item).strip()
    ]
    recommendations = [
        str(item).strip()
        for item in repo_analysis.get("recommendations") or []
        if str(item).strip()
    ]
    decision = _adoption_decision(score, priority, risks)
    stages = _adoption_stages(
        reuse_ideas=reuse_ideas,
        recommendations=recommendations,
        max_stages=max_stages,
    )
    return {
        "type": "github_repo_adoption_plan",
        "repository": repo_analysis.get("repository", ""),
        "url": repo_analysis.get("url", ""),
        "adoption_goal": adoption_goal.strip() or repo_analysis.get("analysis_goal", ""),
        "decision": decision,
        "fit": {
            "score": score,
            "priority": priority,
            "signals": gateway_fit.get("signals") or [],
        },
        "stages": stages,
        "risk_gates": _adoption_risk_gates(risks),
        "acceptance_checks": _adoption_acceptance_checks(stages),
        "handoff": {
            "target_agent_id": "planner",
            "summary": "可交给 planner 拆成 PROJECT_PLAN 小阶段；进入实现前应先通过 risk_gates。",
        },
        "note": "这是基于仓库分析生成的采纳路线图，不代表已经完成代码实现或依赖引入。",
    }


def compose_repo_decision_card(
    repo_summary: dict[str, Any],
    gateway_fit: dict[str, Any] | None = None,
    risk_scan: dict[str, Any] | None = None,
    *,
    decision_goal: str = "",
) -> dict[str, Any]:
    """把仓库摘要、适配评分和风险扫描压缩成轻量决策卡片。"""

    fit = gateway_fit or assess_gateway_repo_fit(repo_summary)
    scan = risk_scan or scan_github_repo_risks(repo_summary, intended_use=decision_goal)
    score = _as_int(fit.get("fit_score"))
    priority = str(fit.get("priority") or "unknown")
    risk_level = str(scan.get("risk_level") or "unknown")
    risk_decision = str(scan.get("decision") or "unknown")
    if risk_decision in {"hold", "block"} or risk_level == "high":
        decision = "hold"
        decision_label = "先暂缓"
        reason = "仓库存在高风险或复用边界不清晰，需要先人工复核。"
    elif score >= 70 and priority == "high":
        decision = "deep-dive"
        decision_label = "值得深入分析"
        reason = "仓库与 Gateway 适配信号较强，适合进入深入分析或小实验。"
    elif score >= 40 or priority == "medium":
        decision = "skim"
        decision_label = "适合快速浏览"
        reason = "仓库有一定参考价值，但暂不建议直接进入实现计划。"
    else:
        decision = "watch"
        decision_label = "保留观察"
        reason = "当前适配信号不足，适合作为素材保留。"

    risk_items = [
        str(item.get("issue") or item.get("mitigation") or "").strip()
        for item in scan.get("risk_items") or []
        if isinstance(item, dict) and str(item.get("issue") or item.get("mitigation") or "").strip()
    ]
    signals = [str(item).strip() for item in fit.get("signals") or [] if str(item).strip()]
    next_steps = [str(item).strip() for item in fit.get("next_steps") or [] if str(item).strip()]
    next_steps.extend(str(item).strip() for item in scan.get("next_actions") or [] if str(item).strip())
    return {
        "type": "github_repo_decision_card",
        "repository": repo_summary.get("repository", ""),
        "url": repo_summary.get("url", ""),
        "decision_goal": decision_goal.strip() or "判断是否值得继续分析或采纳。",
        "decision": decision,
        "decision_label": decision_label,
        "reason": reason,
        "fit": {
            "score": score,
            "priority": priority,
            "signals": signals[:5],
        },
        "risk": {
            "level": risk_level,
            "decision": risk_decision,
            "items": risk_items[:5],
        },
        "repo_snapshot": {
            "description": repo_summary.get("description") or "",
            "language": repo_summary.get("language") or "unknown",
            "stars": _as_int(repo_summary.get("stars")),
            "license": repo_summary.get("license") or "unknown",
            "archived": bool(repo_summary.get("archived")),
            "pushed_at": repo_summary.get("pushed_at") or "",
        },
        "reuse_ideas": [
            str(item).strip()
            for item in fit.get("gateway_reuse_ideas") or []
            if str(item).strip()
        ][:5],
        "next_actions": list(dict.fromkeys(next_steps))[:6],
        "note": "这是仓库轻量决策卡片，不代表已经完成正式分析、风险门禁或采纳计划。",
    }


def _adoption_decision(score: int, priority: str, risks: list[str]) -> dict[str, str]:
    severe_risk = any("许可证" in risk or "归档" in risk for risk in risks)
    if severe_risk:
        action = "hold"
        reason = "存在许可或维护状态风险，先人工确认再进入实现。"
    elif priority == "high" or score >= 70:
        action = "adopt"
        reason = "适配分较高，建议进入小步验证和落地拆解。"
    elif priority == "medium" or score >= 40:
        action = "pilot"
        reason = "有参考价值，但应先做轻量原型或文档对比。"
    else:
        action = "watch"
        reason = "当前信号不足，建议保留观察，不进入近期实现。"
    return {"action": action, "reason": reason}


def _adoption_stages(
    *,
    reuse_ideas: list[str],
    recommendations: list[str],
    max_stages: int,
) -> list[dict[str, Any]]:
    seeds = reuse_ideas or recommendations or ["先阅读 README、目录结构和关键文件，确认是否存在可迁移设计。"]
    stages = [
        {
            "id": "stage-1",
            "title": "证据复核",
            "objective": "确认 README、许可证、维护状态和关键目录是否支持继续采纳。",
            "tasks": [
                "复核 README 与目录树，标注可见证据和推断内容。",
                "确认许可证、最近更新时间和是否归档。",
            ],
        }
    ]
    for index, idea in enumerate(seeds[: max(1, min(max_stages - 1, 5))], start=2):
        stages.append(
            {
                "id": f"stage-{index}",
                "title": f"落地验证 {index - 1}",
                "objective": idea,
                "tasks": [
                    "拆成一个不超过半天的小实验。",
                    "只改 Gateway 中最小必要范围，保留回滚路径。",
                    "补充对应测试或文档验收项。",
                ],
            }
        )
    return stages[: max(1, min(max_stages, 6))]


def _adoption_risk_gates(risks: list[str]) -> list[str]:
    gates = [
        "确认许可证允许学习、引用或复用。",
        "确认仓库未归档且关键依赖仍可获得。",
    ]
    gates.extend(risks[:4])
    return list(dict.fromkeys(gates))


def _adoption_acceptance_checks(stages: list[dict[str, Any]]) -> list[str]:
    checks = ["形成一份可追溯的证据摘要。"]
    if len(stages) > 1:
        checks.append("至少完成一个最小落地实验，并记录是否继续推进。")
    checks.append("相关代码或文档变更必须有聚焦测试或人工验收说明。")
    return checks


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _gateway_reuse_ideas(text: str, tree_paths: list[str]) -> list[str]:
    """根据仓库文本和目录信号生成 Gateway 可借鉴方向。"""

    ideas: list[str] = []
    if "skill" in text or any("/skills/" in path for path in tree_paths):
        ideas.append("参考 Skill 组织方式，优化 Gateway workspace/skills 的分类和说明。")
    if "agent" in text or any(path.endswith("agents.md") for path in tree_paths):
        ideas.append("参考 Agent 提示词边界，优化入口 Agent 与能力 Agent 的协作协议。")
    if any(word in text for word in ("workflow", "scheduler", "cron")):
        ideas.append("参考工作流或调度模式，改进 Cron / 主动任务的表达方式。")
    if any(word in text for word in ("tool", "tools", "function calling")):
        ideas.append("参考工具 schema 和调用约束，补强 Gateway 内置工具设计。")
    if not ideas:
        ideas.append("先作为资料库素材，等待具体需求再做深入拆解。")
    return ideas[:5]


def register_github_repo_tools(
    registry: ToolRegistry,
    *,
    client: GitHubRepoClient | None = None,
    token: str = "",
    max_output_chars: int = 50_000,
) -> None:
    """注册 GitHub 仓库分析辅助工具。"""

    github = client or GitHubRepoClient(token=token)

    def github_repo_summary(repo_url: str, max_tree_items: int = 80) -> str:
        if not repo_url.strip():
            return "Error: repo_url is required"
        summary = github.summarize(repo_url, max_tree_items=max_tree_items)
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if len(text) <= max_output_chars:
            return text
        compact = dict(summary)
        compact["truncated"] = True
        compact["original_chars"] = len(text)
        return json.dumps(compact, ensure_ascii=False)[:max_output_chars]

    def github_repo_gateway_fit(
        repo_summary_json: str,
        focus: list[str] | None = None,
    ) -> str:
        if not repo_summary_json.strip():
            return "Error: repo_summary_json is required"
        data = json.loads(repo_summary_json)
        if not isinstance(data, dict):
            return "Error: repo_summary_json must be a JSON object"
        assessment = assess_gateway_repo_fit(data, focus=focus or [])
        return json.dumps(assessment, ensure_ascii=False, indent=2)

    def github_repo_risk_scan(
        repo_summary_json: str,
        intended_use: str = "",
    ) -> str:
        if not repo_summary_json.strip():
            return "Error: repo_summary_json is required"
        data = json.loads(repo_summary_json)
        if not isinstance(data, dict):
            return "Error: repo_summary_json must be a JSON object"
        scan = scan_github_repo_risks(data, intended_use=intended_use)
        return json.dumps(scan, ensure_ascii=False, indent=2)

    def github_repo_decision_card(
        repo_summary_json: str,
        gateway_fit_json: str = "",
        risk_scan_json: str = "",
        decision_goal: str = "",
    ) -> str:
        if not repo_summary_json.strip():
            return "Error: repo_summary_json is required"
        summary = json.loads(repo_summary_json)
        if not isinstance(summary, dict):
            return "Error: repo_summary_json must be a JSON object"
        fit = None
        if gateway_fit_json.strip():
            parsed_fit = json.loads(gateway_fit_json)
            if not isinstance(parsed_fit, dict):
                return "Error: gateway_fit_json must be a JSON object"
            fit = parsed_fit
        risk = None
        if risk_scan_json.strip():
            parsed_risk = json.loads(risk_scan_json)
            if not isinstance(parsed_risk, dict):
                return "Error: risk_scan_json must be a JSON object"
            if parsed_risk.get("type") != "github_repo_risk_scan":
                return "Error: risk_scan_json type must be github_repo_risk_scan"
            risk = parsed_risk
        card = compose_repo_decision_card(
            summary,
            fit,
            risk,
            decision_goal=decision_goal,
        )
        return json.dumps(card, ensure_ascii=False, indent=2)

    def compose_github_repo_analysis(
        repo_summary_json: str,
        gateway_fit_json: str = "",
        analysis_goal: str = "",
        key_findings: list[str] | None = None,
        risks: list[str] | None = None,
        recommendations: list[str] | None = None,
    ) -> str:
        """组合仓库摘要、fit 评分和分析补充项，输出稳定分析 JSON。"""

        if not repo_summary_json.strip():
            return "Error: repo_summary_json is required"
        summary = json.loads(repo_summary_json)
        if not isinstance(summary, dict):
            return "Error: repo_summary_json must be a JSON object"
        fit = None
        if gateway_fit_json.strip():
            parsed_fit = json.loads(gateway_fit_json)
            if not isinstance(parsed_fit, dict):
                return "Error: gateway_fit_json must be a JSON object"
            fit = parsed_fit
        analysis = compose_repo_analysis(
            summary,
            fit,
            analysis_goal=analysis_goal,
            key_findings=key_findings,
            risks=risks,
            recommendations=recommendations,
        )
        return json.dumps(analysis, ensure_ascii=False, indent=2)

    def plan_github_repo_adoption(
        repo_analysis_json: str,
        adoption_goal: str = "",
        max_stages: int = 4,
    ) -> str:
        if not repo_analysis_json.strip():
            return "Error: repo_analysis_json is required"
        analysis = json.loads(repo_analysis_json)
        if not isinstance(analysis, dict):
            return "Error: repo_analysis_json must be a JSON object"
        try:
            plan = plan_repo_adoption(
                analysis,
                adoption_goal=adoption_goal,
                max_stages=max_stages,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(plan, ensure_ascii=False, indent=2)

    registry.register(
        RegisteredTool(
            name="github_repo_summary",
            description=(
                "Fetch structured metadata, README excerpt, and repository tree summary "
                "for a public GitHub repository."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_url"],
                "properties": {
                    "repo_url": {
                        "type": "string",
                        "description": "GitHub repository URL or owner/repo.",
                    },
                    "max_tree_items": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                    },
                },
            },
            handler=github_repo_summary,
            tags=("github", "repository", "read", "network"),
        )
    )
    registry.register(
        RegisteredTool(
            name="github_repo_gateway_fit",
            description=(
                "Assess how useful a GitHub repository is for Gateway: fit score, "
                "priority, reusable ideas, risks, and next steps. Input should be "
                "the JSON returned by github_repo_summary."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_summary_json"],
                "properties": {
                    "repo_summary_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_summary.",
                    },
                    "focus": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional focus keywords, such as skills, agents, workflow.",
                    },
                },
            },
            handler=github_repo_gateway_fit,
            tags=("github", "repository", "analysis"),
        )
    )
    registry.register(
        RegisteredTool(
            name="github_repo_risk_scan",
            description=(
                "Scan a GitHub repository summary for adoption risks: license, archived state, "
                "README evidence, issue volume, dependency files, and next validation actions."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_summary_json"],
                "properties": {
                    "repo_summary_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_summary.",
                    },
                    "intended_use": {
                        "type": "string",
                        "description": "How Gateway intends to reuse or learn from this repository.",
                    },
                },
            },
            handler=github_repo_risk_scan,
            tags=("github", "repository", "risk", "analysis"),
        )
    )
    registry.register(
        RegisteredTool(
            name="github_repo_decision_card",
            description=(
                "Compose a lightweight GitHub repository decision card from "
                "github_repo_summary, optional gateway fit, and optional risk scan: "
                "decision, reason, fit score, risks, reuse ideas, and next actions."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_summary_json"],
                "properties": {
                    "repo_summary_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_summary.",
                    },
                    "gateway_fit_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by github_repo_gateway_fit.",
                    },
                    "risk_scan_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by github_repo_risk_scan.",
                    },
                    "decision_goal": {
                        "type": "string",
                        "description": "What the user wants to decide about this repository.",
                    },
                },
            },
            handler=github_repo_decision_card,
            tags=("github", "repository", "decision", "analysis"),
        )
    )
    registry.register(
        RegisteredTool(
            name="compose_github_repo_analysis",
            description=(
                "Compose a stable repository analysis JSON from github_repo_summary, "
                "optional github_repo_gateway_fit, and curated findings."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_summary_json"],
                "properties": {
                    "repo_summary_json": {
                        "type": "string",
                        "description": "JSON string returned by github_repo_summary.",
                    },
                    "gateway_fit_json": {
                        "type": "string",
                        "description": "Optional JSON string returned by github_repo_gateway_fit.",
                    },
                    "analysis_goal": {
                        "type": "string",
                        "description": "What the user wants to learn from the repository.",
                    },
                    "key_findings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Curated findings from README/tree/key files.",
                    },
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional risks or uncertainties found by the agent.",
                    },
                    "recommendations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional next actions or Gateway adaptation ideas.",
                    },
                },
            },
            handler=compose_github_repo_analysis,
            tags=("github", "repository", "analysis", "report"),
        )
    )
    registry.register(
        RegisteredTool(
            name="plan_github_repo_adoption",
            description=(
                "Turn a github_repo_analysis JSON object into a Gateway adoption plan: "
                "decision, implementation stages, risk gates, acceptance checks, and planner handoff."
            ),
            input_schema={
                "type": "object",
                "required": ["repo_analysis_json"],
                "properties": {
                    "repo_analysis_json": {
                        "type": "string",
                        "description": "JSON string returned by compose_github_repo_analysis.",
                    },
                    "adoption_goal": {
                        "type": "string",
                        "description": "What Gateway wants to adopt or learn from this repository.",
                    },
                    "max_stages": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 6,
                    },
                },
            },
            handler=plan_github_repo_adoption,
            tags=("github", "repository", "planning"),
        )
    )
