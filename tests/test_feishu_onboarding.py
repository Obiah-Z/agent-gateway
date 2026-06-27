import asyncio
import json
from pathlib import Path

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.domain.models import AgentConfig, InboundMessage
from agent_gateway.gateways.feishu.onboarding import (
    FeishuOnboardingService,
    FeishuOnboardingSessionStore,
)
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.runtime.execution.resilience import ProfileManager


class FakeDispatcher:
    def __init__(self) -> None:
        self.delivered: list[tuple[str, str, dict]] = []

    async def deliver_text(self, channels, target, text: str, *, metadata=None) -> str:
        del channels
        self.delivered.append((target.peer_id, text, dict(metadata or {})))
        return "delivery-1"


class FakeOnboardingStateRepository:
    enabled = True

    def __init__(self, rows=None, *, fail: bool = False) -> None:
        self.rows = list(rows or [])
        self.fail = fail
        self.written: list[dict[str, object]] = []

    def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
        del limit, cursor, filters
        if self.fail:
            raise RuntimeError("postgres unavailable")
        if table == "feishu_onboarding_sessions":
            return list(self.rows)
        return []

    def write_feishu_onboarding_session(self, row: dict[str, object]):
        if self.fail:
            raise RuntimeError("postgres unavailable")
        self.written.append(dict(row))
        return row


def _build_control(tmp_path: Path) -> GatewayControlPlane:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    settings.agents_config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "main",
                        "name": "Main",
                        "personality": "",
                        "model": "",
                        "dm_scope": "per-peer",
                        "extra_system": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings.bindings_config_file.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "agent_id": "main",
                        "tier": 5,
                        "match_key": "default",
                        "match_value": "*",
                        "priority": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([]),
        channels=ChannelManager(),
    )
    control.reload_bindings()
    return control


def test_feishu_onboarding_store_creates_pending_session(tmp_path: Path) -> None:
    store = FeishuOnboardingSessionStore(tmp_path)

    session = store.create(mode="personal", account_id="feishu-long-local", ttl_seconds=60)

    assert session.status == "pending"
    assert session.binding_code.startswith("GATEWAY-")
    assert store.find_pending_by_code(session.binding_code) is not None


def test_feishu_onboarding_store_prefers_postgres_read_backend(tmp_path: Path) -> None:
    repo = FakeOnboardingStateRepository(
        [
            {
                "session_id": "ob_db",
                "binding_code": "GATEWAY-DB1234",
                "mode": "personal",
                "status": "pending",
                "account_id": "feishu-long-local",
                "created_at": 1.0,
                "expires_at": 9999999999.0,
            }
        ]
    )
    store = FeishuOnboardingSessionStore(tmp_path, read_backend=repo)

    session = store.find_pending_by_code("gateway-db1234")

    assert session is not None
    assert session.session_id == "ob_db"


def test_feishu_onboarding_store_writes_postgres_and_local(tmp_path: Path) -> None:
    repo = FakeOnboardingStateRepository()
    store = FeishuOnboardingSessionStore(tmp_path, write_backend=repo)

    session = store.create(mode="personal", account_id="feishu-long-local", ttl_seconds=60)

    assert repo.written[0]["session_id"] == session.session_id
    assert repo.written[0]["binding_code"] == session.binding_code
    local_payload = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert local_payload["sessions"][0]["session_id"] == session.session_id


def test_feishu_onboarding_store_falls_back_to_local_when_postgres_fails(tmp_path: Path) -> None:
    repo = FakeOnboardingStateRepository(fail=True)
    store = FeishuOnboardingSessionStore(tmp_path, read_backend=repo, write_backend=repo)

    session = store.create(mode="personal", account_id="feishu-long-local", ttl_seconds=60)

    assert store.find_pending_by_code(session.binding_code) is not None
    local_payload = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert local_payload["sessions"][0]["session_id"] == session.session_id


