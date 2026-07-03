import json
import subprocess
from pathlib import Path

from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.feishu.channel import FeishuChannel
from agent_gateway.runtime.state.queue import PermanentDeliveryError
from agent_gateway.runtime.domain.models import OutboundMessage
from Crypto.Cipher import AES

import base64
import hashlib


class FakeCardStateRepository:
    enabled = True

    def __init__(self, rows=None, *, fail: bool = False) -> None:
        self.rows = list(rows or [])
        self.fail = fail
        self.written: list[dict[str, object]] = []

    def get(self, table: str, key: str):
        if self.fail:
            raise RuntimeError("postgres unavailable")
        if table != "feishu_card_states":
            return None
        for row in self.rows:
            if str(row.get("card_id", "")) == key:
                return row
        return None

    def write_feishu_card_state(self, row: dict[str, object]):
        if self.fail:
            raise RuntimeError("postgres unavailable")
        self.written.append(dict(row))
        return row


def _build_channel(
    *,
    state_dir: Path | None = None,
    state_read_repository=None,
    state_write_repository=None,
    **config_overrides,
):
    account = ChannelAccount(
        channel="feishu",
        account_id="feishu-main",
        label="Feishu",
        config={
            "app_id": "app",
            "app_secret": "secret",
            "verification_token": "verify-token",
            "bot_open_id": "ou_bot",
            **config_overrides,
        },
    )
    return FeishuChannel(
        account,
        state_dir,
        state_read_repository=state_read_repository,
        state_write_repository=state_write_repository,
    )


def test_feishu_channel_parse_event_rejects_wrong_verification_token() -> None:
    channel = _build_channel()
    payload = {
        "token": "wrong-token",
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "p2p",
                "msg_type": "text",
                "content": json.dumps({"text": "hello"}),
            },
        },
    }

    inbound = channel.parse_event(payload, token="wrong-token")

    assert inbound is None


def test_feishu_channel_parse_event_accepts_p2p_text() -> None:
    channel = _build_channel()
    payload = {
        "token": "verify-token",
        "header": {"event_id": "evt_1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_chat",
                "chat_type": "p2p",
                "msg_type": "text",
                "content": json.dumps({"text": "hello"}),
            },
        },
    }

    inbound = channel.parse_event(payload, token="verify-token")

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.sender_id == "ou_user"
    assert inbound.peer_id == "ou_user"
    assert inbound.metadata["receive_id_type"] == "open_id"
    assert inbound.metadata["feishu_event_id"] == "evt_1"
    assert inbound.metadata["feishu_message_id"] == "om_1"
    assert inbound.metadata["feishu_chat_id"] == "oc_chat"
    assert inbound.metadata["feishu_message_type"] == "text"


def test_feishu_channel_parse_event_ignores_group_message_without_mention() -> None:
    channel = _build_channel()
    payload = {
        "token": "verify-token",
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "group",
                "msg_type": "text",
                "content": json.dumps({"text": "hello group"}),
                "mentions": [],
            },
        },
    }

    inbound = channel.parse_event(payload, token="verify-token")

    assert inbound is None


def test_feishu_channel_can_decode_encrypted_payload() -> None:
    channel = _build_channel(encrypt_key="encrypt-key")
    body = json.dumps({"challenge": "abc123"}, ensure_ascii=False).encode("utf-8")
    key = hashlib.sha256("encrypt-key".encode("utf-8")).digest()
    iv = bytes(range(16))
    padding = 16 - (len(body) % 16)
    padded = body + bytes([padding]) * padding
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = base64.b64encode(iv + cipher.encrypt(padded)).decode("utf-8")

    payload = channel.decode_payload({"encrypt": encrypted})

    assert payload == {"challenge": "abc123"}


def test_feishu_channel_send_uses_interactive_card_for_markdown(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "json": json,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="# Title\n\n- item 1\n- item 2\n\n[link](https://example.com)",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) == 1
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    assert payload["msg_type"] == "interactive"
    card = json.loads(payload["content"])
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "Title"
    assert card["body"]["elements"][0]["tag"] == "markdown"
    assert "- item 1" in card["body"]["elements"][0]["content"]


