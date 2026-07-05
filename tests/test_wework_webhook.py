import asyncio
import base64
import hashlib
import struct
from pathlib import Path
from urllib.parse import urlencode

import pytest

AES = pytest.importorskip("Crypto.Cipher").AES

from agent_gateway.gateways.feishu.http import FeishuWebhookServer
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.wework.channel import WeWorkChannel


class FakeChannelRuntime:
    async def ingest_external(self, inbound) -> None:
        del inbound


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
        query = urlencode(
            {
                "msg_signature": _signature(token, timestamp, nonce, encrypted),
                "timestamp": timestamp,
                "nonce": nonce,
                "echostr": encrypted,
            }
        )

        account = ChannelAccount(
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
