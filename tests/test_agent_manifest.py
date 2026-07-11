import asyncio
import json
from pathlib import Path

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import load_agents, save_agents
from agent_gateway.ai.context.prompt import PromptAssembler
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.context.skills import SkillsManager
from agent_gateway.runtime.domain.models import AgentConfig
from agent_gateway.runtime.execution.agent_manifest import build_agent_template, validate_agent_config
from agent_gateway.runtime.execution.loop import AgentLoopRunner
from agent_gateway.runtime.execution.resilience import ProfileManager
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


class FakeResilienceRunner:
    def __init__(self) -> None:
        self.tools = type(
            "Tools",
            (),
            {"names": lambda self: ["bash", "read_file", "memory_write", "memory_search"]},
        )()
        self.last_allowed_tools = None
        self.last_system_prompt = ""
        self.last_model = ""
        self.last_runtime_context = {}
        self.response_messages = None
        self.response_tool_calls = []
        self.response_text = "ok"

    def run(self, system: str, messages, *, model: str, allowed_tools=None, runtime_context=None):
        self.last_system_prompt = system
        self.last_model = model
        self.last_allowed_tools = allowed_tools
        self.last_runtime_context = dict(runtime_context or {})
        response_messages = self.response_messages or messages + [{"role": "assistant", "content": "ok"}]
        return type(
            "Result",
            (),
            {
                "text": self.response_text,
                "stop_reason": "end_turn",
                "messages": response_messages,
                "tool_calls": list(self.response_tool_calls),
            },
        )()


def _build_tools() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        RegisteredTool(
            name="read_file",
            description="read",
            input_schema={"type": "object"},
            handler=lambda: "",
            tags=("filesystem", "read"),
        )
    )
    tools.register(
        RegisteredTool(
            name="memory_search",
            description="memory",
            input_schema={"type": "object"},
            handler=lambda: "",
            tags=("memory", "read"),
        )
    )
    tools.register(
        RegisteredTool(
            name="web_search",
            description="web",
            input_schema={"type": "object"},
            handler=lambda: "",
            tags=("web", "search", "network", "read"),
        )
    )
    return tools


def test_agent_manifest_round_trip(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    save_agents(
        settings,
        [
            AgentConfig(
                id="planner",
                name="Planner",
                model="deepseek-v4-pro",
                tool_policy_mode="allowlist",
                tool_names=("read_file", "memory_search"),
                memory_enabled=True,
                memory_auto_recall=False,
                memory_top_k=5,
                prompt_dir="agents/planner",
                use_global_prompt_files=False,
                skills_enabled=False,
            )
        ],
    )

    agents = load_agents(settings)

    assert len(agents) == 1
    assert agents[0].tool_policy_mode == "allowlist"
    assert agents[0].tool_names == ("read_file", "memory_search")
    assert agents[0].memory_auto_recall is False
    assert agents[0].memory_top_k == 5
    assert agents[0].prompt_dir == "agents/planner"
    assert agents[0].use_global_prompt_files is False
    assert agents[0].skills_enabled is False


def test_prompt_assembler_uses_agent_prompt_overrides(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "IDENTITY.md").write_text("Global identity", encoding="utf-8")
    (workspace / "SOUL.md").write_text("Global soul", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("Global memory", encoding="utf-8")
    agent_dir = workspace / "agents" / "planner"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("Planner identity", encoding="utf-8")
    (agent_dir / "SOUL.md").write_text("Planner soul", encoding="utf-8")

    memory_store = MemoryStore(workspace)
    skills_manager = SkillsManager(workspace)
    skills_manager.discover()
    assembler = PromptAssembler(workspace, memory_store=memory_store, skills_manager=skills_manager)

    agent = AgentConfig(
        id="planner",
        name="Planner",
        prompt_dir="agents/planner",
        use_global_prompt_files=False,
        memory_enabled=False,
        skills_enabled=False,
    )

    prompt = assembler.build(agent, mode="full", channel="cli", user_text="hello")

    assert "Planner identity" in prompt
    assert "Planner soul" in prompt
    assert "Global identity" not in prompt
    assert "## Memory" not in prompt
    assert "example-skill" not in prompt


def test_agent_loop_runner_applies_tool_policy(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        model_id="deepseek-v4-pro",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "IDENTITY.md").write_text("Identity", encoding="utf-8")

    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="planner",
            name="Planner",
            tool_policy_mode="allowlist",
            tool_names=("read_file", "memory_search"),
        )
    )
    sessions = SessionStore(settings.sessions_dir)
    resilience = FakeResilienceRunner()
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        PromptAssembler(settings.workspace_root),
        resilience,
    )

    reply = asyncio.run(
        runner.run_turn(
            "planner",
            "agent:planner:main",
            "hello",
            channel="cli",
        )
    )

    assert reply.text == "ok"
    assert resilience.last_model == "deepseek-v4-pro"
    assert resilience.last_allowed_tools == ["read_file", "memory_search"]
    assert resilience.last_runtime_context["memory_user_scope"] == "user:main"


