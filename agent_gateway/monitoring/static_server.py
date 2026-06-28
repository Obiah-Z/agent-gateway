from __future__ import annotations

import asyncio
import io
import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote
from typing import Any

from agent_gateway.monitoring import STATIC_DIR
from agent_gateway.monitoring.prometheus import render_prometheus_metrics


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    """Dashboard 前端需要的最小运行配置。"""

    websocket_url: str
    refresh_interval_seconds: int = 15


class DashboardStaticServer:
    """极简静态 HTTP 服务。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        static_dir: Path = STATIC_DIR,
        config: DashboardConfig | None = None,
        onboarding: Any = None,
        control_plane: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.config = config or DashboardConfig(websocket_url="ws://127.0.0.1:8765")
        self.onboarding = onboarding
        self.control_plane = control_plane
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        """启动本地静态 HTTP 服务。"""

        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        """停止静态 HTTP 服务。"""

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await self._read_request(reader)
            if request is None:
                await self._write_bytes(
                    writer,
                    HTTPStatus.BAD_REQUEST,
                    b"bad request",
                    content_type="text/plain; charset=utf-8",
                )
                return
            method, path, raw_path = request
            if method == "POST":
                await self._route_post(reader, writer, path)
                return
            if method != "GET":
                await self._write_bytes(
                    writer,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    b"method not allowed",
                    content_type="text/plain; charset=utf-8",
                    headers={"Allow": "GET"},
                )
                return
            await self._route_get(writer, path, raw_path)
        except Exception:
            try:
                await self._write_bytes(
                    writer,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    b"internal server error",
                    content_type="text/plain; charset=utf-8",
                )
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_request(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, str, str] | None:
        """读取并解析一条最小 HTTP 请求头。"""

        request_line = await reader.readline()
        if not request_line:
            return None
        try:
            method, raw_path, _ = request_line.decode("utf-8").strip().split(" ", 2)
        except ValueError:
            return None
        while True:
            line = await reader.readline()
            if not line or line in {b"\r\n", b"\n"}:
                break
        return method.upper(), self._normalize_path(raw_path), raw_path

    async def _route_get(self, writer: asyncio.StreamWriter, path: str, raw_path: str) -> None:
        """处理 GET 请求路由。"""

        if path in {"", "/"}:
            await self._serve_file(writer, self.static_dir / "index.html")
            return
        if path.startswith("/onboarding/feishu/status"):
            await self._handle_onboarding_status(writer, raw_path)
            return
        if path.startswith("/onboarding/feishu/qr"):
            await self._handle_onboarding_qr(writer, raw_path)
            return
        if path.startswith("/onboarding/feishu"):
            await self._handle_onboarding_page(writer, raw_path)
            return
        if path == "/metrics":
            await self._handle_metrics(writer)
            return
        if path == "/dashboard-config.json":
            await self._write_json(
                writer,
                HTTPStatus.OK,
                {
                    "websocket_url": self.config.websocket_url,
                    "refresh_interval_seconds": self.config.refresh_interval_seconds,
                },
            )
            return

        relative_path = path.lstrip("/")
        file_path = (self.static_dir / relative_path).resolve()
        static_root = self.static_dir.resolve()
        if not self._is_relative_to(file_path, static_root) or not file_path.is_file():
            await self._write_bytes(
                writer,
                HTTPStatus.NOT_FOUND,
                b"not found",
                content_type="text/plain; charset=utf-8",
            )
            return
        await self._serve_file(writer, file_path)

    async def _route_post(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        path: str,
    ) -> None:
        """处理 POST 请求路由。"""

        if path != "/onboarding/feishu/start":
            await self._write_bytes(
                writer,
                HTTPStatus.NOT_FOUND,
                b"not found",
                content_type="text/plain; charset=utf-8",
            )
            return
        await self._handle_onboarding_start(reader, writer)

    async def _handle_onboarding_start(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """创建一条新的飞书 onboarding 会话。"""

        if self.onboarding is None:
            await self._write_json(writer, HTTPStatus.NOT_FOUND, {"error": "onboarding disabled"})
            return
        # Headers have already been consumed by _read_request in this simple server,
        # so the first version accepts query-less defaults for low-friction local use.
        session = self.onboarding.create_session(mode="personal")
        await self._write_json(writer, HTTPStatus.OK, session)

    async def _handle_metrics(self, writer: asyncio.StreamWriter) -> None:
        """Expose runtime metrics in Prometheus text format."""

        if self.control_plane is None:
            await self._write_bytes(
                writer,
                HTTPStatus.SERVICE_UNAVAILABLE,
                b"gateway_metrics_configured 0\ngateway_metrics_available 0\n",
                content_type="text/plain; version=0.0.4; charset=utf-8",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
            return
        summary = self.control_plane.metrics_summary(limit=60)
        await self._write_bytes(
            writer,
            HTTPStatus.OK,
            render_prometheus_metrics(summary).encode("utf-8"),
            content_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def _handle_onboarding_status(
        self,
        writer: asyncio.StreamWriter,
        path: str,
    ) -> None:
        """返回指定 onboarding 会话的状态。"""

        if self.onboarding is None:
            await self._write_json(writer, HTTPStatus.NOT_FOUND, {"error": "onboarding disabled"})
            return
        query = self._query_params(path)
        session_id = query.get("session_id", [""])[0]
        status = self.onboarding.status(session_id)
        if status is None:
            await self._write_json(writer, HTTPStatus.NOT_FOUND, {"error": "session not found"})
            return
        await self._write_json(writer, HTTPStatus.OK, status)

    async def _handle_onboarding_page(
        self,
        writer: asyncio.StreamWriter,
        path: str,
    ) -> None:
        """渲染 onboarding 页面。"""

        if self.onboarding is None:
            await self._write_bytes(
                writer,
                HTTPStatus.NOT_FOUND,
                b"onboarding disabled",
                content_type="text/plain; charset=utf-8",
            )
            return
        query = self._query_params(path)
        session_id = query.get("session_id", [""])[0]
        session = self.onboarding.status(session_id) if session_id else None
        if session is None:
            session = self.onboarding.create_session(mode="personal")
        html = self._render_onboarding_page(session)
        await self._write_bytes(
            writer,
            HTTPStatus.OK,
            html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def _handle_onboarding_qr(
        self,
        writer: asyncio.StreamWriter,
        path: str,
    ) -> None:
        """返回二维码图片或占位 SVG。"""

        query = self._query_params(path)
        text = query.get("text", [""])[0]
        png = self._render_qr_png(text)
        if png is not None:
            await self._write_bytes(
                writer,
                HTTPStatus.OK,
                png,
                content_type="image/png",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
            return
        svg = self._render_qr_placeholder(text)
        await self._write_bytes(
            writer,
            HTTPStatus.OK,
            svg.encode("utf-8"),
            content_type="image/svg+xml; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def _serve_file(self, writer: asyncio.StreamWriter, file_path: Path) -> None:
        if not file_path.is_file():
            await self._write_bytes(
                writer,
                HTTPStatus.NOT_FOUND,
                b"not found",
                content_type="text/plain; charset=utf-8",
            )
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif file_path.suffix in {".html", ".css"}:
            content_type = f"text/{file_path.suffix.lstrip('.')}; charset=utf-8"
        await self._write_bytes(
            writer,
            HTTPStatus.OK,
            file_path.read_bytes(),
            content_type=content_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _write_json(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        await self._write_bytes(
            writer,
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _write_bytes(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        head = [
            f"HTTP/1.1 {status.value} {status.phrase}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        for key, value in (headers or {}).items():
            head.append(f"{key}: {value}")
        head.extend(["", ""])
        writer.write("\r\n".join(head).encode("utf-8") + body)
        await writer.drain()

    @staticmethod
    def _normalize_path(raw_path: str) -> str:
        path = (raw_path or "/").split("?", 1)[0].strip() or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    @staticmethod
    def _query_params(raw_path: str) -> dict[str, list[str]]:
        if "?" not in raw_path:
            return {}
        return parse_qs(raw_path.split("?", 1)[1], keep_blank_values=True)

    @staticmethod
    def _render_onboarding_page(session: dict[str, Any]) -> str:
        session_id = str(session.get("session_id", ""))
        activation_text = str(session.get("activation_text", ""))
        binding_code = str(session.get("binding_code", ""))
        status = str(session.get("status", ""))
        bot_link = str(session.get("bot_link", ""))
        qr_target = str(session.get("qr_target", session.get("onboarding_url", "")))
        safe_activation = activation_text.replace("&", "&amp;").replace("<", "&lt;")
        safe_code = binding_code.replace("&", "&amp;").replace("<", "&lt;")
        safe_status = status.replace("&", "&amp;").replace("<", "&lt;")
        safe_bot_link = bot_link.replace("&", "&amp;").replace("<", "&lt;")
        qr_text = quote(qr_target, safe="")
        has_bot_link = bool(bot_link)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>飞书扫码接入 Gateway</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: ui-sans-serif, system-ui; background: linear-gradient(135deg, #f7efe2, #d9edf0); color: #1f2933; }}
    main {{ width: min(720px, calc(100vw - 32px)); background: rgba(255,255,255,.82); border: 1px solid rgba(31,41,51,.12); border-radius: 28px; padding: 32px; box-shadow: 0 24px 80px rgba(31,41,51,.18); }}
    h1 {{ margin: 0 0 12px; font-size: 32px; }}
    p {{ line-height: 1.7; }}
    .code {{ font-size: 30px; letter-spacing: .08em; font-weight: 800; padding: 18px 20px; background: #102a43; color: #f7efe2; border-radius: 18px; display: inline-block; }}
    .grid {{ display: grid; grid-template-columns: 220px 1fr; gap: 24px; align-items: center; margin-top: 24px; }}
    img {{ width: 220px; height: 220px; border-radius: 18px; background: white; }}
    .muted {{ color: #52606d; }}
    .status {{ margin-top: 18px; padding: 12px 14px; border-radius: 14px; background: rgba(16,42,67,.08); }}
    @media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <h1>飞书接入 Gateway</h1>
    <p class="muted">{'扫码后直接打开飞书机器人，用户在机器人里发第一句话即可自动接入。' if has_bot_link else '第一版无需飞书 OAuth：扫码打开本页后，在飞书里给机器人发送下面的绑定口令即可完成接入。'}</p>
    <div class="grid">
      <img alt="接入二维码" src="/onboarding/feishu/qr?text={qr_text}" />
      <section>
        <p>{'扫码后会打开如下机器人链接：' if has_bot_link else '在飞书私聊机器人，或在群里 @机器人，发送：'}</p>
        <div class="code">{safe_bot_link if has_bot_link else safe_activation}</div>
        <p class="muted">{'如果机器人侧没有自动接入成功，可再发送：' + safe_activation if has_bot_link else '绑定码：' + safe_code}</p>
      </section>
    </div>
    <div class="status">当前状态：<strong id="status">{safe_status}</strong></div>
  </main>
  <script>
    const sessionId = {json.dumps(session_id)};
    async function refresh() {{
      const res = await fetch(`/onboarding/feishu/status?session_id=${{encodeURIComponent(sessionId)}}`);
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById('status').textContent = data.status || 'unknown';
      if (data.status !== 'bound' && data.status !== 'expired' && data.status !== 'failed') {{
        setTimeout(refresh, 2000);
      }}
    }}
    setTimeout(refresh, 1500);
  </script>
</body>
</html>"""

    @staticmethod
    def _render_qr_placeholder(text: str) -> str:
        seed = sum(ord(ch) for ch in text)
        cells = []
        size = 9
        for y in range(size):
            for x in range(size):
                edge = (x < 2 and y < 2) or (x > 6 and y < 2) or (x < 2 and y > 6)
                filled = edge or ((seed + x * 17 + y * 31 + x * y) % 5 in {0, 2})
                if filled:
                    cells.append(f'<rect x="{x * 20 + 20}" y="{y * 20 + 20}" width="16" height="16" rx="3"/>')
        label = text.replace("&", "&amp;").replace("<", "&lt;")[:18]
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">'
            '<rect width="220" height="220" fill="#fff"/>'
            '<g fill="#102a43">'
            + "".join(cells)
            + f'</g><text x="110" y="205" text-anchor="middle" font-size="10" fill="#52606d">{label}</text></svg>'
        )

    @staticmethod
    def _render_qr_png(text: str) -> bytes | None:
        try:
            import qrcode
        except ImportError:
            return None
        image = qrcode.make(text or "gateway-onboarding")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
