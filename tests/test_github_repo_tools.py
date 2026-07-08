import base64
import json

from agent_gateway.ai.tools.github_repo import (
    GitHubRepoClient,
    assess_gateway_repo_fit,
    compose_repo_analysis,
    compose_repo_decision_card,
    compose_repo_reading_guide,
    normalize_github_repo_summary,
    plan_repo_adoption,
    parse_github_repo,
    register_github_repo_tools,
    scan_github_repo_risks,
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


def test_scan_github_repo_risks_flags_license_archive_and_dependencies() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="risky",
        repo_data={
            "html_url": "https://github.com/demo/risky",
            "description": "old agent project",
            "language": "Python",
            "topics": ["agent"],
            "stargazers_count": 5,
            "open_issues_count": 120,
            "license": None,
            "archived": True,
        },
        readme={"name": "README.md", "path": "README.md", "content": "", "error": "not found"},
        tree=[
            {"path": "requirements.txt", "type": "blob", "size": 200},
            {"path": "package.json", "type": "blob", "size": 500},
        ],
    )

    scan = scan_github_repo_risks(summary, intended_use="学习 Agent 工具组织")

    assert scan["type"] == "github_repo_risk_scan"
    assert scan["repository"] == "demo/risky"
    assert scan["risk_level"] == "high"
    assert scan["decision"] == "hold"
    assert scan["dependency_files"] == ["requirements.txt", "package.json"]
    assert scan["summary"]["license"] == "unknown"
    assert any(item["area"] == "license" and item["severity"] == "high" for item in scan["risk_items"])
    assert any(item["area"] == "maintenance" for item in scan["risk_items"])
    assert scan["next_actions"][0] == "围绕预期用途「学习 Agent 工具组织」复核风险是否可接受。"


def test_github_repo_risk_scan_tool_outputs_stable_json() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="safe",
        repo_data={
            "html_url": "https://github.com/demo/safe",
            "description": "Agent workflow templates",
            "language": "Markdown",
            "topics": ["agent", "workflow"],
            "stargazers_count": 300,
            "open_issues_count": 2,
            "license": {"spdx_id": "MIT"},
            "archived": False,
        },
        readme={"name": "README.md", "path": "README.md", "content": "workflow docs", "error": ""},
        tree=[{"path": "README.md", "type": "blob", "size": 200}],
    )
    registry = ToolRegistry()
    register_github_repo_tools(registry, client=object())

    scan = json.loads(
        registry.dispatch(
            "github_repo_risk_scan",
            {
                "repo_summary_json": json.dumps(summary, ensure_ascii=False),
                "intended_use": "参考 workflow 文档结构",
            },
        )
    )

    assert scan["type"] == "github_repo_risk_scan"
    assert scan["risk_level"] == "low"
    assert scan["decision"] == "pass"
    assert scan["risk_items"] == []
    assert scan["summary"]["license"] == "MIT"


def test_compose_repo_decision_card_recommends_deep_dive_for_strong_fit() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="workflow",
        repo_data={
            "html_url": "https://github.com/demo/workflow",
            "description": "Agent workflow and tool calling templates",
            "language": "Markdown",
            "topics": ["agent", "workflow"],
            "stargazers_count": 2000,
            "forks_count": 120,
            "open_issues_count": 2,
            "license": {"spdx_id": "MIT"},
            "archived": False,
            "pushed_at": "2026-07-01T00:00:00Z",
        },
        readme={
            "name": "README.md",
            "path": "README.md",
            "content": "Agent workflow examples and tool calling templates.",
            "error": "",
        },
        tree=[{"path": "skills/writer/SKILL.md", "type": "blob", "size": 100}],
    )
    fit = assess_gateway_repo_fit(summary, focus=["workflow"])
    risk = scan_github_repo_risks(summary, intended_use="判断是否适合 Gateway 借鉴")

    card = compose_repo_decision_card(
        summary,
        fit,
        risk,
        decision_goal="判断是否适合 Gateway 借鉴",
    )

    assert card["type"] == "github_repo_decision_card"
    assert card["repository"] == "demo/workflow"
    assert card["decision"] == "deep-dive"
    assert card["decision_label"] == "值得深入分析"
    assert card["fit"]["score"] == fit["fit_score"]
    assert card["risk"]["level"] == "low"
    assert card["risk"]["decision"] in {"pass", "watch"}
    assert card["reuse_ideas"]
    assert card["next_actions"]


