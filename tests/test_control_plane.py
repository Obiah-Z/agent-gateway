import asyncio
import json
from pathlib import Path

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.domain.models import AgentConfig, Binding
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.execution.alerts_runtime import AlertsRuntime
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager
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
    failed_rows = control.list_deliveries(state="failed", include_text=True)

    assert stats["pending"] == 1
    assert stats["failed"] == 1
    assert pending["items"][0]["id"] == pending_id
    assert pending["items"][0]["text"] == ""
    assert pending["items"][0]["text_preview"] == "pending delivery text"
    assert failed_rows["items"][0]["id"] == failed_id
    assert failed_rows["items"][0]["text"] == "failed delivery text"

    assert control.retry_delivery(failed_id) is True
    assert control.delivery_stats()["failed"] == 0
    assert control.discard_delivery(pending_id, state="pending") is True


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
        delivery_queue=DeliveryQueue(tmp_path / "delivery"),
    )

    status = control.runtime_status()
    health = control.health_check()

    assert status["agents"]["count"] == 1
    assert status["profiles"]["available"] == 1
    assert status["channels"]["active"] == 1
    assert status["delivery"]["pending"] == 0
    assert status["cron"]["count"] == 1
    assert health["ok"] is True
    assert health["status"] == "ok"
    assert all(row["status"] == "ok" for row in health["checks"])


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
