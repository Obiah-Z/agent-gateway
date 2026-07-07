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