def test_github_repo_decision_card_tool_outputs_stable_json() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="risky",
        repo_data={
            "html_url": "https://github.com/demo/risky",
            "description": "Archived agent tools",
            "language": "Python",
            "topics": ["agent"],
            "stargazers_count": 20,
            "open_issues_count": 1,
            "license": None,
            "archived": True,
        },
        readme={"name": "README.md", "path": "README.md", "content": "agent tools", "error": ""},
        tree=[{"path": "README.md", "type": "blob", "size": 100}],
    )
    registry = ToolRegistry()
    register_github_repo_tools(registry, client=object())

    card = json.loads(
        registry.dispatch(
            "github_repo_decision_card",
            {
                "repo_summary_json": json.dumps(summary, ensure_ascii=False),
                "decision_goal": "判断是否可以采纳",
            },
        )
    )

    assert card["type"] == "github_repo_decision_card"
    assert card["repository"] == "demo/risky"
    assert card["decision"] == "hold"
    assert card["decision_goal"] == "判断是否可以采纳"
    assert card["repo_snapshot"]["archived"] is True


def test_compose_repo_reading_guide_prioritizes_key_files() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="skills",
        repo_data={
            "html_url": "https://github.com/demo/skills",
            "description": "Agent skills",
            "language": "Markdown",
            "license": {"spdx_id": "MIT"},
        },
        readme={"name": "README.md", "path": "README.md", "content": "agent skills", "error": ""},
        tree=[
            {"path": "README.md", "type": "blob", "size": 100},
            {"path": "pyproject.toml", "type": "blob", "size": 100},
            {"path": "src/main.py", "type": "blob", "size": 100},
            {"path": "skills/writer/SKILL.md", "type": "blob", "size": 100},
            {"path": "tests/test_skill.py", "type": "blob", "size": 100},
        ],
    )

    guide = compose_repo_reading_guide(summary, reading_goal="快速判断可复用 Skill", max_items=5)

    assert guide["type"] == "github_repo_reading_guide"
    assert guide["repository"] == "demo/skills"
    assert guide["reading_goal"] == "快速判断可复用 Skill"
    paths = [item["path"] for item in guide["priority_files"]]
    assert paths == [
        "README.md",
        "pyproject.toml",
        "src/main.py",
        "skills/writer/SKILL.md",
        "tests/test_skill.py",
    ]
    assert guide["priority_files"][3]["category"] == "agent-skill-assets"
    assert "轻量阅读路线" in guide["note"]


def test_github_repo_reading_guide_tool_outputs_stable_json() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="repo",
        repo_data={
            "html_url": "https://github.com/demo/repo",
            "description": "agent gateway templates",
            "language": "Python",
            "topics": ["agent"],
            "license": {"spdx_id": "Apache-2.0"},
        },
        readme={"name": "README.md", "path": "README.md", "content": "agent gateway", "error": ""},
        tree=[
            {"path": "Dockerfile", "type": "blob", "size": 5},
            {"path": "agent_gateway/app.py", "type": "blob", "size": 5},
            {"path": "AGENTS.md", "type": "blob", "size": 5},
        ],
    )
    registry = ToolRegistry()
    register_github_repo_tools(registry, client=object())

    guide = json.loads(
        registry.dispatch(
            "github_repo_reading_guide",
            {
                "repo_summary_json": json.dumps(summary, ensure_ascii=False),
                "reading_goal": "先看核心入口",
                "max_items": 4,
            },
        )
    )

    assert guide["type"] == "github_repo_reading_guide"
    assert guide["repository"] == "demo/repo"
    assert guide["reading_goal"] == "先看核心入口"
    assert [item["path"] for item in guide["priority_files"]] == [
        "README.md",
        "Dockerfile",
        "agent_gateway/app.py",
        "AGENTS.md",
    ]