def test_agent_loop_runner_excludes_disabled_tools(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        model_id="deepseek-v4-pro",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "IDENTITY.md").write_text("Identity", encoding="utf-8")

    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="planner",
            name="Planner",
            tool_policy_mode="allowlist",
            tool_names=("read_file", "memory_write", "memory_search"),
        )
    )
    sessions = SessionStore(settings.sessions_dir)
    resilience = FakeResilienceRunner()
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        PromptAssembler(settings.workspace_root),
        resilience,
    )

    reply = asyncio.run(
        runner.run_task_turn(
            agent_id="planner",
            session_key="system:cron:test",
            user_text="run cron",
            channel="cron",
            mode="minimal",
            disabled_tools=["memory_write"],
        )
    )

    assert reply.text == "ok"
    assert resilience.last_allowed_tools == ["read_file", "memory_search"]
    assert "memory_write" not in resilience.last_allowed_tools
    assert "disabled_tools: memory_write" in resilience.last_system_prompt


def test_agent_loop_runner_coerces_orchestration_start_reply(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        model_id="deepseek-v4-pro",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "IDENTITY.md").write_text("Identity", encoding="utf-8")
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            tool_policy_mode="allowlist",
            tool_names=("memory_search",),
        )
    )
    sessions = SessionStore(settings.sessions_dir)
    resilience = FakeResilienceRunner()
    resilience.response_tool_calls = ["start_agent_orchestration"]
    resilience.response_messages = [
        {"role": "user", "content": "晚餐吃了：半份蒸面、一个烤的大鸡腿"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_orch", "name": "start_agent_orchestration"}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_orch",
                    "content": json.dumps(
                        {
                            "type": "agent_orchestration_task",
                            "task_id": "task-1",
                            "status": "pending",
                            "run_id": "diet-run",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
        {
            "role": "assistant",
            "content": (
                "已启动，饮食助手记录后会更新今日汇总。稍等～\n\n"
                "## ✅ 晚餐已记录\n这里是模型脑补出来的汇总。"
            ),
        },
    ]
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        PromptAssembler(settings.workspace_root),
        resilience,
    )
    session_key = "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"

    reply = asyncio.run(
        runner.run_turn(
            "personal-secretary-zhanghaibo",
            session_key,
            "晚餐吃了：半份蒸面、一个烤的大鸡腿",
            channel="wework",
        )
    )

    assert reply.text == "已收到，正在处理。本轮结果生成后会继续推送。"
    assert reply.tool_calls == ["start_agent_orchestration"]
    saved = sessions.load_messages("personal-secretary-zhanghaibo", session_key)
    assert saved[-1]["content"] == [
        {"type": "text", "text": "已收到，正在处理。本轮结果生成后会继续推送。"}
    ]


def test_agent_loop_runner_does_not_mask_failed_orchestration_start(
    tmp_path: Path,
) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        model_id="deepseek-v4-pro",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "IDENTITY.md").write_text("Identity", encoding="utf-8")
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            tool_policy_mode="allowlist",
            tool_names=("memory_search",),
        )
    )
    sessions = SessionStore(settings.sessions_dir)
    resilience = FakeResilienceRunner()
    resilience.response_text = "协作任务启动失败：任务队列不可用。"
    resilience.response_tool_calls = ["start_agent_orchestration"]
    resilience.response_messages = [
        {"role": "user", "content": "调研 RabbitMQ 并生成报告"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_orch", "name": "start_agent_orchestration"}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_orch",
                    "content": "Error: task queue is not available in this runtime",
                }
            ],
        },
        {"role": "assistant", "content": "协作任务启动失败：任务队列不可用。"},
    ]
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        PromptAssembler(settings.workspace_root),
        resilience,
    )
    session_key = "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"

    reply = asyncio.run(
        runner.run_turn(
            "personal-secretary-zhanghaibo",
            session_key,
            "调研 RabbitMQ 并生成报告",
            channel="wework",
        )
    )

    assert reply.text == "协作任务启动失败：任务队列不可用。"
    saved = sessions.load_messages("personal-secretary-zhanghaibo", session_key)
    assert saved[-1]["content"] == [
        {"type": "text", "text": "协作任务启动失败：任务队列不可用。"}
    ]