def test_feishu_channel_send_infers_open_id_for_proactive_message(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"params": params, "json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="ou_user",
            text="主动任务推送",
            metadata={"account_id": "feishu-main"},
        )
    )

    assert ok is True
    assert sent[0]["params"] == {"receive_id_type": "open_id"}


def test_feishu_channel_send_uploads_report_path_from_reply_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    report_path = tmp_path / "workspace" / "reports" / "github-repos" / "仓库分析-demo.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# 报告\n\n内容", encoding="utf-8")
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_post(url: str, **kwargs):
        sent.append({"url": url, **kwargs})
        if url.endswith("/im/v1/files"):
            return FakeResponse({"code": 0, "data": {"file_key": "file_report_1"}})
        return FakeResponse({"code": 0, "msg": "success"})

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="ou_user",
            text=(
                "分析完成，报告已写入。\n"
                "报告路径：workspace/reports/github-repos/仓库分析-demo.md"
            ),
            metadata={"receive_id_type": "open_id"},
        )
    )

    assert ok is True
    assert len(sent) == 3
    assert sent[0]["url"].endswith("/im/v1/messages")
    assert sent[1]["url"].endswith("/im/v1/files")
    assert sent[1]["data"] == {"file_type": "stream", "file_name": "仓库分析-demo.md"}
    assert sent[2]["url"].endswith("/im/v1/messages")
    file_payload = sent[2]["json"]
    assert isinstance(file_payload, dict)
    assert file_payload["msg_type"] == "file"
    assert json.loads(file_payload["content"]) == {"file_key": "file_report_1"}


def test_feishu_channel_send_uploads_diagram_path_from_reply_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    diagram_path = tmp_path / "workspace" / "reports" / "diagrams" / "网关架构图.png"
    diagram_path.parent.mkdir(parents=True)
    diagram_path.write_bytes(b"fake-png")
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_post(url: str, **kwargs):
        sent.append({"url": url, **kwargs})
        if url.endswith("/im/v1/files"):
            return FakeResponse({"code": 0, "data": {"file_key": "file_diagram_1"}})
        return FakeResponse({"code": 0, "msg": "success"})

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="ou_user",
            text=(
                "绘图完成。\n"
                "图片路径：workspace/reports/diagrams/网关架构图.png"
            ),
            metadata={"receive_id_type": "open_id"},
        )
    )

    assert ok is True
    assert len(sent) == 3
    assert sent[1]["url"].endswith("/im/v1/files")
    assert sent[1]["data"] == {"file_type": "stream", "file_name": "网关架构图.png"}
    file_payload = sent[2]["json"]
    assert isinstance(file_payload, dict)
    assert file_payload["msg_type"] == "file"
    assert json.loads(file_payload["content"]) == {"file_key": "file_diagram_1"}


def test_feishu_channel_send_can_use_lark_cli_for_group_message(monkeypatch) -> None:
    channel = _build_channel(send_mode="lark_cli", render_mode="text")
    calls: list[list[str]] = []

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.shutil.which", lambda command: command)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.subprocess.run", fake_run)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="hello",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert calls == [
        [
            "lark-cli",
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--chat-id",
            "oc_chat",
            "--text",
            "hello",
        ]
    ]


def test_feishu_channel_lark_cli_sends_metadata_attachment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    report_path = tmp_path / "workspace" / "reports" / "github-repos" / "仓库分析-cli.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# CLI 报告", encoding="utf-8")
    channel = _build_channel(send_mode="lark_cli", render_mode="text")
    calls: list[list[str]] = []

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.shutil.which", lambda command: command)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.subprocess.run", fake_run)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="报告完成",
            metadata={
                "receive_id_type": "chat_id",
                "attachments": [{"path": "reports/github-repos/仓库分析-cli.md"}],
            },
        )
    )

    assert ok is True
    assert len(calls) == 2
    assert calls[0][-2:] == ["--text", "报告完成"]
    assert calls[1][-2:] == ["--file", str(report_path.resolve())]


