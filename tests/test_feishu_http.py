import asyncio
import hashlib
import json
import time
from pathlib import Path

from agent_gateway.channels.base import ChannelAccount
from agent_gateway.channels.feishu import FeishuChannel
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.models import OutboundMessage
from agent_gateway.runtime.feishu_http import FeishuWebhookServer


class FakeChannelRuntime:
    def __init__(self) -> None:
        self.messages = []

    async def ingest_external(self, inbound) -> None:
        self.messages.append(inbound)


async def _post_json(
    host: str,
    port: int,
    path: str,
    payload: dict,
    *,
    secret: str = "encrypt-key",
) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection(host, port)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = "nonce-123"
    signature = hashlib.sha256(
        timestamp.encode("utf-8")
        + nonce.encode("utf-8")
        + secret.encode("utf-8")
        + body
    ).hexdigest()
    request = "\r\n".join(
        [
            f"POST {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            f"X-Lark-Request-Timestamp: {timestamp}",
            f"X-Lark-Request-Nonce: {nonce}",
            f"X-Lark-Signature: {signature}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("utf-8") + body
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()

    head, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = head.splitlines()[0].decode("utf-8")
    status = int(status_line.split(" ")[1])
    payload = json.loads(body_bytes.decode("utf-8"))
    return status, payload


async def _post_json_unsigned(host: str, port: int, path: str, payload: dict) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection(host, port)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = "\r\n".join(
        [
            f"POST {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("utf-8") + body
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()

    head, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = head.splitlines()[0].decode("utf-8")
    status = int(status_line.split(" ")[1])
    payload = json.loads(body_bytes.decode("utf-8"))
    return status, payload


def _build_server(tmp_path: Path | None = None) -> tuple[FeishuWebhookServer, FakeChannelRuntime]:
    runtime = FakeChannelRuntime()
    account = _build_feishu_account(
        account_id="feishu-main",
        verification_token="verify-token",
        encrypt_key="encrypt-key",
    )
    manager = ChannelManager()
    state_root = (tmp_path or Path("/tmp")) / "feishu-webhook-test" / "channel-state"
    manager.register(FeishuChannel(account, state_root), account)
    server = FeishuWebhookServer(
        host="127.0.0.1",
        port=0,
        path="/webhooks/feishu",
        channels=manager,
        channel_runtime=runtime,
        state_dir=(tmp_path or Path("/tmp")) / "feishu-webhook-test",
    )
    return server, runtime


def _build_feishu_account(
    *,
    account_id: str,
    verification_token: str,
    encrypt_key: str,
    webhook_path: str = "",
) -> ChannelAccount:
    config = {
        "app_id": f"app-{account_id}",
        "app_secret": f"secret-{account_id}",
        "verification_token": verification_token,
        "encrypt_key": encrypt_key,
        "bot_open_id": "ou_bot",
    }
    if webhook_path:
        config["webhook_path"] = webhook_path
    return ChannelAccount(
        channel="feishu",
        account_id=account_id,
        label="Feishu Bot",
        config=config,
    )


def _build_multi_account_server(
    tmp_path: Path | None = None,
) -> tuple[FeishuWebhookServer, FakeChannelRuntime]:
    runtime = FakeChannelRuntime()
    state_root = (tmp_path or Path("/tmp")) / "feishu-webhook-test" / "channel-state"
    manager = ChannelManager()
    main = _build_feishu_account(
        account_id="feishu-main",
        verification_token="verify-token",
        encrypt_key="encrypt-key",
        webhook_path="/webhooks/feishu",
    )
    secondary = _build_feishu_account(
        account_id="feishu-secondary",
        verification_token="verify-token-2",
        encrypt_key="encrypt-key-2",
        webhook_path="/webhooks/feishu/secondary",
    )
    manager.register(FeishuChannel(main, state_root), main)
    manager.register(FeishuChannel(secondary, state_root), secondary)
    server = FeishuWebhookServer(
        host="127.0.0.1",
        port=0,
        path="/webhooks/feishu",
        channels=manager,
        channel_runtime=runtime,
        state_dir=(tmp_path or Path("/tmp")) / "feishu-webhook-test",
    )
    return server, runtime


def test_feishu_webhook_server_handles_challenge(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        del runtime
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {"challenge": "abc123"},
        )
        await server.stop()
        assert status == 200
        assert payload == {"challenge": "abc123"}

    asyncio.run(_run())


def test_feishu_webhook_server_handles_unsigned_challenge(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        del runtime
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json_unsigned(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
                "challenge": "abc123",
                "token": "verify-token",
                "type": "url_verification",
            },
        )
        await server.stop()
        assert status == 200
        assert payload == {"challenge": "abc123"}

    asyncio.run(_run())


def test_feishu_webhook_server_rejects_unsigned_non_challenge_event(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        del runtime
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json_unsigned(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
                "token": "verify-token",
                "header": {"event_id": "evt-unsigned"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_user"}},
                    "message": {
                        "chat_id": "oc_chat",
                        "chat_type": "p2p",
                        "msg_type": "text",
                        "content": json.dumps({"text": "hello"}),
                    },
                },
            },
        )
        await server.stop()
        assert status == 401
        assert payload == {"error": "missing signature headers"}

    asyncio.run(_run())


def test_feishu_webhook_server_rejects_unsigned_challenge_with_bad_token(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        del runtime
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json_unsigned(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
                "challenge": "abc123",
                "token": "wrong-token",
                "type": "url_verification",
            },
        )
        await server.stop()
        assert status == 401
        assert payload == {"error": "verification token mismatch"}

    asyncio.run(_run())


def test_feishu_webhook_server_ingests_message_event(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
                "token": "verify-token",
                "header": {"event_id": "evt-1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_user"}},
                    "message": {
                        "chat_id": "oc_chat",
                        "chat_type": "p2p",
                        "msg_type": "text",
                        "content": json.dumps({"text": "hello"}),
                    },
                },
            },
        )
        await server.stop()
        assert status == 200
        assert payload == {"ok": True}
        assert len(runtime.messages) == 1
        assert runtime.messages[0].text == "hello"
        audit_log = (tmp_path / "feishu-webhook-test" / "events.jsonl").read_text(encoding="utf-8")
        assert '"outcome": "accepted"' in audit_log
        assert '"event_id": "evt-1"' in audit_log

    asyncio.run(_run())


def test_feishu_webhook_server_routes_multiple_accounts_by_path(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_multi_account_server(tmp_path)
        assert server.list_webhook_paths() == [
            ("feishu-main", "/webhooks/feishu"),
            ("feishu-secondary", "/webhooks/feishu/secondary"),
        ]
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu/secondary",
            {
                "token": "verify-token-2",
                "header": {"event_id": "evt-secondary-1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_user_2"}},
                    "message": {
                        "chat_id": "oc_secondary",
                        "chat_type": "p2p",
                        "msg_type": "text",
                        "content": json.dumps({"text": "hello secondary"}),
                    },
                },
            },
            secret="encrypt-key-2",
        )
        await server.stop()

        assert status == 200
        assert payload == {"ok": True}
        assert len(runtime.messages) == 1
        assert runtime.messages[0].account_id == "feishu-secondary"
        assert runtime.messages[0].text == "hello secondary"

    asyncio.run(_run())