def test_agent_loop_runner_ignores_legacy_handoff_request_from_history(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        model_id="deepseek-v4-pro",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "IDENTITY.md").write_text("Identity", encoding="utf-8")

    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            tool_policy_mode="allowlist",
            tool_names=("start_agent_orchestration",),
        )
    )
    sessions = SessionStore(settings.sessions_dir)
    session_key = "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"
    stale_history = [
        {"role": "user", "content": "查一下 DeepSeek API 是否正常"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_old",
                    "name": "request_agent_handoff",
                    "input": {
                        "target_agent_id": "research",
                        "handoff_prompt": "查询 DeepSeek API 状态。",
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_old",
                    "content": json.dumps(
                        {
                            "type": "agent_handoff_request",
                            "target_agent_id": "research",
                            "handoff_prompt": "查询 DeepSeek API 状态。",
                            "reason": "需要联网查询。",
                            "scope": "one-shot",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
    ]
    sessions.rewrite_messages(
        "personal-secretary-zhanghaibo",
        session_key,
        stale_history,
    )

    current_user_text = (
        "分析这个仓库是否适合引入 Gateway，并给我风险审查、采纳计划和正式报告："
        "https://github.com/Obiah-Z/smart-trip"
    )
    resilience = FakeResilienceRunner()
    resilience.response_messages = [
        *stale_history,
        {"role": "user", "content": current_user_text},
        {"role": "assistant", "content": "报告已生成。"},
    ]
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        PromptAssembler(settings.workspace_root),
        resilience,
    )

    reply = asyncio.run(
        runner.run_turn(
            "personal-secretary-zhanghaibo",
            session_key,
            current_user_text,
            channel="wework",
        )
    )

    assert not hasattr(reply, "handoff_request")
    assert resilience.last_runtime_context["session_key"] == session_key


def test_agent_manifest_validator_rejects_invalid_config() -> None:
    agent = AgentConfig(
        id="planner",
        name="Planner",
        tool_policy_mode="invalid-mode",
        tool_names=("missing-tool",),
        memory_top_k=99,
        prompt_dir="../escape",
    )

    issues = validate_agent_config(agent, _build_tools())

    assert any("tool_policy.mode" in issue for issue in issues)
    assert any("unknown tool names" in issue for issue in issues)
    assert any("memory_policy.top_k" in issue for issue in issues)
    assert any("prompt_policy.prompt_dir" in issue for issue in issues)


def test_agent_template_builder_uses_capability_tags() -> None:
    template = build_agent_template(
        "planner",
        name="Planner",
        capability_tags=["filesystem", "memory", "web"],
        tools=_build_tools(),
    )

    assert template.agent["tool_policy"]["mode"] == "allowlist"
    assert template.agent["tool_policy"]["tool_names"] == [
        "read_file",
        "memory_search",
        "web_search",
    ]
    assert "IDENTITY.md" in template.prompt_files
