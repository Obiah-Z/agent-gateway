import asyncio
import json
import time
from pathlib import Path

from agent_gateway.channels.base import Channel, ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.models import AgentReply, OutboundMessage, ProactiveTarget
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
