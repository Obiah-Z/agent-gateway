import asyncio
import base64
import hashlib
import struct
from pathlib import Path
from urllib.parse import urlencode

import pytest

AES = pytest.importorskip("Crypto.Cipher.AES")

from agent_gateway.gateways.feishu.http import FeishuWebhookServer
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.wework.channel import WeWorkChannel
from agent_gateway.runtime.domain.models import OutboundMessage


class FakeChannelRuntime:
    def __init__(self) -> None:
        self.messages = []

    async def ingest_external(self, inbound) -> None:
        self.messages.append(inbound)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class FakeHTTPClient:
    def __init__(self, post_payloads: list[dict] | None = None) -> None:
        self.get_calls = []
        self.post_calls = []
        self.post_payloads = list(post_payloads or [{"errcode": 0, "errmsg": "ok"}])

    def get(self, url: str, *, params: dict) -> FakeResponse:
        self.get_calls.append((url, params))
        return FakeResponse({"errcode": 0, "access_token": "token-ok", "expires_in": 7200})

    def post(self, url: str, *, params: dict, json: dict) -> FakeResponse:
        self.post_calls.append((url, params, json))
        payload = self.post_payloads.pop(0) if self.post_payloads else {"errcode": 0, "errmsg": "ok"}
        return FakeResponse(payload)

    def close(self) -> None:
        pass


async def _get_text(host: str, port: int, path: str) -> tuple[int, str]:
    reader, writer = await asyncio.open_connection(host, port)
    request = "\r\n".join(
        [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("utf-8")
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()

    head, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = head.splitlines()[0].decode("utf-8")
    status = int(status_line.split(" ")[1])
    return status, body_bytes.decode("utf-8")


async def _post_xml(host: str, port: int, path: str, body: str) -> tuple[int, str]:
    reader, writer = await asyncio.open_connection(host, port)
    body_bytes = body.encode("utf-8")
    request = "\r\n".join(
        [
            f"POST {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Content-Type: text/xml",
            f"Content-Length: {len(body_bytes)}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("utf-8") + body_bytes
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()

    head, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = head.splitlines()[0].decode("utf-8")
    status = int(status_line.split(" ")[1])
    return status, body_bytes.decode("utf-8")


def _encrypt_wework_message(message: str, *, encoding_aes_key: str, corp_id: str) -> str:
    aes_key = base64.b64decode(f"{encoding_aes_key}=")
    payload = (
        b"0123456789abcdef"
        + struct.pack(">I", len(message.encode("utf-8")))
        + message.encode("utf-8")
        + corp_id.encode("utf-8")
    )
    pad = 32 - (len(payload) % 32)
    payload += bytes([pad]) * pad
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    return base64.b64encode(cipher.encrypt(payload)).decode("utf-8")


def _signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypted])).encode()).hexdigest()


def _build_wework_account(
    *,
    token: str,
    corp_id: str,
    encoding_aes_key: str,
) -> ChannelAccount:
    return ChannelAccount(
        channel="wework",
        account_id="wework-main",
        label="WeWork Bot",
        token=token,
        config={
            "corp_id": corp_id,
            "agent_id": "1000002",
            "secret": "secret",
            "encoding_aes_key": encoding_aes_key,
            "webhook_path": "/webhooks/wework",
        },
    )


