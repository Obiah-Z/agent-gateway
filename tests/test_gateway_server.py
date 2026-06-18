import asyncio

from websockets.exceptions import ConnectionClosedError

from agent_gateway.agents import AgentManager
from agent_gateway.channels.base import ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.models import AgentConfig, Binding
from agent_gateway.router import BindingTable
from agent_gateway.application.control_plane import GatewayControlPlane
from agent_gateway.interfaces.websocket.server import GatewayServer
from agent_gateway.observability.events import RuntimeEventStore
from agent_gateway.application.resilience import AuthProfile, ProfileManager
from agent_gateway.sessions.store import SessionStore
from agent_gateway.tools.registry import RegisteredTool, ToolRegistry


class FakeHeartbeat:
    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "running": False,
            "should_run": True,
            "reason": "all checks passed",
            "last_run": "never",
            "next_in_seconds": 60,
        }


class FakeCron:
    def list_jobs(self) -> list[dict[str, object]]:
        return [
            {
                "id": "agent-news-digest",
                "name": "AI Agent 每日简报",
                "enabled": True,
                "kind": "cron",
                "errors": 0,
                "last_run": "never",
                "next_run": "n/a",
                "next_in": None,
            }
        ]


class FakeAutonomy:
    def __init__(self) -> None:
        self.heartbeat = FakeHeartbeat()
        self.cron = FakeCron()


class FakeDeliveryRuntime:
    def __init__(self, queue: DeliveryQueue) -> None:
        self.queue = queue
        self.flush_calls = 0

    def pending_count(self) -> int:
        return len(self.queue.pending_entries())

    async def flush_once(self) -> None:
        self.flush_calls += 1


class DisconnectingWebSocket:
    def __init__(self) -> None:
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return '{"jsonrpc":"2.0","id":1,"method":"status","params":{}}'

    async def send(self, payload: str) -> None:
        del payload
        raise ConnectionClosedError(None, None)


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


def test_gateway_server_exposes_event_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    profiles = ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")])
    event_store = RuntimeEventStore(settings.data_dir / "events" / "runtime-events.jsonl")
    event_store.record(
        "route.resolved",
        status="ok",
        component="dispatcher",
        message="resolved",
        correlation_id="corr-route",
        agent_id="main",
    )
    event_store.record(
        "delivery.failed",
        status="failed",
        component="delivery",
        message="failed",
        correlation_id="corr-delivery",
        error="channel unavailable",
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=ChannelManager(),
        event_store=event_store,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    events = asyncio.run(server._m_events_tail({"limit": 10}))
    errors = asyncio.run(server._m_errors_recent({"limit": 10}))
    filtered_events = asyncio.run(
        server._m_events_tail(
            {
                "limit": 10,
                "component": "delivery",
                "correlation_id": "corr-delivery",
            }
        )
    )
    filtered_errors = asyncio.run(
        server._m_errors_recent(
            {
                "limit": 10,
                "component": "delivery",
                "correlation_id": "corr-delivery",
            }
        )
    )

    assert events["configured"] is True
    assert [event["type"] for event in events["items"]] == ["route.resolved", "delivery.failed"]
    assert [event["type"] for event in errors["items"]] == ["delivery.failed"]
    assert [event["type"] for event in filtered_events["items"]] == ["delivery.failed"]
    assert [event["type"] for event in filtered_errors["items"]] == ["delivery.failed"]


def test_gateway_server_ignores_websocket_disconnect_during_send(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
    )

    asyncio.run(server._handle(DisconnectingWebSocket()))


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


def test_gateway_server_exposes_delivery_control_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    queue = DeliveryQueue(tmp_path / "delivery")
    delivery_id = queue.enqueue(
        "cli",
        "peer-1",
        "delivery body",
        {"account_id": "cli-local", "kind": "reply"},
    )
    runtime = FakeDeliveryRuntime(queue)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        delivery_queue=queue,
        delivery_runtime=runtime,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    stats = asyncio.run(server._m_delivery_stats({}))
    listed = asyncio.run(server._m_delivery_list({"include_text": True}))
    retried = asyncio.run(server._m_delivery_retry({"delivery_id": delivery_id}))
    flushed = asyncio.run(server._m_delivery_flush({"rounds": 2}))
    discarded = asyncio.run(server._m_delivery_discard({"delivery_id": delivery_id}))

    assert stats["pending"] == 1
    assert listed["items"][0]["text"] == "delivery body"
    assert retried == {"ok": True}
    assert runtime.flush_calls == 2
    assert flushed["after"]["pending"] == 1
    assert discarded == {"ok": True}


def test_gateway_server_exposes_runtime_status_and_health_check(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        proactive_channel="cli",
        proactive_account_id="cli-local",
        proactive_peer_id="cli-user",
    )
    settings.ensure_directories()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    queue = DeliveryQueue(tmp_path / "delivery")
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")]),
        channels=channels,
        autonomy=FakeAutonomy(),
        delivery_queue=queue,
        delivery_runtime=FakeDeliveryRuntime(queue),
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    status = asyncio.run(server._m_runtime_status({}))
    health = asyncio.run(server._m_health_check({}))

    assert status["server"]["running"] is False
    assert status["agents"]["count"] == 1
    assert status["delivery"]["pending"] == 0
    assert health["status"] == "degraded"
    assert any(row["name"] == "server.running" for row in health["checks"])