def test_feishu_webhook_server_deduplicates_per_account(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_multi_account_server(tmp_path)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        main_event = {
            "token": "verify-token",
            "header": {"event_id": "evt-same-id"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_main"}},
                "message": {
                    "chat_id": "oc_main",
                    "chat_type": "p2p",
                    "msg_type": "text",
                    "content": json.dumps({"text": "main event"}),
                },
            },
        }
        secondary_event = {
            "token": "verify-token-2",
            "header": {"event_id": "evt-same-id"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_secondary"}},
                "message": {
                    "chat_id": "oc_secondary",
                    "chat_type": "p2p",
                    "msg_type": "text",
                    "content": json.dumps({"text": "secondary event"}),
                },
            },
        }
        main_status, _main_payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            main_event,
            secret="encrypt-key",
        )
        secondary_status, _secondary_payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu/secondary",
            secondary_event,
            secret="encrypt-key-2",
        )
        await server.stop()

        assert main_status == 200
        assert secondary_status == 200
        assert [message.account_id for message in runtime.messages] == [
            "feishu-main",
            "feishu-secondary",
        ]

    asyncio.run(_run())


def test_feishu_webhook_server_returns_accepted_for_ignored_event(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
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
            },
        )
        await server.stop()
        assert status == 202
        assert payload == {"ok": True, "ignored": True}
        assert runtime.messages == []

    asyncio.run(_run())


