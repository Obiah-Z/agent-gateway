import asyncio

from agent_gateway.agents import AgentManager
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.models import AgentConfig, Binding
from agent_gateway.router import BindingTable
from agent_gateway.runtime.control_plane import GatewayControlPlane
from agent_gateway.runtime.gateway_server import GatewayServer
from agent_gateway.runtime.resilience import AuthProfile, ProfileManager
from agent_gateway.sessions.store import SessionStore
from agent_gateway.tools.registry import RegisteredTool, ToolRegistry


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
    return tools


def test_gateway_server_exposes_control_plane_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    profiles = ProfileManager(
        [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
    )
    channels = ChannelManager()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    profiles_result = asyncio.run(server._m_profiles_list({}))
    channels_result = asyncio.run(server._m_channels_list({}))
    source_result = asyncio.run(server._m_config_source({"kind": "bindings"}))

    assert profiles_result[0]["name"] == "primary"
    assert channels_result == []
    assert source_result == {"bindings": []}


def test_gateway_server_mutation_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    settings.agents_config_file.write_text(
        '{"agents":[{"id":"main","name":"Main","personality":"","model":"","dm_scope":"per-peer","extra_system":""}]}',
        encoding="utf-8",
    )
    settings.profiles_config_file.write_text(
        '{"profiles":[{"name":"primary","provider":"anthropic","api_key_env":"ANTHROPIC_API_KEY"}]}',
        encoding="utf-8",
    )
    settings.channels_config_file.write_text(
        '{"channels":[{"channel":"cli","account_id":"cli-local","enabled":true,"label":"CLI","token":"","config":{}}]}',
        encoding="utf-8",
    )

    agents = AgentManager()
    bindings = BindingTable()
    profiles = ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")])
    channels = ChannelManager()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
    )
    control.reload_agents()
    control.reload_profiles()
    asyncio.run(control.reload_channels())

    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    agent_result = asyncio.run(server._m_agents_set({"id": "helper", "name": "Helper"}))
    profile_result = asyncio.run(server._m_profiles_set({"name": "backup", "api_key_env": "BACKUP_API_KEY"}))
    channel_result = asyncio.run(
        server._m_channels_set(
            {
                "channel": "telegram",
                "account_id": "telegram-main",
                "enabled": False,
                "label": "Telegram Bot",
                "config": {"allowed_chats": "1001"},
            }
        )
    )

    assert agent_result["agent"]["id"] == "helper"
    assert profile_result["profile"]["name"] == "backup"
    assert channel_result["channel_account"]["account_id"] == "telegram-main"


def test_gateway_server_agent_manifest_fields(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    settings.agents_config_file.write_text(
        '{"agents":[{"id":"main","name":"Main","personality":"","model":"","dm_scope":"per-peer","extra_system":""}]}',
        encoding="utf-8",
    )
    agents = AgentManager()
    bindings = BindingTable()
    profiles = ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")])
    channels = ChannelManager()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
    )
    control.reload_agents()

    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(
        server._m_agents_set(
            {
                "id": "planner",
                "name": "Planner",
                "tool_policy_mode": "allowlist",
                "tool_names": ["read_file"],
                "memory_enabled": True,
                "memory_auto_recall": False,
                "memory_top_k": 5,
                "prompt_dir": "agents/planner",
                "use_global_prompt_files": False,
                "skills_enabled": False,
            }
        )
    )

    assert result["agent"]["tool_policy_mode"] == "allowlist"
    assert result["agent"]["tool_names"] == ["read_file"]
    assert result["agent"]["memory_auto_recall"] is False
    assert result["agent"]["prompt_dir"] == "agents/planner"


def test_gateway_server_exposes_agent_capabilities_and_template(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")]),
        channels=ChannelManager(),
        tools=_build_tools(),
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    capabilities = asyncio.run(server._m_agents_capabilities({}))
    template = asyncio.run(
        server._m_agents_template(
            {
                "id": "planner",
                "capability_tags": ["filesystem", "memory"],
                "write_files": False,
            }
        )
    )

    assert any(row["tag"] == "filesystem" for row in capabilities)
    assert template["agent"]["id"] == "planner"
    assert "read_file" in template["agent"]["tool_policy"]["tool_names"]
