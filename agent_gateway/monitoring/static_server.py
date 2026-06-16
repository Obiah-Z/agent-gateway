from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from agent_gateway.monitoring import STATIC_DIR


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    websocket_url: str
    refresh_interval_seconds: int = 15


class DashboardStaticServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        static_dir: Path = STATIC_DIR,
        config: DashboardConfig | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.config = config or DashboardConfig(websocket_url="ws://127.0.0.1:8765")
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
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
            method, path = request
            if method != "GET":
                await self._write_bytes(
                    writer,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    b"method not allowed",
                    content_type="text/plain; charset=utf-8",
                    headers={"Allow": "GET"},
                )
                return
            await self._route_get(writer, path)
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
    ) -> tuple[str, str] | None:
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
        return method.upper(), self._normalize_path(raw_path)

    async def _route_get(self, writer: asyncio.StreamWriter, path: str) -> None:
        if path in {"", "/"}:
            await self._serve_file(writer, self.static_dir / "index.html")
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
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
