import asyncio

from websockets.exceptions import ConnectionClosedError

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.tasks.queue import LocalTaskQueue
from agent_gateway.runtime.tasks.store import LocalTaskStore
from agent_gateway.runtime.domain.models import AgentConfig, Binding
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.gateways.control.websocket_server import GatewayServer
from agent_gateway.runtime.execution.alerts_runtime import AlertsRuntime
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


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


class FakeLaneStateRepository:
    def __init__(self) -> None:
        self.calls = []
        self.releases = []
        self.rows = [
            {
                "session_key": "agent:feishu:user-1",
                "lane_key": "gateway:lock:agent:feishu:user-1",
                "worker_id": "worker-a",
                "task_id": "task-a",
                "owner_token": "worker-a:task-a",
                "state": "owned",
                "ttl_seconds": 1,
                "renewed_at": 1.0,
            }
        ]
        self.events = [
            {
                "id": "lane-event-1",
                "session_key": "agent:feishu:user-1",
                "lane_key": "gateway:lock:agent:feishu:user-1",
                "worker_id": "worker-a",
                "task_id": "task-a",
                "owner_token": "worker-a:task-a",
                "event": "acquired",
                "ttl_seconds": 1,
                "occurred_at": 1.0,
            }
        ]

    def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
        del cursor
        self.calls.append((table, limit, dict(filters or {})))
        if table == "session_lanes":
            rows = list(self.rows)
        elif table == "session_lane_events":
            rows = list(self.events)
        else:
            return []
        for key, value in dict(filters or {}).items():
            if value:
                rows = [row for row in rows if str(row.get(key, "")) == str(value)]
        return rows[:limit]

    def get(self, table: str, key: str):
        del table, key
        return None

    def release_session_lane(
        self,
        session_key: str,
        *,
        owner_token: str = "",
        reason: str = "manual release",
        now: float = 0.0,
    ) -> bool:
        self.releases.append(
            {
                "session_key": session_key,
                "owner_token": owner_token,
                "reason": reason,
                "now": now,
            }
        )
        for row in self.rows:
            if row["session_key"] == session_key and (
                not owner_token or row.get("owner_token") == owner_token
            ):
                row["state"] = "released"
                return True
        return False


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


