import asyncio
import json
import time
from pathlib import Path

from agent_gateway.channels.base import Channel, ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.models import AgentReply, OutboundMessage, ProactiveTarget
from agent_gateway.news.models import NewsItem, NewsSourceConfig
from agent_gateway.runtime.autonomy import CronService, HeartbeatService


class DummyChannel(Channel):
    name = "cli"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def receive(self):
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        self.sent.append(outbound.text)
        return True


class FakeDispatcher:
    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.background_prompts: list[str] = []
        self.deliveries: list[dict[str, object]] = []
        self.command_queue = type("Queue", (), {"stats": lambda self: {}})()

    async def dispatch_background(
        self,
        *,
        agent_id: str,
        session_key: str,
        prompt: str,
        channel: str,
        mode: str = "minimal",
        lane_name: str = "",
    ) -> AgentReply:
        self.background_prompts.append(prompt)
        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=self.reply_text,
            stop_reason="end_turn",
            tool_calls=[],
        )

    async def deliver_text(
        self,
        channels: ChannelManager,
        target: ProactiveTarget,
        text: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        self.deliveries.append({"target": target, "text": text, "metadata": metadata or {}})
        channel = channels.get(target.channel, target.account_id)
        assert channel is not None
        return channel.send(OutboundMessage(channel=target.channel, to=target.peer_id, text=text))


def _build_channel_manager() -> tuple[ChannelManager, DummyChannel]:
    manager = ChannelManager()
    channel = DummyChannel()
    manager.register(channel, ChannelAccount(channel="cli", account_id="cli-local"))
    return manager, channel


def test_heartbeat_trigger_delivers_message(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("Check follow-ups.", encoding="utf-8")
    settings = GatewaySettings(
        workspace_root=workspace,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        proactive_channel="cli",
        proactive_account_id="cli-local",
        proactive_peer_id="cli-user",
        proactive_agent_id="main",
    )
    settings.ensure_directories()
    manager, channel = _build_channel_manager()
    heartbeat = HeartbeatService(
        settings,
        FakeDispatcher("Please follow up with the user."),
        manager,
        ProactiveTarget("cli", "cli-local", "cli-user", "main"),
    )

    result = asyncio.run(heartbeat.trigger())

    assert "delivered" in result
    assert channel.sent == ["Please follow up with the user."]


def test_cron_service_runs_system_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cron_payload = {
        "jobs": [
            {
                "id": "system-ping",
                "name": "System Ping",
                "enabled": True,
                "schedule": {"kind": "every", "every_seconds": 1, "anchor": "2026-01-01T00:00:00+00:00"},
                "payload": {"kind": "system_event", "text": "Ping"},
                "delete_after_run": False,
            }
        ]
    }
    (workspace / "CRON.json").write_text(json.dumps(cron_payload), encoding="utf-8")
    settings = GatewaySettings(
        workspace_root=workspace,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        proactive_channel="cli",
        proactive_account_id="cli-local",
        proactive_peer_id="cli-user",
        proactive_agent_id="main",
    )
    settings.ensure_directories()
    manager, channel = _build_channel_manager()
    cron = CronService(
        settings,
        FakeDispatcher("unused"),
        manager,
        ProactiveTarget("cli", "cli-local", "cli-user", "main"),
    )

    job = cron.jobs[0]
    asyncio.run(cron._run_job(job, time.time()))

    assert channel.sent == ["[System Ping] Ping"]


def test_cron_service_runs_agent_news_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent-news-sources.json").write_text(
        json.dumps({"sources": [{"id": "fake", "type": "github_releases"}]}),
        encoding="utf-8",
    )
    cron_payload = {
        "jobs": [
            {
                "id": "agent-news-digest",
                "name": "AI Agent 每日简报",
                "enabled": True,
                "schedule": {
                    "kind": "every",
                    "every_seconds": 1,
                    "anchor": "2026-01-01T00:00:00+00:00",
                },
                "payload": {
                    "kind": "agent_news_digest",
                    "sources_file": "agent-news-sources.json",
                    "lookback_hours": 24,
                    "max_items": 6,
                },
                "delete_after_run": False,
            }
        ]
    }
    (workspace / "CRON.json").write_text(json.dumps(cron_payload), encoding="utf-8")
    source = NewsSourceConfig(id="fake", type="github_releases")
    item = NewsItem.build(
        source=source,
        title="LangGraph release",
        url="https://github.com/langchain-ai/langgraph/releases/tag/v1",
        published_at="2026-06-15T00:00:00Z",
        summary="Release summary",
    )
    marked: list[NewsItem] = []

    class FakeCollector:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def collect(self, **kwargs):
            return type("Result", (), {"items": [item], "errors": []})()

        def close(self) -> None:
            pass

    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def mark_seen(self, items: list[NewsItem]) -> None:
            marked.extend(items)

    monkeypatch.setattr("agent_gateway.runtime.autonomy.NewsCollector", FakeCollector)
    monkeypatch.setattr("agent_gateway.runtime.autonomy.NewsDigestStore", FakeStore)
    settings = GatewaySettings(
        workspace_root=workspace,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        proactive_channel="cli",
        proactive_account_id="cli-local",
        proactive_peer_id="cli-user",
        proactive_agent_id="research",
    )
    settings.ensure_directories()
    manager, channel = _build_channel_manager()
    dispatcher = FakeDispatcher("整理后的 AI Agent 简报")
    cron = CronService(
        settings,
        dispatcher,
        manager,
        ProactiveTarget("cli", "cli-local", "cli-user", "research"),
    )

    asyncio.run(cron._run_job(cron.jobs[0], time.time()))

    assert "LangGraph release" in dispatcher.background_prompts[0]
    assert channel.sent == ["[AI Agent 每日简报] 整理后的 AI Agent 简报"]
    assert marked == []
    delivery = dispatcher.deliveries[0]
    metadata = delivery["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["cron_payload_kind"] == "agent_news_digest"
    assert metadata["news_digest_items"] == [item.to_dict()]

    cron.on_delivery_success(type("Entry", (), {"metadata": metadata})())

    assert marked == [item]