def test_feishu_webhook_server_rejects_bad_signature(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        del runtime
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps({"challenge": "abc123"}, ensure_ascii=False).encode("utf-8")
        request = "\r\n".join(
            [
                "POST /webhooks/feishu HTTP/1.1",
                f"Host: 127.0.0.1:{port}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                f"X-Lark-Request-Timestamp: {int(time.time())}",
                "X-Lark-Request-Nonce: bad-nonce",
                "X-Lark-Signature: bad-signature",
                "Connection: close",
                "",
                "",
            ]
        ).encode("utf-8") + body
        writer.write(request)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        await server.stop()

        head, body_bytes = raw.split(b"\r\n\r\n", 1)
        status_line = head.splitlines()[0].decode("utf-8")
        status = int(status_line.split(" ")[1])
        payload = json.loads(body_bytes.decode("utf-8"))
        assert status == 401
        assert payload == {"error": "signature mismatch"}

    asyncio.run(_run())


def test_feishu_webhook_server_deduplicates_events(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        event = {
            "token": "verify-token",
            "header": {"event_id": "evt-dedup-1"},
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
        first_status, first_payload = await _post_json("127.0.0.1", port, "/webhooks/feishu", event)
        second_status, second_payload = await _post_json("127.0.0.1", port, "/webhooks/feishu", event)
        await server.stop()

        assert first_status == 200
        assert first_payload == {"ok": True}
        assert second_status == 202
        assert second_payload == {"ok": True, "duplicate": True}
        assert len(runtime.messages) == 1

    asyncio.run(_run())


def test_feishu_webhook_server_accepts_card_action_callback(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        event = {
            "token": "verify-token",
            "header": {
                "event_id": "evt-card-1",
                "event_type": "card.action.trigger",
            },
            "event": {
                "operator": {"open_id": "ou_user"},
                "token": "card-token",
                "action": {
                    "tag": "button",
                    "name": "approve_button",
                    "value": {
                        "action": "approve_task",
                        "task_id": "task-1",
                    },
                },
                "context": {
                    "open_message_id": "om_123",
                    "open_chat_id": "oc_chat",
                },
            },
        }
        status, payload = await _post_json("127.0.0.1", port, "/webhooks/feishu", event)
        await asyncio.sleep(0.05)
        await server.stop()

        assert status == 200
        assert payload["toast"]["type"] == "info"
        assert len(runtime.messages) == 1
        assert runtime.messages[0].metadata["kind"] == "card_action"
        assert runtime.messages[0].metadata["feishu_action_value"]["task_id"] == "task-1"
        assert runtime.messages[0].peer_id == "oc_chat"

    asyncio.run(_run())


def test_feishu_webhook_server_updates_stateful_card_control_callback(tmp_path: Path) -> None:
    async def _run() -> None:
        server, runtime = _build_server(tmp_path)
        channel = server._resolve_channel()
        assert channel is not None
        channel.enable_stateful_cards = True
        channel._renderer.enable_stateful_cards = True  # type: ignore[attr-defined]
        payloads = channel._build_send_payloads(  # type: ignore[attr-defined]
            OutboundMessage(
                channel="feishu",
                to="oc_chat",
                text="# 长回复\n\n" + "\n\n".join(f"段落 {index}" for index in range(1, 7)),
                metadata={"receive_id_type": "chat_id"},
            )
        )
        state = payloads[0].card_state
        assert state is not None
        channel.save_card_state(state)

        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, payload = await _post_json(
            "127.0.0.1",
            port,
            "/webhooks/feishu",
            {
                "token": "verify-token",
                "header": {
                    "event_id": "evt-card-control-1",
                    "event_type": "card.action.trigger",
                },
                "event": {
                    "operator": {"open_id": "ou_user"},
                    "token": "card-token",
                    "action": {
                        "tag": "button",
                        "name": "next_page",
                        "value": {
                            "source": "gateway_card_control",
                            "action": "next_page",
                            "card_id": state.card_id,
                        },
                    },
                    "context": {
                        "open_message_id": "om_123",
                        "open_chat_id": "oc_chat",
                    },
                },
            },
        )
        await server.stop()

        assert status == 200
        assert payload["card"]["type"] == "raw"
        assert payload["card"]["data"]["header"]["title"]["content"].endswith("(2/2)")
        assert runtime.messages == []
        updated = channel.load_card_state(state.card_id)
        assert updated is not None
        assert updated.page_index == 1

    asyncio.run(_run())