def _build_query(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return urlencode(
        {
            "msg_signature": _signature(token, timestamp, nonce, encrypted),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": encrypted,
        }
    )


def test_wework_webhook_server_handles_url_verification(tmp_path: Path) -> None:
    async def _run() -> None:
        token = "token-123"
        corp_id = "ww1234567890abcdef"
        encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
        encrypted = _encrypt_wework_message(
            "wework-echo-ok",
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )
        timestamp = "1783256000"
        nonce = "nonce-123"
        query = _build_query(token, timestamp, nonce, encrypted)

        account = _build_wework_account(
            token=token,
            corp_id=corp_id,
            encoding_aes_key=encoding_aes_key,
        )
        manager = ChannelManager()
        manager.register(WeWorkChannel(account), account)
        server = FeishuWebhookServer(
            host="127.0.0.1",
            port=0,
            path="/webhooks/feishu",
            channels=manager,
            channel_runtime=FakeChannelRuntime(),
            state_dir=tmp_path / "webhook",
        )

        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, body = await _get_text("127.0.0.1", port, f"/webhooks/wework?{query}")
        await server.stop()

        assert status == 200
        assert body == "wework-echo-ok"

    asyncio.run(_run())


def test_wework_webhook_server_ingests_text_callback(tmp_path: Path) -> None:
    async def _run() -> None:
        token = "token-123"
        corp_id = "ww1234567890abcdef"
        encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
        plaintext = """<xml>
<ToUserName><![CDATA[ww1234567890abcdef]]></ToUserName>
<FromUserName><![CDATA[zhangsan]]></FromUserName>
<CreateTime>1783256001</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[hello gateway]]></Content>
<MsgId>msg-001</MsgId>
<AgentID>1000002</AgentID>
</xml>"""
        encrypted = _encrypt_wework_message(
            plaintext,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )
        timestamp = "1783256001"
        nonce = "nonce-456"
        query = _build_query(token, timestamp, nonce, encrypted)
        body = f"""<xml>
<ToUserName><![CDATA[{corp_id}]]></ToUserName>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
<AgentID>1000002</AgentID>
</xml>"""

        account = _build_wework_account(
            token=token,
            corp_id=corp_id,
            encoding_aes_key=encoding_aes_key,
        )
        manager = ChannelManager()
        manager.register(WeWorkChannel(account), account)
        runtime = FakeChannelRuntime()
        server = FeishuWebhookServer(
            host="127.0.0.1",
            port=0,
            path="/webhooks/feishu",
            channels=manager,
            channel_runtime=runtime,
            state_dir=tmp_path / "webhook",
        )

        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        status, response_body = await _post_xml(
            "127.0.0.1",
            port,
            f"/webhooks/wework?{query}",
            body,
        )
        await server.stop()

        assert status == 200
        assert response_body == "success"
        assert len(runtime.messages) == 1
        inbound = runtime.messages[0]
        assert inbound.channel == "wework"
        assert inbound.account_id == "wework-main"
        assert inbound.sender_id == "zhangsan"
        assert inbound.peer_id == "zhangsan"
        assert inbound.text == "hello gateway"
        assert inbound.metadata["wework_msg_id"] == "msg-001"
        assert inbound.metadata["idempotency_key"] == "wework:wework-main:msg:msg-001"

    asyncio.run(_run())


def test_wework_webhook_server_deduplicates_text_callback(tmp_path: Path) -> None:
    async def _run() -> None:
        token = "token-123"
        corp_id = "ww1234567890abcdef"
        encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
        plaintext = """<xml>
<ToUserName><![CDATA[ww1234567890abcdef]]></ToUserName>
<FromUserName><![CDATA[zhangsan]]></FromUserName>
<CreateTime>1783256001</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[hello gateway]]></Content>
<MsgId>msg-duplicate</MsgId>
<AgentID>1000002</AgentID>
</xml>"""
        encrypted = _encrypt_wework_message(
            plaintext,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
        )
        timestamp = "1783256001"
        nonce = "nonce-456"
        query = _build_query(token, timestamp, nonce, encrypted)
        body = f"""<xml>
<ToUserName><![CDATA[{corp_id}]]></ToUserName>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
<AgentID>1000002</AgentID>
</xml>"""

        account = _build_wework_account(
            token=token,
            corp_id=corp_id,
            encoding_aes_key=encoding_aes_key,
        )
        manager = ChannelManager()
        manager.register(WeWorkChannel(account), account)
        runtime = FakeChannelRuntime()
        server = FeishuWebhookServer(
            host="127.0.0.1",
            port=0,
            path="/webhooks/feishu",
            channels=manager,
            channel_runtime=runtime,
            state_dir=tmp_path / "webhook",
        )

        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        first = await _post_xml("127.0.0.1", port, f"/webhooks/wework?{query}", body)
        second = await _post_xml("127.0.0.1", port, f"/webhooks/wework?{query}", body)
        await server.stop()

        assert first == (200, "success")
        assert second == (200, "success")
        assert len(runtime.messages) == 1

    asyncio.run(_run())


def test_wework_channel_sends_text_message() -> None:
    token = "token-123"
    corp_id = "ww1234567890abcdef"
    encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
    account = _build_wework_account(
        token=token,
        corp_id=corp_id,
        encoding_aes_key=encoding_aes_key,
    )
    channel = WeWorkChannel(account)
    fake_http = FakeHTTPClient()
    channel._http = fake_http

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="你好",
        )
    )

    assert ok is True
    assert fake_http.get_calls == [
        (
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            {"corpid": corp_id, "corpsecret": "secret"},
        )
    ]
    assert fake_http.post_calls == [
        (
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            {"access_token": "token-ok"},
            {
                "touser": "zhangsan",
                "msgtype": "text",
                "agentid": 1000002,
                "text": {"content": "你好"},
                "safe": 0,
            },
        )
    ]


def test_wework_channel_sends_markdown_message_for_markdown_content() -> None:
    token = "token-123"
    corp_id = "ww1234567890abcdef"
    encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
    account = _build_wework_account(
        token=token,
        corp_id=corp_id,
        encoding_aes_key=encoding_aes_key,
    )
    channel = WeWorkChannel(account)
    fake_http = FakeHTTPClient()
    channel._http = fake_http

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="## 今日摘要\n\n- **热量**：1800 kcal\n- 蛋白质充足",
        )
    )

    assert ok is True
    _, _, payload = fake_http.post_calls[0]
    assert payload["msgtype"] == "markdown"
    assert payload["markdown"]["content"].startswith("## 今日摘要")


def test_wework_channel_refreshes_token_and_retries_on_token_error() -> None:
    token = "token-123"
    corp_id = "ww1234567890abcdef"
    encoding_aes_key = base64.b64encode(b"12345678901234567890123456789012").decode().rstrip("=")
    account = _build_wework_account(
        token=token,
        corp_id=corp_id,
        encoding_aes_key=encoding_aes_key,
    )
    channel = WeWorkChannel(account)
    fake_http = FakeHTTPClient(
        post_payloads=[
            {"errcode": 42001, "errmsg": "access_token expired"},
            {"errcode": 0, "errmsg": "ok"},
        ]
    )
    channel._http = fake_http

    ok = channel.send(OutboundMessage(channel="wework", to="zhangsan", text="你好"))

    assert ok is True
    assert len(fake_http.get_calls) == 2
    assert len(fake_http.post_calls) == 2
