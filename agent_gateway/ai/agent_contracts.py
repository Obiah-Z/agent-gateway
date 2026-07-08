from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class AgentRoutingContract:
    """Baseline contract for one entry-agent routing scenario."""

    name: str
    user_text: str
    expected_intent: str
    expected_agent_id: str
    expected_requires_collaboration: bool
    context_hint: str = ""
    required_tools: tuple[str, ...] = ()
    read_only: bool = True
    requires_confirmation: bool = False
    collaboration_mode: str = "single-agent"


DEFAULT_AGENT_ROUTING_CONTRACTS: tuple[AgentRoutingContract, ...] = (
    AgentRoutingContract(
        name="simple-chat",
        user_text="你好，简单介绍一下你能做什么",
        expected_intent="chat",
        expected_agent_id="main",
        expected_requires_collaboration=False,
        required_tools=("classify_task_intent", "format_entry_response"),
    ),
    AgentRoutingContract(
        name="repo-analysis",
        user_text="帮我分析一下这个仓库 https://github.com/Obiah-Z/agent-gateway",
        expected_intent="repo-analysis",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
        required_tools=("compose_github_repo_analysis", "format_github_repo_analysis"),
    ),
    AgentRoutingContract(
        name="repo-reading-guide",
        user_text="这个仓库 https://github.com/Obiah-Z/agent-gateway 我应该先看哪些文件，从哪里读起？",
        expected_intent="repo-reading-guide",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=False,
        required_tools=("github_repo_reading_guide", "format_github_repo_reading_guide"),
    ),
    AgentRoutingContract(
        name="repo-adoption",
        user_text="分析这个 GitHub 仓库 https://github.com/Obiah-Z/agent-gateway，评估风险，并给出采纳计划和正式报告",
        expected_intent="repo-adoption",
        expected_agent_id="repo-analyzer",
        expected_requires_collaboration=True,
        required_tools=("plan_github_repo_adoption", "format_github_repo_adoption_plan"),
        collaboration_mode="repo-adoption",
    ),
    AgentRoutingContract(
        name="research-option-validation",
        user_text="帮我做 RabbitMQ、Redis、Kafka 的技术选型和方案对比，输出验证计划、风险审查和正式报告",
        expected_intent="research-option-validation",
        expected_agent_id="research",
        expected_requires_collaboration=True,
        required_tools=("compose_research_option_comparison",),
        collaboration_mode="research-option-validation",
    ),
    AgentRoutingContract(
        name="planning",
        user_text="帮我规划一下下一阶段任务，拆成阶段和验收标准",
        expected_intent="planning",
        expected_agent_id="planner",
        expected_requires_collaboration=False,
        required_tools=("plan_execution_stage", "format_execution_stage_plan"),
    ),
    AgentRoutingContract(
        name="agent-capabilities",
        user_text="当前系统有哪些 Agent？每个 Agent 能做什么？",
        expected_intent="agent-capabilities",
        expected_agent_id="main",
        expected_requires_collaboration=False,
        required_tools=(
            "list_agent_capabilities",
            "format_agent_capability_catalog",
            "explain_agent_capability_contract",
            "format_agent_capability_contract",
            "check_agent_capability_contracts",
            "format_agent_capability_contract_check",
        ),
    ),
    AgentRoutingContract(
        name="agent-capability-contract",
        user_text="饮食记录这个任务会不会写入数据，执行前是否需要我确认？",
        expected_intent="agent-capability-contract",
        expected_agent_id="main",
        expected_requires_collaboration=False,
        required_tools=("explain_agent_capability_contract", "format_agent_capability_contract"),
    ),
    AgentRoutingContract(
        name="ops",
        user_text="帮我看一下 Docker 容器和 Redis、RabbitMQ、PostgreSQL 的运行状态",
        expected_intent="ops",
        expected_agent_id="ops",
        expected_requires_collaboration=False,
        required_tools=("ops_readonly_health", "ops_runtime_diagnostics"),
    ),
    AgentRoutingContract(
        name="diet",
        user_text="今天早餐吃了鸡蛋和牛奶，帮我记录一下饮食",
        expected_intent="diet",
        expected_agent_id="diet-assistant-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("meal_log_add", "format_meal_log_entry"),
        read_only=False,
        requires_confirmation=True,
    ),
    AgentRoutingContract(
        name="personal",
        user_text="提醒我明天上午继续背项目八股，并做一次复盘",
        expected_intent="personal",
        expected_agent_id="personal-secretary-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("personal_todo_add", "personal_review_add"),
        read_only=False,
        requires_confirmation=True,
    ),
    AgentRoutingContract(
        name="personal-due-reminders",
        user_text="今天有哪些到期提醒和逾期待办？",
        expected_intent="personal",
        expected_agent_id="personal-secretary-zhanghaibo",
        expected_requires_collaboration=False,
        required_tools=("personal_due_todo_digest_generate", "format_personal_due_todo_digest"),
    ),
    AgentRoutingContract(
        name="document",
        user_text="把这段材料整理成 Markdown 报告",
        expected_intent="document",
        expected_agent_id="doc-writer",
        expected_requires_collaboration=False,
        required_tools=("outline_structured_document", "save_structured_document"),
        read_only=False,
        requires_confirmation=False,
    ),
    AgentRoutingContract(
        name="review",
        user_text="帮我审查这个方案是否合理，有哪些风险和隐患",
        expected_intent="review",
        expected_agent_id="reviewer",
        expected_requires_collaboration=False,
        required_tools=("assess_risk_decision", "format_risk_decision_assessment"),
    ),
)


def load_agent_tool_allowlists(config_path: Path) -> dict[str, set[str]]:
    """Load Agent tool allowlists from config/agents.json."""

    data = json.loads(config_path.read_text(encoding="utf-8"))
    agents = data.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("Agent config field 'agents' must be a list")
    return {
        str(agent.get("id") or ""): set(agent.get("tool_policy", {}).get("tool_names", []))
        for agent in agents
        if isinstance(agent, dict) and agent.get("id")
    }


def baseline_agent_required_tools(
    contracts: tuple[AgentRoutingContract, ...] = DEFAULT_AGENT_ROUTING_CONTRACTS,
) -> dict[str, tuple[str, ...]]:
    """Aggregate baseline required tools by Agent from routing contracts."""

    required: dict[str, set[str]] = {}
    for contract in contracts:
        bucket = required.setdefault(contract.expected_agent_id, set())
        bucket.update(contract.required_tools)
    return {agent_id: tuple(sorted(tool_names)) for agent_id, tool_names in sorted(required.items())}


def find_agent_contract_gaps(
    tool_allowlists: dict[str, set[str]],
    required_tools: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Return missing Agents and missing tools for baseline routing contracts."""

    required = required_tools or baseline_agent_required_tools()
    missing_agents = [agent_id for agent_id in required if agent_id not in tool_allowlists]
    missing_tools: dict[str, list[str]] = {}
    for agent_id, tool_names in required.items():
        if agent_id not in tool_allowlists:
            continue
        missing = [tool_name for tool_name in tool_names if tool_name not in tool_allowlists[agent_id]]
        if missing:
            missing_tools[agent_id] = missing
    return missing_agents, missing_tools
