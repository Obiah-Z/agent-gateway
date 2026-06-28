import asyncio
import json
from pathlib import Path

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
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.execution.alerts_runtime import AlertsRuntime
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.infra.redis_client import RedisHealth
from agent_gateway.runtime.infra.postgres_client import PostgresHealth
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry
from agent_gateway.runtime.state.postgres import PostgresWriteRepository


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
        self.updated_channels = None
        self.heartbeat = FakeHeartbeat()
        self.cron = FakeCron()

    def set_channels(self, channels: ChannelManager) -> None:
        self.updated_channels = channels


class FakeChannelRuntime:
    def __init__(self) -> None:
        self.restarted_with = None

    async def restart(self, channels: ChannelManager) -> None:
        self.restarted_with = channels

    def stats(self) -> dict[str, object]:
        return {
            "running": True,
            "global_queue_depth": 0,
            "global_queue_limit": 200,
            "lane_queue_limit": 20,
            "max_concurrent_lanes": 4,
            "active_lanes": 0,
            "running_tasks": 0,
            "lane_count": 0,
            "queued_messages": 0,
            "oldest_wait_seconds": 0.0,
            "lanes": [],
        }


class FakeTaskWorker:
    def stats(self) -> dict[str, object]:
        return {
            "running": True,
            "worker_id": "worker-a",
            "concurrency": 2,
            "registered_task_types": ["agent_inbound"],
            "queue": {"pending": 0, "running": 0},
            "broker": {},
            "session_locks": {"blocked_session_count": 0, "skip_count": 0, "last_blocked_sessions": []},
        }


class FakeRedisClient:
    def __init__(self, health: RedisHealth) -> None:
        self._health = health

    def health(self) -> RedisHealth:
        return self._health


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
                "ttl_seconds": 30,
                "renewed_at": 2.0,
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
                "ttl_seconds": 30,
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
        filters = dict(filters or {})
        for key, value in filters.items():
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
            if row["session_key"] != session_key:
                continue
            if owner_token and row.get("owner_token") != owner_token:
                continue
            row["state"] = "released"
            return True
        return False


class FakePostgresClient:
    def __init__(self, health: PostgresHealth) -> None:
        self._health = health

    def health(self) -> PostgresHealth:
        return self._health