def test_feishu_channel_lark_cli_send_uses_user_id_for_open_id(monkeypatch) -> None:
    channel = _build_channel(send_mode="lark_cli")
    calls: list[list[str]] = []

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.shutil.which", lambda command: command)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("agent_gateway.gateways.feishu.channel.subprocess.run", fake_run)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="ou_user",
            text="hello",
            metadata={},
        )
    )

    assert ok is True
    assert "--user-id" in calls[0]
    assert "ou_user" in calls[0]
    assert "--markdown" in calls[0]


def test_feishu_channel_rewrites_secondary_heading_for_feishu_markdown(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="## 四个核心组件\n\n- Agent Loop\n- Tool Calling",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "四个核心组件"
    assert card["body"]["elements"][0]["content"] == "- Agent Loop\n- Tool Calling"


def test_feishu_channel_rewrites_inner_heading_markers_to_bold(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="这是导语。\n\n## 四个核心组件\n1. Agent Loop\n2. Tool Calling",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    assert card["body"]["elements"][0]["content"] == "这是导语。"
    assert card["body"]["elements"][1]["content"].startswith("## 四个核心组件")
    assert "## 四个核心组件" in card["body"]["elements"][1]["content"]


def test_feishu_channel_send_falls_back_to_text_when_card_rejected(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []
    responses = iter(
        [
            {"code": 230054, "msg": "This type of message is unavailable in the connection group."},
            {"code": 0, "msg": "success"},
        ]
    )

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json, "params": params})
        return FakeResponse(next(responses))

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="**bold**\n\nplain text body",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) == 2
    first_payload = sent[0]["json"]
    second_payload = sent[1]["json"]
    assert isinstance(first_payload, dict)
    assert isinstance(second_payload, dict)
    assert first_payload["msg_type"] == "interactive"
    assert second_payload["msg_type"] == "text"


def test_feishu_channel_send_raises_permanent_error_for_invalid_receive_id(monkeypatch) -> None:
    channel = _build_channel()

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {
                "code": 99992351,
                "msg": "The request you send is not a valid {open_id}. Invalid ids: [ou_test_user_002]",
            }

    def fake_post(url: str, *, params=None, headers=None, json=None):
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    try:
        channel.send(
            OutboundMessage(
                channel="feishu",
                to="ou_test_user_002",
                text="bad target",
                metadata={"receive_id_type": "open_id"},
            )
        )
    except PermanentDeliveryError as exc:
        message = str(exc)
    else:
        raise AssertionError("PermanentDeliveryError was not raised")

    assert "ou_test_user_002" in message
    assert "99992351" in message


def test_feishu_channel_send_honors_text_render_mode(monkeypatch) -> None:
    channel = _build_channel(render_mode="text")
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="# Heading\n\n- item",
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) == 1
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    assert payload["msg_type"] == "text"


def test_feishu_channel_send_splits_long_markdown_into_multiple_cards(monkeypatch) -> None:
    channel = _build_channel(card_page_max_bytes=160)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = (
        "# Long Reply\n\n"
        + "\n\n".join(
            f"## Section {index}\n" + ("content " * 18)
            for index in range(1, 6)
        )
    )

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) >= 2
    first_payload = sent[0]["json"]
    assert isinstance(first_payload, dict)
    assert first_payload["msg_type"] == "interactive"
    first_card = json.loads(first_payload["content"])
    assert first_card["schema"] == "2.0"
    assert first_card["header"]["title"]["content"].startswith("Long Reply")
    last_payload = sent[-1]["json"]
    assert isinstance(last_payload, dict)
    last_card = json.loads(last_payload["content"])
    assert "(1/" in first_card["header"]["title"]["content"]
    assert last_card["header"]["title"]["content"].startswith("Long Reply")


def test_feishu_channel_send_appends_card_actions(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="### Build Result\n\n构建完成，详情见下方链接。",
            metadata={
                "receive_id_type": "chat_id",
                "feishu_card_actions": [
                    {"text": "查看日志", "url": "https://example.com/logs", "type": "primary"},
                    {"text": "查看报告", "url": "https://example.com/report"},
                ],
            },
        )
    )

    assert ok is True
    assert len(sent) == 1
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    assert card["schema"] == "2.0"
    buttons = [element for element in card["body"]["elements"] if element.get("tag") == "button"]
    assert len(buttons) == 2
    assert buttons[0]["behaviors"][0]["default_url"] == "https://example.com/logs"
    assert buttons[1]["behaviors"][0]["default_url"] == "https://example.com/report"


