from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_gateway.monitoring.static_server import DashboardConfig, DashboardStaticServer


class FakeOnboarding:
    def __init__(self) -> None:
        self.created = 0

    def create_session(self, **kwargs):
        self.created += 1
        return {
            "session_id": "ob_test",
            "binding_code": "GATEWAY-ABC123",
            "activation_text": "绑定 GATEWAY-ABC123",
            "status": "pending",
            "bot_link": "https://open.feishu.cn/bot/abc",
            "qr_target": "https://open.feishu.cn/bot/abc",
        }

    def status(self, session_id: str):
        if session_id != "ob_test":
            return None
        return {
            "session_id": "ob_test",
            "binding_code": "GATEWAY-ABC123",
            "activation_text": "绑定 GATEWAY-ABC123",
            "status": "bound",
            "bot_link": "https://open.feishu.cn/bot/abc",
            "qr_target": "https://open.feishu.cn/bot/abc",
        }


async def _get(host: str, port: int, path: str) -> tuple[int, dict[str, str], bytes]:
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

    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("utf-8").splitlines()
    status = int(lines[0].split(" ")[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.lower()] = value.strip()
    return status, headers, body


def test_dashboard_static_server_serves_index_and_config(tmp_path: Path) -> None:
    async def _run() -> None:
        server = DashboardStaticServer(
            host="127.0.0.1",
            port=0,
            config=DashboardConfig(
                websocket_url="ws://127.0.0.1:9876",
                refresh_interval_seconds=9,
            ),
        )
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        try:
            index_status, index_headers, index_body = await _get("127.0.0.1", port, "/")
            config_status, config_headers, config_body = await _get(
                "127.0.0.1",
                port,
                "/dashboard-config.json",
            )
        finally:
            await server.stop()

        assert index_status == 200
        assert "text/html" in index_headers["content-type"]
        assert "运维监控台".encode("utf-8") in index_body
        assert config_status == 200
        assert "application/json" in config_headers["content-type"]
        assert json.loads(config_body.decode("utf-8")) == {
            "websocket_url": "ws://127.0.0.1:9876",
            "refresh_interval_seconds": 9,
        }

    asyncio.run(_run())


def test_dashboard_static_server_rejects_path_traversal() -> None:
    async def _run() -> None:
        server = DashboardStaticServer(host="127.0.0.1", port=0)
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        try:
            status, _headers, body = await _get("127.0.0.1", port, "/../pyproject.toml")
        finally:
            await server.stop()

        assert status == 404
        assert body == b"not found"

    asyncio.run(_run())


def test_dashboard_static_server_serves_feishu_onboarding_page_and_status() -> None:
    async def _run() -> None:
        server = DashboardStaticServer(
            host="127.0.0.1",
            port=0,
            onboarding=FakeOnboarding(),
        )
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        try:
            page_status, page_headers, page_body = await _get("127.0.0.1", port, "/onboarding/feishu")
            status_code, status_headers, status_body = await _get(
                "127.0.0.1",
                port,
                "/onboarding/feishu/status?session_id=ob_test",
            )
        finally:
            await server.stop()

        assert page_status == 200
        assert "text/html" in page_headers["content-type"]
        assert "GATEWAY-ABC123".encode("utf-8") in page_body
        assert "扫码后直接打开飞书机器人".encode("utf-8") in page_body
        assert status_code == 200
        assert "application/json" in status_headers["content-type"]
        assert json.loads(status_body.decode("utf-8"))["status"] == "bound"

    asyncio.run(_run())