class FakePostgresWriteRepository(PostgresWriteRepository):
    def __init__(self) -> None:
        super().__init__(url="postgresql://example", enabled=False)
        self.append_calls: list[tuple[str, dict[str, object]]] = []
        self.upsert_calls: list[tuple[str, dict[str, object]]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def append(self, table: str, row: dict[str, object]) -> dict[str, object]:
        self.append_calls.append((table, row))
        return row

    def upsert(self, table: str, row: dict[str, object]) -> dict[str, object]:
        self.upsert_calls.append((table, row))
        return row

    def delete(self, table: str, key: str) -> bool:
        self.delete_calls.append((table, key))
        return True


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


def _build_settings(tmp_path: Path) -> GatewaySettings:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    return settings


def test_control_plane_save_and_reload_bindings(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )

    control.add_binding(Binding(agent_id="helper", tier=4, match_key="channel", match_value="telegram"))
    saved = control.save_bindings()

    assert saved == 2
    payload = json.loads(settings.bindings_config_file.read_text(encoding="utf-8"))
    assert len(payload["bindings"]) == 2

    settings.bindings_config_file.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "agent_id": "reloaded",
                        "tier": 4,
                        "match_key": "channel",
                        "match_value": "cli",
                        "priority": 1,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    count = control.reload_bindings()

    assert count == 1
    current = control.list_bindings()
    assert len(current) == 1
    assert current[0].agent_id == "reloaded"


def test_control_plane_reload_agents_and_profiles(tmp_path: Path, monkeypatch) -> None:
    settings = _build_settings(tmp_path)
    settings.agents_config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "main",
                        "name": "Main Agent",
                        "personality": "direct",
                        "model": "deepseek-v4-pro",
                        "dm_scope": "per-peer",
                        "extra_system": "A",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings.profiles_config_file.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "primary",
                        "provider": "anthropic",
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "base_url_env": "ANTHROPIC_BASE_URL",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.test/anthropic")

    agents = AgentManager()
    profiles = ProfileManager(
        [
            AuthProfile(
                name="primary",
                provider="anthropic",
                api_key="old",
                base_url="https://old",
                cooldown_until=12.0,
                failure_reason="timeout",
                last_good_at=34.0,
            )
        ]
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=BindingTable(),
        profiles=profiles,
        channels=ChannelManager(),
    )

    loaded_agents = control.reload_agents()
    snapshot = control.reload_profiles()

    assert len(loaded_agents) == 1
    assert loaded_agents[0].model == "deepseek-v4-pro"
    assert snapshot[0]["has_key"] is True
    assert snapshot[0]["failure_reason"] == "timeout"
    assert snapshot[0]["last_good_at"] == 34.0


def test_control_plane_reload_channels_updates_runtime_and_autonomy(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.channels_config_file.write_text(
        json.dumps(
            {
                "channels": [
                    {
                        "channel": "cli",
                        "account_id": "cli-local",
                        "enabled": True,
                        "label": "CLI",
                        "token": "",
                        "config": {},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="dummy", account_id="old")]
    runtime = FakeChannelRuntime()
    autonomy = FakeAutonomy()
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=channels,
        autonomy=autonomy,
        channel_runtime=runtime,
    )

    result = asyncio.run(control.reload_channels())

    assert result == ["cli"]
    assert runtime.restarted_with is not None
    assert runtime.restarted_with.list_channels() == ["cli"]
    assert autonomy.updated_channels is control.channels
    assert control.channels.accounts[0].account_id == "cli-local"


def test_control_plane_lists_and_saves_runtime_state(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    profiles = ProfileManager(
        [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
    )

    assert control.list_agents()[0].id == "main"
    assert control.list_profiles()[0]["name"] == "primary"
    assert control.list_channels()[0]["account_id"] == "cli-local"

    assert control.save_agents() == 1
    assert control.save_profiles() == 1
    assert control.save_channels() == 1

    assert '"id": "main"' in settings.agents_config_file.read_text(encoding="utf-8")
    assert '"name": "primary"' in settings.profiles_config_file.read_text(encoding="utf-8")
    assert '"account_id": "cli-local"' in settings.channels_config_file.read_text(encoding="utf-8")
    assert control.get_source("agents")["agents"][0]["id"] == "main"


def test_control_plane_records_config_audit_when_state_repository_is_postgres_like(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=BindingTable(),
        profiles=ProfileManager(
            [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
        ),
        channels=ChannelManager(),
        state_repository=FakePostgresWriteRepository(),
    )

    control.save_agents()

    repo = control.state_repository
    assert isinstance(repo, FakePostgresWriteRepository)
    assert len(repo.append_calls) == 1
    table, row = repo.append_calls[0]
    assert table == "config_audits"
    assert row["entity_type"] == "agents"
    assert row["action"] == "save"
    assert row["after"]["count"] == 1


def test_control_plane_saves_config_to_postgres_before_local_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events: list[str] = []

    class OrderedFakePostgresWriteRepository(FakePostgresWriteRepository):
        def upsert(self, table: str, row: dict[str, object]) -> dict[str, object]:
            events.append(f"db:{table}")
            return super().upsert(table, row)

    def fake_save_agents(settings: GatewaySettings, agents: list[AgentConfig]) -> None:
        events.append("file:agents")

    settings = _build_settings(tmp_path)
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_write_repository=OrderedFakePostgresWriteRepository(),
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.execution.control_plane.save_agents",
        fake_save_agents,
    )

    control.save_agents()

    assert events[:2] == ["db:agents", "file:agents"]


def test_control_plane_uses_state_repository_for_read_views(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    metrics = MetricsStore(settings.metrics_dir, retention_days=2000)
    alerts = AlertStore(settings.alerts_dir, retention_days=2000)
    event_store = RuntimeEventStore(settings.events_dir, retention_days=2000)
    event_store.record(
        "tool.failed",
        status="error",
        component="tool",
        message="boom",
        correlation_id="corr-1",
        agent_id="main",
        metadata={"delivery_id": "delivery-1"},
    )
    event_store.record(
        "task.worker.completed",
        status="ok",
        component="task_worker",
        message="done",
        correlation_id="task-worker-1",
        agent_id="main",
        session_key="session-worker",
        metadata={
            "worker_id": "worker-a",
            "task_id": "task-worker-1",
            "task_type": "agent_inbound",
            "duration_seconds": 0.12,
        },
    )
    event_store.record(
        "session_lane.recovery.released",
        status="ok",
        component="session_lane_recovery",
        message="released",
        session_key="session-worker",
        metadata={
            "worker_id": "worker-a",
            "task_id": "task-worker-1",
            "owner_token": "worker-a:task-worker-1",
        },
    )
    memory_store = MemoryStore(settings.workspace_root)
    memory_store.write_memory("remember me", category="test")
    task_store = LocalTaskStore(tmp_path / "tasks")
    task_queue = LocalTaskQueue(task_store)
    task = task_queue.enqueue(
        task_type="cron",
        source="cron",
        agent_id="main",
        payload={"job_id": "health-check"},
    )
    repo = LocalStateReadRepository(
        sessions=SessionStore(settings.sessions_dir),
        tasks=task_store,
        events=event_store,
        metrics=metrics,
        alerts=alerts,
        memory=memory_store,
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repo,
    )

    events = control.tail_events(limit=5)
    executions = control.task_executions(limit=5, session_key="session-worker", worker_id="worker-a")
    recovery_events = control.lane_recovery_events(
        limit=5,
        session_key="session-worker",
        worker_id="worker-a",
    )
    errors = control.recent_errors(limit=5)
    memories = control.recent_memories(limit=5)
    tasks = control.get_task(task.id)

    assert events["configured"] is True
    assert events["count"] == 3
    assert executions["configured"] is True
    assert executions["count"] == 1
    assert executions["items"][0]["type"] == "task.worker.completed"
    assert executions["items"][0]["metadata"]["worker_id"] == "worker-a"
    assert recovery_events["configured"] is True
    assert recovery_events["count"] == 1
    assert recovery_events["items"][0]["type"] == "session_lane.recovery.released"
    assert errors["configured"] is True
    assert errors["count"] == 1
    assert memories["configured"] is True
    assert memories["count"] >= 1
    assert tasks["id"] == task.id


def test_control_plane_exposes_metrics_views(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    metrics = MetricsStore(settings.metrics_dir, retention_days=2000)
    metrics.record(
        runtime={"uptime_seconds": 10},
        delivery={"pending": 2, "failed": 0, "retry_ready": 1},
        lanes={"count": 1, "active": 1, "queued": 3, "max_queue_depth": 3},
        cron={"configured": True, "count": 2, "enabled": 1, "errored": 0},
        events={"errors_5m": 1, "rejected_5m": 0, "delivery_failed_5m": 0, "tool_failed_5m": 1, "cron_failed_5m": 0},
        profiles={"count": 2, "available": 1, "cooling_down": 1},
        timestamp=1_704_067_200.0,
    )
    metrics.record(
        runtime={"uptime_seconds": 20},
        delivery={"pending": 5, "failed": 1, "retry_ready": 2, "oldest_pending_age_seconds": 30},
        lanes={"count": 2, "active": 1, "queued": 4, "max_queue_depth": 4},
        cron={"configured": True, "count": 3, "enabled": 2, "errored": 1},
        events={"errors_5m": 3, "rejected_5m": 1, "delivery_failed_5m": 2, "tool_failed_5m": 1, "cron_failed_5m": 1},
        profiles={"count": 2, "available": 2, "cooling_down": 0},
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

    snapshot = control.metrics_snapshot()
    tail = control.metrics_tail(limit=10)
    summary = control.metrics_summary(limit=10)

    assert snapshot["configured"] is True
    assert snapshot["available"] is True
    assert snapshot["item"]["delivery"]["pending"] == 5
    assert tail["count"] == 2
    assert [row["delivery"]["pending"] for row in tail["items"]] == [2, 5]
    assert summary["available"] is True
    assert summary["count"] == 2
    assert summary["latest"]["delivery"]["failed"] == 1
    assert summary["delivery"]["max_pending"] == 5
    assert summary["lanes"]["max_queued"] == 4
    assert summary["events"]["max_errors_5m"] == 3
    assert summary["cron"]["max_errored"] == 1
    assert summary["profiles"]["max_available"] == 2


def test_control_plane_metrics_views_handle_missing_store(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )

    assert control.metrics_snapshot() == {"configured": False}
    assert control.metrics_tail(limit=5) == {"items": [], "count": 0, "configured": False, "limit": 5}
    assert control.metrics_summary(limit=5) == {"configured": False, "count": 0, "limit": 5}


def test_control_plane_exposes_alert_views(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
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

    active = control.active_alerts()
    history = control.alert_history(limit=10)

    assert active["configured"] is True
    assert active["count"] == 1
    assert active["items"][0]["rule_id"] == "delivery_failed_persisting"
    assert history["configured"] is True
    assert history["items"][-1]["event"] == "triggered"


def test_control_plane_can_set_and_remove_agent(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.agents_config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "main", "name": "Main", "personality": "", "model": "", "dm_scope": "per-peer", "extra_system": ""}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )
    control.reload_agents()

    agent = control.set_agent(
        agent_id="helper",
        name="Helper",
        model="deepseek-v4-pro",
        personality="pragmatic",
        tool_policy_mode="allowlist",
        tool_names=["read_file", "memory_search"],
        memory_enabled=True,
        memory_auto_recall=False,
        memory_top_k=4,
        prompt_dir="agents/helper",
        use_global_prompt_files=False,
        skills_enabled=False,
    )

    assert agent.id == "helper"
    assert agent.tool_policy_mode == "allowlist"
    assert agent.tool_names == ("read_file", "memory_search")
    assert agent.memory_auto_recall is False
    assert agent.prompt_dir == "agents/helper"
    assert control.agents.get("helper") is not None
    assert '"id": "helper"' in settings.agents_config_file.read_text(encoding="utf-8")
    assert control.remove_agent("helper") is True
    assert control.agents.get("helper") is None


def test_control_plane_remove_agent_rejects_referenced_or_last(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.agents_config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "main", "name": "Main"},
                    {"id": "helper", "name": "Helper"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bindings = BindingTable()
    bindings.add(Binding(agent_id="helper", tier=5, match_key="default", match_value="*"))
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )
    control.reload_agents()

    try:
        control.remove_agent("helper")
    except RuntimeError as exc:
        assert "still referenced by bindings" in str(exc)
    else:
        raise AssertionError("expected removal to be rejected")


def test_control_plane_can_set_and_remove_profile(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.profiles_config_file.write_text(
        json.dumps(
            {
                "profiles": [
                    {"name": "primary", "provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )
    control.reload_profiles()

    profile = control.set_profile(
        name="backup",
        provider="anthropic",
        api_key_env="BACKUP_API_KEY",
        base_url="https://backup.example/anthropic",
    )

    assert profile["name"] == "backup"
    assert "backup" in settings.profiles_config_file.read_text(encoding="utf-8")
    assert control.remove_profile("backup") is True


def test_control_plane_set_profile_rejects_mixed_secret_modes(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )

    try:
        control.set_profile(
            name="bad",
            api_key="literal",
            api_key_env="API_KEY_ENV",
        )
    except ValueError as exc:
        assert "mutually exclusive" in str(exc)
    else:
        raise AssertionError("expected mixed secret mode to fail")


def test_control_plane_can_set_and_remove_channel(tmp_path: Path, monkeypatch) -> None:
    for key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    settings = _build_settings(tmp_path)
    settings.channels_config_file.write_text(
        json.dumps(
            {
                "channels": [
                    {"channel": "cli", "account_id": "cli-local", "enabled": True, "label": "CLI", "token": "", "config": {}}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime = FakeChannelRuntime()
    autonomy = FakeAutonomy()
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        autonomy=autonomy,
        channel_runtime=runtime,
    )

    descriptor = asyncio.run(
        control.set_channel(
            channel="telegram",
            account_id="telegram-main",
            enabled=True,
            label="Telegram Bot",
            token_env="TELEGRAM_BOT_TOKEN",
            config={"allowed_chats": "1001,1002"},
        )
    )

    assert descriptor["channel"] == "telegram"
    assert runtime.restarted_with is not None
    assert "telegram-main" in settings.channels_config_file.read_text(encoding="utf-8")
    assert asyncio.run(control.remove_channel("telegram", "telegram-main")) is True


def test_control_plane_remove_channel_rejects_proactive_target(tmp_path: Path, monkeypatch) -> None:
    for key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        proactive_channel="cli",
        proactive_account_id="cli-local",
    )
    settings.ensure_directories()
    settings.channels_config_file.write_text(
        json.dumps(
            {
                "channels": [
                    {"channel": "cli", "account_id": "cli-local", "enabled": True, "label": "CLI", "token": "", "config": {}}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )

    try:
        asyncio.run(control.remove_channel("cli", "cli-local"))
    except RuntimeError as exc:
        assert "proactive channel account" in str(exc)
    else:
        raise AssertionError("expected proactive channel removal to fail")


def test_control_plane_lists_tool_capabilities(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        tools=_build_tools(),
    )

    capabilities = control.list_tool_capabilities()

    assert any(row["tag"] == "filesystem" and "read_file" in row["tools"] for row in capabilities)
    assert any(row["tag"] == "web" and "web_search" in row["tools"] for row in capabilities)


def test_control_plane_manages_delivery_queue(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    queue = DeliveryQueue(tmp_path / "delivery")
    pending_id = queue.enqueue(
        "feishu",
        "ou_user",
        "pending delivery text",
        {"account_id": "feishu-main", "kind": "reply"},
    )
    failed_id = queue.enqueue(
        "feishu",
        "ou_user",
        "failed delivery text",
        {"account_id": "feishu-main", "kind": "cron"},
    )
    failed = queue.get_pending(failed_id)
    assert failed is not None
    failed.retry_count = 5
    failed.last_error = "send failed"
    queue.move_to_failed(failed)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        delivery_queue=queue,
    )

    stats = control.delivery_stats()
    pending = control.list_deliveries(state="pending")
    all_rows = control.list_deliveries(state="all")
    failed_rows = control.list_deliveries(state="failed", include_text=True)

    assert stats["pending"] == 1
    assert stats["retrying"] == 0
    assert stats["failed"] == 1
    assert {row["state"] for row in all_rows["items"]} == {"failed", "pending"}
    assert pending["items"][0]["id"] == pending_id
    assert pending["items"][0]["text"] == ""
    assert pending["items"][0]["text_preview"] == "pending delivery text"
    assert failed_rows["items"][0]["id"] == failed_id
    assert failed_rows["items"][0]["text"] == "failed delivery text"

    assert control.retry_delivery(failed_id) is True
    assert control.delivery_stats()["failed"] == 0
    assert control.republish_deliveries()["published"] >= 1
    assert control.discard_delivery(pending_id, state="pending") is True


def test_control_plane_manages_task_queue(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    pending = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        agent_id="research",
        session_key="feishu:user-1",
        payload={"text": "/github-repo-analyzer https://github.com/example/repo"},
    )
    failed = queue.enqueue(
        task_type="cron",
        source="cron",
        agent_id="main",
        payload={"job_id": "health-check"},
    )
    queue.fail(failed.id, error="cron failed")
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        task_queue=queue,
    )

    listed = control.list_tasks(status="all")
    pending_rows = control.list_tasks(status="pending", include_payload=False)
    detail = control.get_task(pending.id)

    assert listed["count"] == 2
    assert pending_rows["count"] == 1
    assert "payload" not in pending_rows["items"][0]
    assert detail["payload"]["text"].startswith("/github-repo-analyzer")
    assert detail["payload_preview"].startswith("/github-repo-analyzer")
    assert control.cancel_task(pending.id) is True
    assert control.get_task(pending.id, include_payload=False)["status"] == "cancelled"
    assert control.retry_task(failed.id) is True
    assert control.get_task(failed.id, include_payload=False)["status"] == "retrying"
    assert control.cancel_task(failed.id) is True
    assert control.retry_task(pending.id) is True


def test_control_plane_formats_task_payload_preview_time(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="cron",
        source="scheduler",
        agent_id="research",
        session_key="system:cron:health-check",
        payload={"job_id": "health-check", "scheduled_at": 1782619200.6118104},
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        task_queue=queue,
    )

    detail = control.get_task(task.id, include_payload=False)

    assert detail["payload_preview"] == "任务 health-check · 调度时间 2026年06月28日 12时00分"
    assert "1782619200" not in detail["payload_preview"]
    assert "{'job_id'" not in detail["payload_preview"]


def test_control_plane_runtime_status_and_health_check(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    settings.proactive_channel = "cli"
    settings.proactive_account_id = "cli-local"
    settings.proactive_peer_id = "cli-user"
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    profiles = ProfileManager(
        [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
        autonomy=FakeAutonomy(),
        channel_runtime=FakeChannelRuntime(),
        delivery_queue=DeliveryQueue(tmp_path / "delivery"),
        redis_client=FakeRedisClient(
            RedisHealth(enabled=False, ok=True, url="redis://127.0.0.1:6379/0")
        ),
        postgres_client=FakePostgresClient(
            PostgresHealth(enabled=False, ok=True, url="postgresql://postgres:postgres@127.0.0.1:5432/postgres")
        ),
        state_repository=FakeLaneStateRepository(),
        task_worker=FakeTaskWorker(),
        task_queue=LocalTaskQueue(LocalTaskStore(tmp_path / "tasks")),
        event_store=RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl"),
    )

    status = control.runtime_status()
    health = control.health_check()

    assert status["agents"]["count"] == 1
    assert status["profiles"]["available"] == 1
    assert status["channels"]["active"] == 1
    assert status["delivery"]["pending"] == 0
    assert status["redis"]["enabled"] is False
    assert status["redis"]["ok"] is True
    assert status["postgres"]["enabled"] is False
    assert status["postgres"]["ok"] is True
    assert status["inbound"]["configured"] is True
    assert status["inbound"]["max_concurrent_lanes"] == 4
    assert status["tasks"]["persisted_lanes"]["configured"] is True
    assert status["tasks"]["persisted_lanes"]["count"] == 1
    assert status["tasks"]["persisted_lanes"]["stale_count"] == 1
    assert status["tasks"]["persisted_lanes"]["history_count"] == 1
    assert status["tasks"]["persisted_lanes"]["recovery_suggestion_count"] == 1
    assert status["tasks"]["persisted_lanes"]["items"][0]["worker_id"] == "worker-a"
    assert status["tasks"]["persisted_lanes"]["stale_items"][0]["session_key"] == "agent:feishu:user-1"
    assert status["tasks"]["persisted_lanes"]["history_items"][0]["event"] == "acquired"
    assert (
        status["tasks"]["persisted_lanes"]["recovery_suggestions"][0]["action"]
        == "release_session_lane"
    )
    assert status["tasks"]["persisted_lanes"]["recovery_plan"]["dry_run"] is True
    assert status["tasks"]["persisted_lanes"]["recovery_plan"]["action_count"] == 1
    assert status["tasks"]["persisted_lanes"]["recovery_execution"]["executed"] is False
    assert status["cron"]["count"] == 1
    assert health["ok"] is False
    assert health["status"] == "degraded"
    assert any(
        row["name"] == "tasks.session_lanes.stale" and row["status"] == "warning"
        for row in health["checks"]
    )
    assert control.event_store is not None
    assert control.event_store.tail(limit=10, event_type="session_lane.recovery.dry_run") == []


def test_control_plane_lists_session_lanes_with_filters(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["state"] = "released"
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
    )

    result = control.list_session_lanes(
        state="released",
        limit=20,
        session_key="agent:feishu:user-1",
        worker_id="worker-a",
        task_id="task-a",
    )

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["session_key"] == "agent:feishu:user-1"
    assert repository.calls[0] == (
        "session_lanes",
        20,
        {
            "state": "released",
            "session_key": "agent:feishu:user-1",
            "worker_id": "worker-a",
            "task_id": "task-a",
        },
    )


def test_control_plane_lists_session_lane_history_with_filters(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
    )

    result = control.list_session_lane_history(
        limit=10,
        session_key="agent:feishu:user-1",
        worker_id="worker-a",
        event="acquired",
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


def test_control_plane_suggests_recovery_for_stale_session_lanes(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
    )

    result = control.session_lane_recovery_suggestions(limit=10)

    assert result["configured"] is True
    assert result["count"] == 1
    assert result["items"][0]["action"] == "release_session_lane"
    assert result["items"][0]["release_params"] == {
        "session_key": "agent:feishu:user-1",
        "owner_token": "worker-a:task-a",
        "force": False,
        "reason": "stale lane recovery",
    }
    assert result["items"][0]["expired_seconds"] > 0
    assert repository.calls[0] == (
        "session_lanes",
        10,
        {
            "state": "owned",
            "session_key": "",
            "worker_id": "",
            "task_id": "",
        },
    )


def test_control_plane_skips_recovery_for_fresh_session_lanes(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 9999999999.0
    repository.rows[0]["ttl_seconds"] = 30
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
    )

    result = control.session_lane_recovery_suggestions(limit=10)

    assert result["configured"] is True
    assert result["count"] == 0
    assert result["items"] == []


def test_control_plane_plans_stale_session_lane_recovery_without_releasing(
    tmp_path: Path,
) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
        state_write_repository=repository,
    )

    result = control.plan_session_lane_recovery(limit=10)

    assert result["configured"] is True
    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["action_count"] == 1
    assert result["skipped_count"] == 0
    assert result["actions"][0]["method"] == "tasks.lanes.release"
    assert result["actions"][0]["params"] == {
        "session_key": "agent:feishu:user-1",
        "owner_token": "worker-a:task-a",
        "force": False,
        "reason": "stale lane recovery",
    }
    assert repository.releases == []


def test_control_plane_recovery_plan_skips_missing_owner_token(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["owner_token"] = ""
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
    )

    result = control.plan_session_lane_recovery(limit=10)

    assert result["candidate_count"] == 1
    assert result["action_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "missing owner_token"


def test_control_plane_recovery_execute_defaults_to_dry_run(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
        state_write_repository=repository,
        event_store=RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl"),
    )

    result = control.execute_session_lane_recovery(limit=10)

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["released_count"] == 0
    assert result["plan"]["action_count"] == 1
    assert repository.releases == []
    assert control.event_store is not None
    events = control.event_store.tail(limit=10, event_type="session_lane.recovery.dry_run")
    assert len(events) == 1
    assert events[0]["metadata"]["action_count"] == 1


def test_control_plane_recovery_execute_releases_stale_lanes(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 1.0
    repository.rows[0]["ttl_seconds"] = 1
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
        state_write_repository=repository,
        event_store=RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl"),
    )

    result = control.execute_session_lane_recovery(limit=10, execute=True)

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["released_count"] == 1
    assert result["failed_count"] == 0
    assert result["results"][0]["released"] is True
    assert repository.releases[0]["owner_token"] == "worker-a:task-a"
    assert repository.releases[0]["reason"] == "stale lane recovery"
    assert control.event_store is not None
    released_events = control.event_store.tail(limit=10, event_type="session_lane.recovery.released")
    completed_events = control.event_store.tail(limit=10, event_type="session_lane.recovery.completed")
    assert released_events[0]["session_key"] == "agent:feishu:user-1"
    assert completed_events[0]["metadata"]["released_count"] == 1
    recovery_events = control.lane_recovery_events(
        limit=10,
        session_key="agent:feishu:user-1",
        worker_id="worker-a",
    )
    assert recovery_events["count"] == 1
    assert recovery_events["items"][0]["type"] == "session_lane.recovery.released"


def test_control_plane_releases_only_stale_session_lane_by_default(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    repository.rows[0]["renewed_at"] = 100.0
    repository.rows[0]["ttl_seconds"] = 30
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
        state_write_repository=repository,
    )

    repository.rows[0]["renewed_at"] = 9999999999.0
    fresh = control.release_session_lane(
        session_key="agent:feishu:user-1",
        owner_token="worker-a:task-a",
    )
    stale_check = control._is_session_lane_stale(
        {"renewed_at": 100.0, "ttl_seconds": 30},
        now=131.0,
    )
    repository.rows[0]["renewed_at"] = 1.0
    stale = control.release_session_lane(
        session_key="agent:feishu:user-1",
        owner_token="worker-a:task-a",
        reason="worker expired",
    )

    assert fresh["released"] is False
    assert "not stale" in fresh["reason"]
    assert stale_check is True
    assert stale["released"] is True
    assert stale["stale"] is True
    assert repository.releases[0]["reason"] == "worker expired"


def test_control_plane_release_session_lane_rejects_owner_mismatch(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    repository = FakeLaneStateRepository()
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        state_repository=repository,
        state_write_repository=repository,
    )

    result = control.release_session_lane(
        session_key="agent:feishu:user-1",
        owner_token="other-worker:task",
        force=True,
    )

    assert result["released"] is False
    assert result["reason"] == "owner_token mismatch"
    assert repository.releases == []


def test_control_plane_health_check_reports_redis_failure(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    settings.proactive_channel = "cli"
    settings.proactive_account_id = "cli-local"
    settings.proactive_peer_id = "cli-user"
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    profiles = ProfileManager(
        [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
        autonomy=FakeAutonomy(),
        channel_runtime=FakeChannelRuntime(),
        delivery_queue=DeliveryQueue(tmp_path / "delivery"),
        redis_client=FakeRedisClient(
            RedisHealth(
                enabled=True,
                ok=False,
                url="redis://127.0.0.1:6379/0",
                error="connection refused",
            )
        ),
        postgres_client=FakePostgresClient(
            PostgresHealth(
                enabled=True,
                ok=False,
                url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
                error="connection refused",
            )
        ),
    )

    health = control.health_check()

    redis_check = next(row for row in health["checks"] if row["name"] == "redis.ping")
    postgres_check = next(row for row in health["checks"] if row["name"] == "postgres.ping")
    assert redis_check["status"] == "warning"
    assert "connection refused" in redis_check["message"]
    assert postgres_check["status"] == "warning"
    assert "connection refused" in postgres_check["message"]
    assert health["status"] == "degraded"


def test_control_plane_health_check_reports_postgres_schema_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _build_settings(tmp_path)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    settings.proactive_channel = "cli"
    settings.proactive_account_id = "cli-local"
    settings.proactive_peer_id = "cli-user"
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main", model="deepseek-v4-pro"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    profiles = ProfileManager(
        [AuthProfile(name="primary", provider="anthropic", api_key="k", base_url="https://base")]
    )

    class FakeSchemaResult:
        def to_dict(self):
            return {
                "ok": False,
                "missing_tables": ["memory_entries"],
                "missing_columns": {"tasks": ["payload"]},
                "type_mismatches": {"agents": {"updated_at": {"expected": "double precision", "actual": "text"}}},
            }

    def fake_check_postgres_schema(*, url: str, connect_timeout_seconds: float):
        return FakeSchemaResult()

    monkeypatch.setattr(
        "agent_gateway.runtime.execution.control_plane.check_postgres_schema",
        fake_check_postgres_schema,
    )
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profiles,
        channels=channels,
        autonomy=FakeAutonomy(),
        channel_runtime=FakeChannelRuntime(),
        delivery_queue=DeliveryQueue(tmp_path / "delivery"),
        postgres_client=FakePostgresClient(
            PostgresHealth(
                enabled=True,
                ok=True,
                url="postgresql://postgres:postgres@127.0.0.1:5432/postgres",
                latency_ms=1.0,
            )
        ),
    )

    status = control.runtime_status()
    health = control.health_check()

    assert status["postgres"]["schema"]["ok"] is False
    schema_check = next(row for row in health["checks"] if row["name"] == "postgres.schema")
    assert schema_check["status"] == "warning"
    assert "missing_tables=1" in schema_check["message"]
    assert "missing_columns=1" in schema_check["message"]
    assert "type_mismatches=1" in schema_check["message"]
    assert health["status"] == "degraded"


def test_control_plane_generates_agent_template(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        tools=_build_tools(),
    )

    result = control.generate_agent_template(
        agent_id="planner",
        capability_tags=["filesystem", "memory"],
        write_files=True,
    )

    assert result["agent"]["id"] == "planner"
    assert "read_file" in result["agent"]["tool_policy"]["tool_names"]
    assert "memory_search" in result["agent"]["tool_policy"]["tool_names"]
    assert "agents/planner/IDENTITY.md" in result["written_files"]


def test_control_plane_generates_web_agent_template(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    control = GatewayControlPlane(
        settings=settings,
        agents=AgentManager(),
        bindings=BindingTable(),
        profiles=ProfileManager([]),
        channels=ChannelManager(),
        tools=_build_tools(),
    )

    result = control.generate_agent_template(
        agent_id="research",
        capability_tags=["web", "memory"],
        write_files=False,
    )

    assert result["agent"]["tool_policy"]["mode"] == "allowlist"
    assert "web_search" in result["agent"]["tool_policy"]["tool_names"]
    assert "memory_search" in result["agent"]["tool_policy"]["tool_names"]
