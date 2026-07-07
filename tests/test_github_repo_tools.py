import base64
import json

from agent_gateway.ai.tools.github_repo import (
    GitHubRepoClient,
    assess_gateway_repo_fit,
    normalize_github_repo_summary,
    parse_github_repo,
    register_github_repo_tools,
)
from agent_gateway.ai.tools.registry import ToolRegistry


class FakeGitHubHttp:
    def __init__(self) -> None:
        self.requests = []

    def get(self, url, headers=None):
        self.requests.append((url, headers or {}))
        if url.endswith("/repos/openclaw/openclaw"):
            return FakeResponse(
                {
                    "html_url": "https://github.com/openclaw/openclaw",
                    "description": "Agent Gateway upstream",
                    "language": "Python",
                    "topics": ["agent", "gateway"],
                    "stargazers_count": 42,
                    "forks_count": 7,
                    "open_issues_count": 3,
                    "license": {"spdx_id": "MIT"},
                    "default_branch": "main",
                    "pushed_at": "2026-07-01T00:00:00Z",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "archived": False,
                }
            )
        if url.endswith("/repos/openclaw/openclaw/readme"):
            content = base64.b64encode("# OpenClaw\n\nGateway project".encode()).decode()
            return FakeResponse({"name": "README.md", "path": "README.md", "content": content, "encoding": "base64"})
        if url.endswith("/repos/openclaw/openclaw/git/trees/main?recursive=1"):
            return FakeResponse(
                {
                    "tree": [
                        {"path": "README.md", "type": "blob", "size": 100},
                        {"path": "agent_gateway/app.py", "type": "blob", "size": 200},
                    ]
                }
            )
        raise AssertionError(f"unexpected url: {url}")


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def test_parse_github_repo_accepts_url_and_short_name() -> None:
    assert parse_github_repo("https://github.com/openclaw/openclaw") == ("openclaw", "openclaw")
    assert parse_github_repo("openclaw/openclaw.git") == ("openclaw", "openclaw")


def test_github_repo_client_summarizes_repo() -> None:
    client = GitHubRepoClient(token="token")
    client._http = FakeGitHubHttp()

    summary = client.summarize("https://github.com/openclaw/openclaw", max_tree_items=10)

    assert summary["repository"] == "openclaw/openclaw"
    assert summary["description"] == "Agent Gateway upstream"
    assert summary["language"] == "Python"
    assert summary["license"] == "MIT"
    assert summary["readme"]["excerpt"].startswith("# OpenClaw")
    assert summary["tree"][1]["path"] == "agent_gateway/app.py"
    assert client._http.requests[0][1]["Authorization"] == "Bearer token"


def test_register_github_repo_summary_tool_outputs_json() -> None:
    class FakeClient:
        def summarize(self, repo_url: str, *, max_tree_items: int):
            return normalize_github_repo_summary(
                owner="demo",
                repo="repo",
                repo_data={
                    "html_url": repo_url,
                    "description": "demo",
                    "language": "Python",
                    "license": {"spdx_id": "Apache-2.0"},
                },
                readme={"name": "README.md", "path": "README.md", "content": "hello", "error": ""},
                tree=[{"path": "README.md", "type": "blob", "size": 5}],
            )

    registry = ToolRegistry()
    register_github_repo_tools(registry, client=FakeClient())

    payload = json.loads(
        registry.dispatch(
            "github_repo_summary",
            {"repo_url": "https://github.com/demo/repo", "max_tree_items": 5},
        )
    )

    assert payload["repository"] == "demo/repo"
    assert payload["readme"]["excerpt"] == "hello"
    assert payload["tree_count"] == 1


def test_assess_gateway_repo_fit_scores_reusable_agent_assets() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="skills",
        repo_data={
            "html_url": "https://github.com/demo/skills",
            "description": "Agent skills and workflow templates for tool calling",
            "language": "Markdown",
            "topics": ["agent", "skills", "workflow"],
            "stargazers_count": 1500,
            "open_issues_count": 4,
            "license": {"spdx_id": "MIT"},
            "archived": False,
        },
        readme={
            "name": "README.md",
            "path": "README.md",
            "content": "This repo contains Agent Skill definitions and workflow examples.",
            "error": "",
        },
        tree=[
            {"path": "skills/writer/SKILL.md", "type": "blob", "size": 100},
            {"path": "AGENTS.md", "type": "blob", "size": 50},
        ],
    )

    fit = assess_gateway_repo_fit(summary, focus=["skills"])

    assert fit["repository"] == "demo/skills"
    assert fit["priority"] == "high"
    assert fit["fit_score"] >= 70
    assert any("Skill" in item for item in fit["gateway_reuse_ideas"])
    assert any("关注度较高" in signal for signal in fit["signals"])


def test_github_repo_gateway_fit_tool_uses_summary_json() -> None:
    class FakeClient:
        def summarize(self, repo_url: str, *, max_tree_items: int):
            return normalize_github_repo_summary(
                owner="demo",
                repo="repo",
                repo_data={
                    "html_url": repo_url,
                    "description": "demo agent gateway",
                    "language": "Python",
                    "topics": ["agent", "gateway"],
                    "license": {"spdx_id": "Apache-2.0"},
                },
                readme={"name": "README.md", "path": "README.md", "content": "agent gateway", "error": ""},
                tree=[{"path": "skills/example/SKILL.md", "type": "blob", "size": 5}],
            )

    registry = ToolRegistry()
    register_github_repo_tools(registry, client=FakeClient())
    summary_json = registry.dispatch(
        "github_repo_summary",
        {"repo_url": "https://github.com/demo/repo", "max_tree_items": 5},
    )
    fit = json.loads(
        registry.dispatch(
            "github_repo_gateway_fit",
            {"repo_summary_json": summary_json, "focus": ["agent"]},
        )
    )

    assert fit["repository"] == "demo/repo"
    assert fit["priority"] in {"medium", "high"}
    assert "next_steps" in fit