def test_feishu_channel_send_supports_callback_action_values(monkeypatch) -> None:
    channel = _build_channel()
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="### Approval\n\n请处理该任务。",
            metadata={
                "receive_id_type": "chat_id",
                "feishu_card_actions": [
                    {
                        "text": "批准",
                        "callback": True,
                        "action": "approve_task",
                        "value": {"task_id": "task-1"},
                        "type": "primary",
                    }
                ],
            },
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    button = card["body"]["elements"][-1]
    callback_behavior = next(
        behavior for behavior in button["behaviors"] if behavior["type"] == "callback"
    )
    assert callback_behavior["value"]["task_id"] == "task-1"
    assert callback_behavior["value"]["action"] == "approve_task"
    assert callback_behavior["value"]["source"] == "gateway"


def test_feishu_channel_parse_card_action_event() -> None:
    channel = _build_channel()
    payload = {
        "token": "verify-token",
        "header": {
            "event_id": "evt-card-1",
            "event_type": "card.action.trigger",
        },
        "event": {
            "operator": {"open_id": "ou_user"},
            "token": "c-token",
            "action": {
                "tag": "button",
                "name": "approve_button",
                "value": {"action": "approve_task", "task_id": "task-1"},
            },
            "context": {
                "open_message_id": "om_123",
                "open_chat_id": "oc_chat",
            },
        },
    }

    inbound = channel.parse_card_action(payload, token="verify-token")

    assert inbound is not None
    assert inbound.sender_id == "ou_user"
    assert inbound.peer_id == "oc_chat"
    assert inbound.metadata["kind"] == "card_action"
    assert inbound.metadata["feishu_action_value"]["task_id"] == "task-1"
    assert "FEISHU_CARD_ACTION" in inbound.text


def test_feishu_channel_send_uses_text_fallback_for_each_failed_card_page(monkeypatch) -> None:
    channel = _build_channel(card_page_max_bytes=150)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse(next(responses))

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = "# Incident\n\n" + "\n\n".join(
        f"段落 {index}\n" + ("内容 " * 20)
        for index in range(1, 5)
    )
    page_count = len(
        channel._build_send_payloads(  # type: ignore[attr-defined]
            OutboundMessage(
                channel="feishu",
                to="oc_chat",
                text=text,
                metadata={"receive_id_type": "chat_id"},
            )
        )
    )
    responses = iter(
        item
        for _ in range(page_count)
        for item in (
            {"code": 230054, "msg": "card unsupported"},
            {"code": 0, "msg": "success"},
        )
    )

    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) == page_count * 2
    assert sent[0]["json"]["msg_type"] == "interactive"
    assert sent[1]["json"]["msg_type"] == "text"
    assert sent[2]["json"]["msg_type"] == "interactive"
    assert sent[3]["json"]["msg_type"] == "text"


def test_feishu_channel_send_uses_stateful_single_card_for_long_reply(
    monkeypatch,
    tmp_path: Path,
) -> None:
    channel = _build_channel(state_dir=tmp_path, enable_stateful_cards=True)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "msg": "success",
                "data": {"message_id": "om_stateful_1"},
            }

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = "# 状态卡片\n\n" + "\n\n".join(
        f"## Section {index}\n" + ("内容 " * 20)
        for index in range(1, 7)
    )
    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    assert len(sent) == 1
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    assert card["schema"] == "2.0"
    buttons = [item for item in card["body"]["elements"] if item.get("tag") == "button"]
    assert any(
        behavior["value"]["action"] == "expand"
        for button in buttons
        for behavior in button.get("behaviors", [])
        if behavior.get("type") == "callback"
    )
    assert any(
        behavior["value"]["action"] == "next_page"
        for button in buttons
        for behavior in button.get("behaviors", [])
        if behavior.get("type") == "callback"
    )
    state = channel.load_card_state(buttons[0]["behaviors"][0]["value"]["card_id"])
    assert state is not None
    assert state.message_id == "om_stateful_1"
    assert state.page_index == 0
    assert state.expanded is False