def test_compose_repo_analysis_combines_summary_fit_and_findings() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="workflow",
        repo_data={
            "html_url": "https://github.com/demo/workflow",
            "description": "Agent workflow and tool calling templates",
            "language": "Markdown",
            "topics": ["agent", "workflow"],
            "stargazers_count": 2000,
            "forks_count": 120,
            "license": {"spdx_id": "MIT"},
            "archived": False,
            "pushed_at": "2026-07-01T00:00:00Z",
        },
        readme={
            "name": "README.md",
            "path": "README.md",
            "content": "Agent workflow examples.",
            "error": "",
        },
        tree=[{"path": "skills/writer/SKILL.md", "type": "blob", "size": 100}],
    )
    fit = assess_gateway_repo_fit(summary, focus=["workflow"])

    analysis = compose_repo_analysis(
        summary,
        fit,
        analysis_goal="判断是否值得 Gateway 借鉴。",
        key_findings=["仓库提供 workflow 示例。"],
        risks=["需要确认许可证复用边界。"],
        recommendations=["优先抽取 workflow 模板。"],
    )

    assert analysis["type"] == "github_repo_analysis"
    assert analysis["repository"] == "demo/workflow"
    assert analysis["analysis_goal"] == "判断是否值得 Gateway 借鉴。"
    assert analysis["gateway_fit"]["priority"] == fit["priority"]
    assert analysis["key_findings"] == ["仓库提供 workflow 示例。"]
    assert analysis["risks"] == ["需要确认许可证复用边界。"]
    assert analysis["recommendations"] == ["优先抽取 workflow 模板。"]
    assert "对 Gateway 的借鉴点" in analysis["suggested_report_sections"]


def test_compose_github_repo_analysis_tool_outputs_stable_json() -> None:
    summary = normalize_github_repo_summary(
        owner="demo",
        repo="repo",
        repo_data={
            "html_url": "https://github.com/demo/repo",
            "description": "agent gateway templates",
            "language": "Python",
            "topics": ["agent", "gateway"],
            "license": {"spdx_id": "Apache-2.0"},
            "archived": False,
        },
        readme={"name": "README.md", "path": "README.md", "content": "agent gateway", "error": ""},
        tree=[{"path": "AGENTS.md", "type": "blob", "size": 5}],
    )
    fit = assess_gateway_repo_fit(summary)
    registry = ToolRegistry()
    register_github_repo_tools(registry, client=object())

    analysis = json.loads(
        registry.dispatch(
            "compose_github_repo_analysis",
            {
                "repo_summary_json": json.dumps(summary, ensure_ascii=False),
                "gateway_fit_json": json.dumps(fit, ensure_ascii=False),
                "analysis_goal": "输出结构化仓库分析",
            },
        )
    )

    assert analysis["type"] == "github_repo_analysis"
    assert analysis["repository"] == "demo/repo"
    assert analysis["project_positioning"]["language"] == "Python"
    assert analysis["gateway_fit"]["score"] == fit["fit_score"]
    assert analysis["recommendations"]


def test_plan_repo_adoption_turns_analysis_into_gateway_roadmap() -> None:
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/workflow",
        "url": "https://github.com/demo/workflow",
        "analysis_goal": "判断 workflow 是否值得落地。",
        "gateway_fit": {
            "score": 82,
            "priority": "high",
            "signals": ["包含 Agent workflow 信号。"],
        },
        "gateway_reuse_ideas": ["参考工作流模板，改进 Gateway 主动任务编排。"],
        "recommendations": ["先做一个最小 Cron workflow 原型。"],
        "risks": ["需要确认许可证复用边界。"],
    }

    plan = plan_repo_adoption(analysis, adoption_goal="拆成 Gateway 落地阶段", max_stages=3)

    assert plan["type"] == "github_repo_adoption_plan"
    assert plan["repository"] == "demo/workflow"
    assert plan["decision"]["action"] == "hold"
    assert plan["fit"]["score"] == 82
    assert plan["stages"][0]["title"] == "证据复核"
    assert plan["stages"][1]["objective"] == "参考工作流模板，改进 Gateway 主动任务编排。"
    assert "确认许可证允许学习、引用或复用。" in plan["risk_gates"]
    assert plan["handoff"]["target_agent_id"] == "planner"


def test_plan_github_repo_adoption_tool_outputs_stable_json() -> None:
    registry = ToolRegistry()
    register_github_repo_tools(registry, client=object())
    analysis = {
        "type": "github_repo_analysis",
        "repository": "demo/agent",
        "url": "https://github.com/demo/agent",
        "analysis_goal": "学习 Agent prompt 组织。",
        "gateway_fit": {"score": 75, "priority": "high", "signals": []},
        "gateway_reuse_ideas": ["参考 Agent 提示词边界。"],
        "recommendations": [],
        "risks": [],
    }

    plan = json.loads(
        registry.dispatch(
            "plan_github_repo_adoption",
            {
                "repo_analysis_json": json.dumps(analysis, ensure_ascii=False),
                "adoption_goal": "优化 Gateway Agent 提示词",
                "max_stages": 2,
            },
        )
    )

    assert plan["decision"]["action"] == "adopt"
    assert plan["adoption_goal"] == "优化 Gateway Agent 提示词"
    assert len(plan["stages"]) == 2
