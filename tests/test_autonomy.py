import asyncio
import json
import time
from pathlib import Path

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.domain.models import AgentReply, OutboundMessage, ProactiveTarget
from agent_gateway.ai.news.models import NewsItem, NewsSourceConfig
from agent_gateway.runtime.execution.autonomy import CronService, HeartbeatService


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
        self.background_calls: list[dict[str, object]] = []
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
        disabled_tools: list[str] | None = None,
        correlation_id: str = "",
    ) -> AgentReply:
        self.background_prompts.append(prompt)
        self.background_calls.append(
            {
                "agent_id": agent_id,
                "session_key": session_key,
                "channel": channel,
                "mode": mode,
                "lane_name": lane_name,
                "disabled_tools": disabled_tools or [],
                "correlation_id": correlation_id,
            }
        )
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

    monkeypatch.setattr("agent_gateway.runtime.execution.autonomy.NewsCollector", FakeCollector)
    monkeypatch.setattr("agent_gateway.runtime.execution.autonomy.NewsDigestStore", FakeStore)
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


def test_cron_service_runs_github_skill_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "github-skill-sources.json").write_text(
        json.dumps({"sources": [{"id": "fake", "type": "github_search_repositories"}]}),
        encoding="utf-8",
    )
    cron_payload = {
        "jobs": [
            {
                "id": "github-skill-digest",
                "name": "GitHub 热门 Skill 发现",
                "enabled": True,
                "schedule": {
                    "kind": "every",
                    "every_seconds": 1,
                    "anchor": "2026-01-01T00:00:00+00:00",
                },
                "payload": {
                    "kind": "github_skill_digest",
                    "sources_file": "github-skill-sources.json",
                    "lookback_hours": 168,
                    "max_items": 6,
                },
                "delete_after_run": False,
            }
        ]
    }
    (workspace / "CRON.json").write_text(json.dumps(cron_payload), encoding="utf-8")
    source = NewsSourceConfig(id="fake", type="github_search_repositories")
    item = NewsItem.build(
        source=source,
        title="owner/agent-skills",
        url="https://github.com/owner/agent-skills",
        published_at="2026-06-20T00:00:00Z",
        summary="Reusable skills",
        metadata={"stars": 1000, "forks": 70, "language": "Python", "topics": ["agent"]},
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

    monkeypatch.setattr("agent_gateway.runtime.execution.autonomy.NewsCollector", FakeCollector)
    monkeypatch.setattr("agent_gateway.runtime.execution.autonomy.NewsDigestStore", FakeStore)
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
    dispatcher = FakeDispatcher("整理后的 GitHub Skill 简报")
    cron = CronService(
        settings,
        dispatcher,
        manager,
        ProactiveTarget("cli", "cli-local", "cli-user", "research"),
    )

    asyncio.run(cron._run_job(cron.jobs[0], time.time()))

    assert "热门 Skill 发现" in dispatcher.background_prompts[0]
    assert "owner/agent-skills" in dispatcher.background_prompts[0]
    assert channel.sent == ["[GitHub 热门 Skill 发现] 整理后的 GitHub Skill 简报"]
    delivery = dispatcher.deliveries[0]
    metadata = delivery["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["cron_payload_kind"] == "github_skill_digest"
    assert metadata["news_digest_items"] == [item.to_dict()]

    cron.on_delivery_success(type("Entry", (), {"metadata": metadata})())

    assert marked == [item]


def test_cron_service_loads_agent_scoped_jobs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    agent_dir = workspace / "agents" / "research"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CRON.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "daily-digest",
                        "name": "Research Daily Digest",
                        "enabled": True,
                        "schedule": {
                            "kind": "every",
                            "every_seconds": 1,
                            "anchor": "2026-01-01T00:00:00+00:00",
                        },
                        "payload": {"kind": "agent_turn", "message": "Summarize research."},
                        "delete_after_run": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
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
    dispatcher = FakeDispatcher("Research summary")
    cron = CronService(
        settings,
        dispatcher,
        manager,
        ProactiveTarget("cli", "cli-local", "cli-user", "main"),
    )

    assert len(cron.jobs) == 1
    job = cron.jobs[0]
    assert job.id == "research:daily-digest"
    assert job.config_id == "daily-digest"
    assert job.scope == "research"
    assert job.target.agent_id == "research"
    assert job.source_file == "agents/research/CRON.json"

    rows = cron.list_jobs()
    assert rows[0]["id"] == "research:daily-digest"
    assert rows[0]["config_id"] == "daily-digest"
    assert rows[0]["agent_id"] == "research"
    assert rows[0]["scope"] == "research"

    result = asyncio.run(cron.trigger_job("daily-digest"))

    assert "triggered" in result
    assert dispatcher.background_prompts == ["Summarize research."]
    assert dispatcher.background_calls[0]["disabled_tools"] == ["memory_write"]
    assert channel.sent == ["[Research Daily Digest] Research summary"]
    assert dispatcher.background_calls[0]["disabled_tools"] == ["memory_write"]
    delivery = dispatcher.deliveries[0]
    assert delivery["target"].agent_id == "research"
    metadata = delivery["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["job_id"] == "research:daily-digest"
    assert metadata["cron_config_id"] == "daily-digest"
    assert metadata["cron_scope"] == "research"