def test_feishu_channel_handle_control_card_action_updates_stateful_card(tmp_path: Path) -> None:
    channel = _build_channel(state_dir=tmp_path, enable_stateful_cards=True)
    send_payloads = channel._build_send_payloads(  # type: ignore[attr-defined]
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="# 长回复\n\n" + "\n\n".join(f"段落 {index}" for index in range(1, 7)),
            metadata={"receive_id_type": "chat_id"},
        )
    )
    state = send_payloads[0].card_state
    assert state is not None
    channel.save_card_state(state)

    response = channel.handle_control_card_action(
        {
            "event": {
                "action": {
                    "value": {
                        "source": "gateway_card_control",
                        "action": "next_page",
                        "card_id": state.card_id,
                    }
                }
            }
        }
    )

    assert response is not None
    assert response["card"]["type"] == "raw"
    updated = channel.load_card_state(state.card_id)
    assert updated is not None
    assert updated.page_index == 1
    response_card = response["card"]["data"]
    assert response_card["header"]["title"]["content"].endswith("(2/2)")

    expand_response = channel.handle_control_card_action(
        {
            "event": {
                "action": {
                    "value": {
                        "source": "gateway_card_control",
                        "action": "expand",
                        "card_id": state.card_id,
                    }
                }
            }
        }
    )
    assert expand_response is not None
    expanded = channel.load_card_state(state.card_id)
    assert expanded is not None
    assert expanded.expanded is True
    buttons = [
        item
        for item in expand_response["card"]["data"]["body"]["elements"]
        if item.get("tag") == "button"
    ]
    assert any(
        behavior["value"]["action"] == "collapse"
        for button in buttons
        for behavior in button.get("behaviors", [])
        if behavior.get("type") == "callback"
    )


def test_feishu_channel_loads_card_state_from_postgres_first(tmp_path: Path) -> None:
    repo = FakeCardStateRepository(
        [
            {
                "card_id": "card-db",
                "owner_channel": "feishu",
                "owner_account_id": "feishu-main",
                "peer_id": "oc_chat",
                "message_id": "om_db",
                "title": "数据库卡片",
                "summary": "summary",
                "template": "blue",
                "card_link": "",
                "blocks": ["第一页", "第二页"],
                "structured_blocks": [],
                "actions": [],
                "page_size": 1,
                "page_index": 1,
                "expanded": True,
                "updated_at": 1.0,
                "metadata": {},
            }
        ]
    )
    channel = _build_channel(
        state_dir=tmp_path,
        enable_stateful_cards=True,
        state_read_repository=repo,
    )

    state = channel.load_card_state("card-db")

    assert state is not None
    assert state.message_id == "om_db"
    assert state.page_index == 1
    assert state.expanded is True


def test_feishu_channel_card_state_writes_postgres_and_keeps_local_fallback(tmp_path: Path) -> None:
    repo = FakeCardStateRepository()
    channel = _build_channel(
        state_dir=tmp_path,
        enable_stateful_cards=True,
        state_write_repository=repo,
    )
    state = channel._build_send_payloads(  # type: ignore[attr-defined]
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="# 长回复\n\n" + "\n\n".join(f"段落 {index}" for index in range(1, 7)),
            metadata={"receive_id_type": "chat_id"},
        )
    )[0].card_state
    assert state is not None

    channel.save_card_state(state)

    assert repo.written[0]["card_id"] == state.card_id
    local_path = tmp_path / "feishu" / "feishu-main" / "cards" / f"{state.card_id}.json"
    assert local_path.exists()


