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