def test_feishu_onboarding_consumes_binding_code_and_creates_agent(tmp_path: Path) -> None:
    control = _build_control(tmp_path)
    dispatcher = FakeDispatcher()
    service = FeishuOnboardingService(
        store=FeishuOnboardingSessionStore(tmp_path / "onboarding"),
        control_plane=control,
        dispatcher=dispatcher,
    )
    session = service.create_session(mode="personal", account_id="feishu-long-local")

    consumed = asyncio.run(
        service.try_consume_activation(
            InboundMessage(
                text=f"绑定 {session['binding_code']}",
                sender_id="ou_user",
                channel="feishu",
                account_id="feishu-long-local",
                peer_id="ou_user",
                metadata={"receive_id_type": "open_id"},
            )
        )
    )

    assert consumed is True
    status = service.status(session["session_id"])
    assert status is not None
    assert status["status"] == "bound"
    assert status["bound_peer_id"] == "ou_user"
    assert control.agents.get(status["agent_id"]) is not None
    assert any(binding.match_value == "ou_user" for binding in control.bindings.list_all())
    assert dispatcher.delivered[0][0] == "ou_user"
    assert "接入成功" in dispatcher.delivered[0][1]


def test_feishu_onboarding_auto_binds_first_p2p_message(tmp_path: Path) -> None:
    control = _build_control(tmp_path)
    dispatcher = FakeDispatcher()
    service = FeishuOnboardingService(
        store=FeishuOnboardingSessionStore(tmp_path / "onboarding"),
        control_plane=control,
        dispatcher=dispatcher,
        auto_bind_first_message=True,
    )

    consumed = asyncio.run(
        service.try_consume_activation(
            InboundMessage(
                text="你好，帮我看下流程",
                sender_id="ou_user",
                channel="feishu",
                account_id="feishu-long-local",
                peer_id="ou_user",
                metadata={"receive_id_type": "open_id"},
            )
        )
    )

    assert consumed is True
    assert any(binding.match_value == "ou_user" for binding in control.bindings.list_all())
    assert dispatcher.delivered[0][0] == "ou_user"
    assert "接入成功" in dispatcher.delivered[0][1]


def test_feishu_onboarding_session_response_includes_bot_link(tmp_path: Path) -> None:
    control = _build_control(tmp_path)
    service = FeishuOnboardingService(
        store=FeishuOnboardingSessionStore(tmp_path / "onboarding"),
        control_plane=control,
        dispatcher=FakeDispatcher(),
        bot_link="https://open.feishu.cn/bot/abc",
    )

    session = service.create_session(mode="personal", account_id="feishu-long-local")

    assert session["bot_link"] == "https://open.feishu.cn/bot/abc"
    assert session["qr_target"] == "https://open.feishu.cn/bot/abc"
    assert session["auto_bind_first_message"] is True


def test_feishu_onboarding_group_binding_uses_chat_peer(tmp_path: Path) -> None:
    control = _build_control(tmp_path)
    dispatcher = FakeDispatcher()
    service = FeishuOnboardingService(
        store=FeishuOnboardingSessionStore(tmp_path / "onboarding"),
        control_plane=control,
        dispatcher=dispatcher,
    )
    session = service.create_session(mode="group", account_id="feishu-long-local")

    consumed = asyncio.run(
        service.try_consume_activation(
            InboundMessage(
                text=f"请绑定 {session['binding_code']}",
                sender_id="ou_user",
                channel="feishu",
                account_id="feishu-long-local",
                peer_id="oc_group",
                is_group=True,
                metadata={"receive_id_type": "chat_id"},
            )
        )
    )

    assert consumed is True
    assert any(binding.match_value == "oc_group" for binding in control.bindings.list_all())
    assert dispatcher.delivered[0][0] == "oc_group"


def test_feishu_onboarding_ignores_unknown_code(tmp_path: Path) -> None:
    control = _build_control(tmp_path)
    service = FeishuOnboardingService(
        store=FeishuOnboardingSessionStore(tmp_path / "onboarding"),
        control_plane=control,
        dispatcher=FakeDispatcher(),
    )

    consumed = asyncio.run(
        service.try_consume_activation(
            InboundMessage(
                text="绑定 GATEWAY-UNKNOWN",
                sender_id="ou_user",
                channel="feishu",
                account_id="feishu-long-local",
                peer_id="ou_user",
            )
        )
    )

    assert consumed is False