def test_feishu_channel_card_state_keeps_local_fallback_when_postgres_fails(tmp_path: Path) -> None:
    channel = _build_channel(
        state_dir=tmp_path,
        enable_stateful_cards=True,
        state_write_repository=FakeCardStateRepository(fail=True),
    )
    state = channel._build_send_payloads(  # type: ignore[attr-defined]
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text="# 长回复\n\n" + "\n\n".join(f"段落 {index}" for index in range(1, 7)),
            metadata={"receive_id_type": "chat_id"},
        )
    )[0].card_state
    assert state is not None

    channel.save_card_state(state)

    assert channel.load_card_state(state.card_id) is not None


def test_feishu_channel_renders_structured_json_blocks_as_components(
    monkeypatch,
    tmp_path: Path,
) -> None:
    channel = _build_channel(state_dir=tmp_path)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = """# 结果汇总

```json
[
  {
    "type": "status",
    "status": "success",
    "title": "任务完成",
    "message": "全部任务均已通过"
  },
  {
    "type": "kv",
    "title": "关键指标",
    "items": [
      {"label": "成功数", "value": "12"},
      {"label": "失败数", "value": "0"}
    ]
  },
  {
    "type": "table",
    "title": "明细",
    "columns": ["任务", "状态"],
    "rows": [
      ["构建", "成功"],
      ["测试", "成功"]
    ]
  }
]
```"""
    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    tags = [item.get("tag") for item in card["body"]["elements"]]
    assert "column_set" in tags
    assert "table" in tags
    table = next(item for item in card["body"]["elements"] if item.get("tag") == "table")
    assert table["columns"][0]["display_name"] == "任务"
    assert table["rows"][0]["col_1"] == "构建"


def test_feishu_channel_renders_table_rows_from_header_named_dicts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    channel = _build_channel(state_dir=tmp_path)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = """# 数据表

```json
{
  "type": "table",
  "title": "发布结果",
  "headers": [
    {"label": "服务", "key": "service"},
    {"label": "状态", "key": "status"},
    {"label": "耗时", "key": "duration", "type": "number"}
  ],
  "rows": [
    {"服务": "gateway", "状态": "success", "耗时": 12},
    {"service": "worker", "status": "running", "duration": "8"}
  ]
}
```"""
    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    table = next(item for item in card["body"]["elements"] if item.get("tag") == "table")
    assert table["columns"][0]["display_name"] == "服务"
    assert table["columns"][0]["name"] == "service"
    assert table["rows"][0]["service"] == "gateway"
    assert table["rows"][0]["duration"] == 12
    assert table["rows"][1]["duration"] == 8


def test_feishu_channel_auto_expands_table_layout_for_long_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    channel = _build_channel(state_dir=tmp_path)
    sent: list[dict[str, object]] = []

    def fake_refresh_token() -> str:
        return "tenant-token"

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"code": 0, "msg": "success"}

    def fake_post(url: str, *, params=None, headers=None, json=None):
        sent.append({"json": json})
        return FakeResponse()

    monkeypatch.setattr(channel, "_refresh_token", fake_refresh_token)
    monkeypatch.setattr(channel._http, "post", fake_post)

    text = """# 长文本表格

```json
{
  "type": "table",
  "title": "执行详情",
  "headers": [
    {"label": "任务", "key": "task"},
    {"label": "详情", "key": "detail"},
    {"label": "状态", "key": "status"}
  ],
  "rows": [
    {
      "task": "gateway",
      "detail": "这一行包含非常长的说明文字，用来验证表格是否会自动拉宽长文本列，并且让行高改为 auto，从而尽量在不点击行的前提下展示更多内容。",
      "status": "done"
    }
  ]
}
```"""
    ok = channel.send(
        OutboundMessage(
            channel="feishu",
            to="oc_chat",
            text=text,
            metadata={"receive_id_type": "chat_id"},
        )
    )

    assert ok is True
    payload = sent[0]["json"]
    assert isinstance(payload, dict)
    card = json.loads(payload["content"])
    table = next(item for item in card["body"]["elements"] if item.get("tag") == "table")
    assert table["row_height"] == "auto"
    assert table["row_max_height"] == "480px"
    assert table["columns"][1]["name"] == "detail"
    assert table["columns"][1]["width"] in {"35%", "45%", "55%", "240px", "320px", "420px"}
    assert table["columns"][0]["width"] == "120px"
