from __future__ import annotations

import asyncio
import json
import traceback
from http import HTTPStatus
from pathlib import Path
from typing import Any

from agent_gateway.channels.feishu import FeishuChannel
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.runtime.channel_runtime import ChannelRuntime
from agent_gateway.runtime.feishu_security import (
    FeishuEventDeduplicator,
    FeishuSignatureVerifier,
    FeishuWebhookAuditLog,
    extract_event_id,
)


class FeishuWebhookServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        channels: ChannelManager,
        channel_runtime: ChannelRuntime,
        state_dir: Path,
        signature_window_seconds: int = 300,
        dedup_ttl_seconds: int = 86400,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.channels = channels
        self.channel_runtime = channel_runtime
        self.audit = FeishuWebhookAuditLog(state_dir)
        self.dedup = FeishuEventDeduplicator(
            state_dir / "dedup",
            ttl_seconds=dedup_ttl_seconds,
        )
        self.signature_window_seconds = max(30, signature_window_seconds)
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
        request_headers: dict[str, str] = {}
        request_body: dict[str, Any] = {}
        account_id = ""
        try:
            status, payload, request_headers, body_bytes, request_path = await self._read_request(reader)
            if status is not None:
                self.audit.write(
                    outcome="rejected",
                    body=payload,
                    headers=request_headers,
                    http_status=status.value,
                    reason=payload.get("error", "request rejected"),
                )
                await self._write_json(writer, status, payload)
                return

            channel = self._resolve_channel(request_path)
            if channel is None:
                request_body = payload
                self.audit.write(
                    outcome="rejected",
                    body=request_body,
                    headers=request_headers,
                    http_status=HTTPStatus.NOT_FOUND.value,
                    reason="feishu channel not configured",
                )
                await self._write_json(writer, HTTPStatus.NOT_FOUND, {"error": "feishu channel not configured"})
                return
            account_id = channel.account_id

            if not self._has_signature_headers(request_headers):
                body = channel.decode_payload(payload)
                request_body = body
                if "challenge" in body:
                    if not self._challenge_token_matches(channel, body, require_token=True):
                        self.audit.write(
                            outcome="rejected",
                            body=body,
                            headers=request_headers,
                            http_status=HTTPStatus.UNAUTHORIZED.value,
                            reason="verification token mismatch",
                            channel_account=account_id,
                        )
                        await self._write_json(
                            writer,
                            HTTPStatus.UNAUTHORIZED,
                            {"error": "verification token mismatch"},
                        )
                        return
                    await self._accept_challenge(writer, channel, body, request_headers)
                    return

            ok, reason = FeishuSignatureVerifier(
                secret=channel.encrypt_key,
                window_seconds=self.signature_window_seconds,
            ).verify(
                headers=request_headers,
                body_bytes=body_bytes,
            )
            if not ok:
                request_body = payload
                self.audit.write(
                    outcome="rejected",
                    body=request_body,
                    headers=request_headers,
                    http_status=HTTPStatus.UNAUTHORIZED.value,
                    reason=reason,
                    channel_account=account_id,
                )
                await self._write_json(writer, HTTPStatus.UNAUTHORIZED, {"error": reason})
                return

            body = channel.decode_payload(payload)
            request_body = body
            if "challenge" in body:
                if not self._challenge_token_matches(channel, body, require_token=False):
                    self.audit.write(
                        outcome="rejected",
                        body=body,
                        headers=request_headers,
                        http_status=HTTPStatus.UNAUTHORIZED.value,
                        reason="verification token mismatch",
                        channel_account=account_id,
                    )
                    await self._write_json(
                        writer,
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "verification token mismatch"},
                    )
                    return
                await self._accept_challenge(writer, channel, body, request_headers)
                return

            event_id = extract_event_id(body)
            dedup_key = f"{account_id}:{event_id}" if event_id else ""
            if not self.dedup.mark_if_new(dedup_key):
                print(f"[feishu] webhook duplicate ignored: event_id={event_id}")
                self.audit.write(
                    outcome="duplicate",
                    body=body,
                    headers=request_headers,
                    http_status=HTTPStatus.ACCEPTED.value,
                    reason="duplicate event",
                    channel_account=account_id,
                )
                await self._write_json(writer, HTTPStatus.ACCEPTED, {"ok": True, "duplicate": True})
                return

            token = str(body.get("token", ""))
            event_type = self._extract_event_type(body)
            if event_type == "card.action.trigger":
                if channel.is_control_card_action(body):
                    try:
                        response_payload = channel.handle_control_card_action(body)
                    except Exception as exc:
                        print("[feishu] control card action failed")
                        traceback.print_exc()
                        self.audit.write(
                            outcome="error",
                            body=body,
                            headers=request_headers,
                            http_status=HTTPStatus.OK.value,
                            reason=f"card control action failed: {exc}",
                            channel_account=account_id,
                        )
                        await self._write_json(
                            writer,
                            HTTPStatus.OK,
                            self._card_action_toast(
                                "卡片更新失败，请稍后重试",
                                "Card update failed, please try again later",
                                toast_type="error",
                            ),
                        )
                        return
                    if response_payload is None:
                        response_payload = self._card_action_toast(
                            "暂不支持该卡片操作",
                            "Unsupported card action",
                            toast_type="warning",
                        )
                    self.audit.write(
                        outcome="accepted",
                        body=body,
                        headers=request_headers,
                        http_status=HTTPStatus.OK.value,
                        reason="card control action accepted",
                        channel_account=account_id,
                    )
                    await self._write_json(writer, HTTPStatus.OK, response_payload)
                    return
                inbound = channel.parse_card_action(body, token=token)
                if inbound is None:
                    print("[feishu] webhook card action ignored")
                    self.audit.write(
                        outcome="ignored",
                        body=body,
                        headers=request_headers,
                        http_status=HTTPStatus.ACCEPTED.value,
                        reason="card action ignored by parser",
                        channel_account=account_id,
                    )
                    await self._write_json(writer, HTTPStatus.ACCEPTED, {"toast": {"type": "warning", "content": "action ignored"}})
                    return
                print(
                    "[feishu] webhook card action accepted:"
                    f" account={inbound.account_id}"
                    f" sender={inbound.sender_id}"
                    f" peer={inbound.peer_id}"
                )
                await self._write_json(
                    writer,
                    HTTPStatus.OK,
                    {
                        "toast": {
                            "type": "info",
                            "content": "已收到操作，正在处理",
                            "i18n": {
                                "zh_cn": "已收到操作，正在处理",
                                "en_us": "Action received, processing",
                            },
                        }
                    },
                )
                self.audit.write(
                    outcome="accepted",
                    body=body,
                    headers=request_headers,
                    http_status=HTTPStatus.OK.value,
                    reason="card action accepted",
                    channel_account=account_id,
                    inbound={
                        "sender_id": inbound.sender_id,
                        "peer_id": inbound.peer_id,
                        "is_group": inbound.is_group,
                        "receive_id_type": inbound.metadata.get("receive_id_type", ""),
                        "kind": inbound.metadata.get("kind", ""),
                    },
                )
                asyncio.create_task(self.channel_runtime.ingest_external(inbound))
                return

            inbound = channel.parse_event(body, token=token)
            if inbound is None:
                print("[feishu] webhook event ignored")
                self.audit.write(
                    outcome="ignored",
                    body=body,
                    headers=request_headers,
                    http_status=HTTPStatus.ACCEPTED.value,
                    reason="event ignored by parser",
                    channel_account=account_id,
                )
                await self._write_json(writer, HTTPStatus.ACCEPTED, {"ok": True, "ignored": True})
                return

            print(
                "[feishu] webhook event accepted:"
                f" account={inbound.account_id}"
                f" sender={inbound.sender_id}"
                f" peer={inbound.peer_id}"
            )
            await self.channel_runtime.ingest_external(inbound)
            self.audit.write(
                outcome="accepted",
                body=body,
                headers=request_headers,
                http_status=HTTPStatus.OK.value,
                reason="event accepted",
                channel_account=account_id,
                inbound={
                    "sender_id": inbound.sender_id,
                    "peer_id": inbound.peer_id,
                    "is_group": inbound.is_group,
                    "receive_id_type": inbound.metadata.get("receive_id_type", ""),
                },
            )
            await self._write_json(writer, HTTPStatus.OK, {"ok": True})
        except Exception as exc:
            print("[feishu] webhook request failed during processing")
            traceback.print_exc()
            self.audit.write(
                outcome="error",
                body=request_body,
                headers=request_headers,
                http_status=HTTPStatus.BAD_REQUEST.value,
                reason=str(exc),
                channel_account=account_id,
            )
            try:
                await self._write_json(writer, HTTPStatus.BAD_REQUEST, {"error": "bad request"})
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
    ) -> tuple[HTTPStatus | None, dict[str, Any], dict[str, str], bytes, str]:
        request_line = await reader.readline()
        if not request_line:
            return HTTPStatus.BAD_REQUEST, {"error": "empty request"}, {}, b"", ""
        try:
            method, raw_path, _ = request_line.decode("utf-8").strip().split(" ", 2)
        except ValueError:
            return HTTPStatus.BAD_REQUEST, {"error": "invalid request line"}, {}, b"", ""
        if method.upper() != "POST":
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"}, {}, b"", ""
        path = raw_path.split("?", 1)[0]
        path = self._normalize_path(path)

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("utf-8").strip()
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            return HTTPStatus.BAD_REQUEST, {"error": "invalid content-length"}, headers, b"", path
        body_bytes = await reader.readexactly(content_length) if content_length > 0 else b""
        if not body_bytes:
            return HTTPStatus.BAD_REQUEST, {"error": "empty body"}, headers, body_bytes, path
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HTTPStatus.BAD_REQUEST, {"error": "invalid json"}, headers, body_bytes, path
        if not isinstance(body, dict):
            return HTTPStatus.BAD_REQUEST, {"error": "json body must be object"}, headers, body_bytes, path
        return None, body, headers, body_bytes, path

    async def _write_json(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reason = status.phrase
        head = [
            f"HTTP/1.1 {status.value} {reason}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(head).encode("utf-8") + body)
        await writer.drain()

    def _resolve_channel(self, request_path: str = "") -> FeishuChannel | None:
        normalized_path = self._normalize_path(request_path or self.path)
        for _account, channel in self._iter_feishu_channels():
            if self._channel_webhook_path(channel) == normalized_path:
                return channel

        # Backward compatibility: single Feishu account deployments used the
        # process-level FEISHU_WEBHOOK_PATH before account-level paths existed.
        if normalized_path == self._normalize_path(self.path):
            channel = self.channels.get("feishu")
            if isinstance(channel, FeishuChannel):
                return channel
        return None

    def list_webhook_paths(self) -> list[tuple[str, str]]:
        return [
            (channel.account_id, self._channel_webhook_path(channel))
            for _account, channel in self._iter_feishu_channels()
        ]

    def _iter_feishu_channels(self) -> list[tuple[object, FeishuChannel]]:
        rows: list[tuple[object, FeishuChannel]] = []
        for account, channel in self.channels.iter_channels():
            if account.channel == "feishu" and isinstance(channel, FeishuChannel):
                rows.append((account, channel))
        return rows

    def _channel_webhook_path(self, channel: FeishuChannel) -> str:
        raw_path = str(channel.account.config.get("webhook_path", "") or self.path)
        return self._normalize_path(raw_path)

    def _has_signature_headers(self, headers: dict[str, str]) -> bool:
        return bool(
            headers.get("x-lark-request-timestamp")
            or headers.get("x-lark-request-nonce")
            or headers.get("x-lark-signature")
        )

    def _challenge_token_matches(
        self,
        channel: FeishuChannel,
        body: dict[str, Any],
        *,
        require_token: bool,
    ) -> bool:
        if not channel.verification_token:
            return True
        token = str(body.get("token", ""))
        if not token:
            return not require_token
        return token == channel.verification_token

    async def _accept_challenge(
        self,
        writer: asyncio.StreamWriter,
        channel: FeishuChannel,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        print("[feishu] webhook challenge received")
        self.audit.write(
            outcome="challenge",
            body=body,
            headers=headers,
            http_status=HTTPStatus.OK.value,
            reason="challenge",
            channel_account=channel.account_id,
        )
        await self._write_json(writer, HTTPStatus.OK, {"challenge": body.get("challenge", "")})

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = (path or "/").split("?", 1)[0].strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized.rstrip("/") or "/"

    def _extract_event_type(self, body: dict[str, Any]) -> str:
        header = body.get("header", {})
        if isinstance(header, dict) and header.get("event_type"):
            return str(header.get("event_type", ""))
        return str(body.get("event_type", ""))

    def _card_action_toast(
        self,
        zh_cn: str,
        en_us: str,
        *,
        toast_type: str,
    ) -> dict[str, Any]:
        return {
            "toast": {
                "type": toast_type,
                "content": zh_cn,
                "i18n": {
                    "zh_cn": zh_cn,
                    "en_us": en_us,
                },
            }
        }