def test_gateway_server_sessions_use_state_repository(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    sessions = SessionStore(settings.sessions_dir)
    sessions.append_message(
        agent_id="main",
        session_key="session-1",
        role="user",
        content="hello",
    )
    state_repository = LocalStateReadRepository(
        sessions=sessions,
        tasks=LocalTaskStore(tmp_path / "tasks"),
        events=RuntimeEventStore(settings.data_dir / "events" / "runtime-events.jsonl"),
        metrics=MetricsStore(settings.data_dir / "metrics" / "metrics.jsonl"),
        alerts=AlertStore(settings.data_dir / "alerts" / "alerts.jsonl"),
        memory=MemoryStore(settings.workspace_root),
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        state_repository=state_repository,
    )

    result = asyncio.run(server._m_sessions({"agent_id": "main"}))

    assert result["session-1"] == 1


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
    event_store.record(
        "task.worker.completed",
        status="ok",
        component="task_worker",
        message="worker done",
        correlation_id="task-1",
        agent_id="main",
        session_key="session-1",
        metadata={"worker_id": "worker-a", "task_id": "task-1", "task_type": "agent_inbound"},
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
    executions = asyncio.run(
        server._m_tasks_executions(
            {
                "limit": 10,
                "task_id": "task-1",
                "session_key": "session-1",
                "worker_id": "worker-a",
            }
        )
    )
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
    assert [event["type"] for event in events["items"]] == [
        "route.resolved",
        "delivery.failed",
        "task.worker.completed",
    ]
    assert [event["type"] for event in errors["items"]] == ["delivery.failed"]
    assert [event["type"] for event in executions["items"]] == ["task.worker.completed"]
    assert executions["items"][0]["metadata"]["worker_id"] == "worker-a"
    assert [event["type"] for event in filtered_events["items"]] == ["delivery.failed"]
    assert [event["type"] for event in filtered_errors["items"]] == ["delivery.failed"]


def test_gateway_server_exposes_recent_memory_writes(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    memory_dir = settings.workspace_root / "memory" / "daily"
    memory_dir.mkdir(parents=True)
    (memory_dir / "2026-06-18.jsonl").write_text(
        (
            '{"ts":"2026-06-18T12:00:00+00:00",'
            '"category":"general","content":"cron should not write this often"}\n'
        ),
        encoding="utf-8",
    )
    agents = AgentManager()
    bindings = BindingTable()
    profiles = ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")])
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=ChannelManager(),
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(server._m_memory_recent({"limit": 10}))

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["content"] == "cron should not write this often"


def test_gateway_server_exposes_metrics_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    metrics = MetricsStore(settings.metrics_dir, retention_days=2000)
    metrics.record(
        delivery={"pending": 1, "failed": 0},
        lanes={"queued": 2},
        events={"errors_5m": 1},
        timestamp=1_704_067_200.0,
    )
    metrics.record(
        delivery={"pending": 4, "failed": 1},
        lanes={"queued": 5},
        events={"errors_5m": 3},
        timestamp=1_704_067_260.0,
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        metrics_store=metrics,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    snapshot = asyncio.run(server._m_metrics_snapshot({}))
    tail = asyncio.run(server._m_metrics_tail({"limit": 10}))
    summary = asyncio.run(server._m_metrics_summary({"limit": 10}))

    assert snapshot["available"] is True
    assert snapshot["item"]["delivery"]["pending"] == 4
    assert tail["count"] == 2
    assert summary["delivery"]["max_pending"] == 4
    assert summary["lanes"]["max_queued"] == 5
    assert summary["events"]["max_errors_5m"] == 3


def test_gateway_server_exposes_alert_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    metrics = MetricsStore(settings.metrics_dir, retention_days=2000)
    alerts = AlertStore(settings.alerts_dir, retention_days=2000)
    runtime = AlertsRuntime(metrics_store=metrics, alert_store=alerts, interval_seconds=60)
    metrics.record(delivery={"failed": 1}, lanes={"queued": 0}, profiles={"available": 1})
    runtime.evaluate_once()
    metrics.record(delivery={"failed": 2}, lanes={"queued": 0}, profiles={"available": 1})
    runtime.evaluate_once()

    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        alert_store=alerts,
        alerts_runtime=runtime,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    active = asyncio.run(server._m_alerts_active({}))
    history = asyncio.run(server._m_alerts_history({"limit": 10}))

    assert active["count"] == 1
    assert active["items"][0]["rule_id"] == "delivery_failed_persisting"
    assert history["count"] >= 1


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
    republished = asyncio.run(server._m_delivery_republish({}))
    flushed = asyncio.run(server._m_delivery_flush({"rounds": 2}))
    discarded = asyncio.run(server._m_delivery_discard({"delivery_id": delivery_id}))

    assert stats["pending"] == 1
    assert listed["items"][0]["text"] == "delivery body"
    assert retried == {"ok": True}
    assert republished["published"] == 1
    assert runtime.flush_calls == 2
    assert flushed["after"]["pending"] == 1
    assert discarded == {"ok": True}


def test_gateway_server_exposes_task_control_methods(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    pending = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        agent_id="research",
        payload={"text": "/space-advisor 服务器空间巡检"},
    )
    failed = queue.enqueue(task_type="cron", source="cron", payload={"job_id": "health-check"})
    queue.fail(failed.id, error="failed once")
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        task_queue=queue,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": AgentManager(), "bindings": BindingTable()})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    listed = asyncio.run(server._m_tasks_list({"status": "all"}))
    detail = asyncio.run(server._m_tasks_get({"task_id": pending.id}))
    cancelled = asyncio.run(server._m_tasks_cancel({"task_id": pending.id}))
    retried = asyncio.run(server._m_tasks_retry({"task_id": failed.id}))

    assert listed["count"] == 2
    assert detail["payload"]["text"].startswith("/space-advisor")
    assert cancelled == {"ok": True}
    assert retried == {"ok": True}


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


def test_gateway_server_exposes_session_lane_list(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    agents = AgentManager()
    bindings = BindingTable()
    channels = ChannelManager()
    repository = FakeLaneStateRepository()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=channels,
        state_repository=repository,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(
        server._m_tasks_lanes(
            {
                "state": "owned",
                "limit": 10,
                "session_key": "agent:feishu:user-1",
                "worker_id": "worker-a",
            }
        )
    )

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["worker_id"] == "worker-a"
    assert repository.calls[0] == (
        "session_lanes",
        10,
        {
            "state": "owned",
            "session_key": "agent:feishu:user-1",
            "worker_id": "worker-a",
            "task_id": "",
        },
    )


def test_gateway_server_exposes_session_lane_history(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    agents = AgentManager()
    bindings = BindingTable()
    channels = ChannelManager()
    repository = FakeLaneStateRepository()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=channels,
        state_repository=repository,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(
        server._m_tasks_lanes_history(
            {
                "limit": 10,
                "session_key": "agent:feishu:user-1",
                "worker_id": "worker-a",
                "event": "acquired",
            }
        )
    )

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["event"] == "acquired"
    assert repository.calls[0] == (
        "session_lane_events",
        10,
        {
            "session_key": "agent:feishu:user-1",
            "worker_id": "worker-a",
            "task_id": "",
            "event": "acquired",
        },
    )


def test_gateway_server_exposes_session_lane_recovery_suggestions(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    agents = AgentManager()
    bindings = BindingTable()
    channels = ChannelManager()
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=channels,
        state_repository=repository,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(server._m_tasks_lanes_recovery({"limit": 10}))

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["action"] == "release_session_lane"
    assert result["items"][0]["release_params"]["session_key"] == "agent:feishu:user-1"


def test_gateway_server_releases_session_lane(tmp_path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    agents = AgentManager()
    bindings = BindingTable()
    channels = ChannelManager()
    repository = FakeLaneStateRepository()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=channels,
        state_repository=repository,
        state_write_repository=repository,
    )
    server = GatewayServer(
        host="127.0.0.1",
        port=8765,
        dispatcher=type("Dispatcher", (), {"agents": agents, "bindings": bindings})(),
        sessions=SessionStore(settings.sessions_dir),
        control_plane=control,
    )

    result = asyncio.run(
        server._m_tasks_lanes_release(
            {
                "session_key": "agent:feishu:user-1",
                "owner_token": "worker-a:task-a",
                "reason": "worker expired",
            }
        )
    )

    assert result["released"] is True
    assert result["stale"] is True
    assert repository.releases[0]["reason"] == "worker expired"
