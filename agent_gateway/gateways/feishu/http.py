"""飞书 Webhook 接入层。

这一层只负责 HTTP 入口、签名校验、事件去重、challenge 响应和把消息转成统一的
InboundMessage。真正的会话处理和消息回复仍然交给 application 层。
"""

from __future__ import annotations

import asyncio
import json
import traceback
from http import HTTPStatus
from pathlib import Path
from typing import Any

from agent_gateway.gateways.feishu.channel import FeishuChannel
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.wework.channel import WeWorkChannel
from agent_gateway.runtime.execution.channel_runtime import ChannelRuntime
from agent_gateway.gateways.feishu.security import (
    FeishuEventDeduplicator,
    FeishuSignatureVerifier,
    FeishuWebhookAuditLog,
    FallbackFeishuEventDeduplicator,
    PostgresFeishuEventDeduplicator,
    RedisFeishuEventDeduplicator,
    extract_event_id,
)
from agent_gateway.runtime.observability.events import RuntimeEventStore, new_correlation_id


class FeishuWebhookServer:
    """飞书事件订阅的 HTTP 入口。"""

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
        event_store: RuntimeEventStore | None = None,
        redis_client: Any = None,
        state_write_repository: Any = None,
    ) -> None:
        """初始化实例。"""
        self.host = host
        self.port = port
        self.path = path
        self.channels = channels
        self.channel_runtime = channel_runtime
        self.audit = FeishuWebhookAuditLog(state_dir, repository=state_write_repository)
        local_dedup = FeishuEventDeduplicator(
            state_dir / "dedup",
            ttl_seconds=dedup_ttl_seconds,
        )
        fallback_dedup: Any = local_dedup
        if state_write_repository is not None and getattr(state_write_repository, "enabled", False):
            fallback_dedup = FallbackFeishuEventDeduplicator(
                PostgresFeishuEventDeduplicator(
                    state_write_repository,
                    ttl_seconds=dedup_ttl_seconds,
                ),
                local_dedup,
            )
        if redis_client is not None and getattr(redis_client, "enabled", False):
            self.dedup = FallbackFeishuEventDeduplicator(
                RedisFeishuEventDeduplicator(
                    redis_client,
                    ttl_seconds=dedup_ttl_seconds,
                ),
                fallback_dedup,
            )
        else:
            self.dedup = fallback_dedup
        self.signature_window_seconds = max(30, signature_window_seconds)
        self.event_store = event_store
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        """启动后台服务。"""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        """停止后台服务。"""
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
        """处理一次 webhook 请求。

        顺序上先做鉴权和 challenge，再做去重、卡片动作或普通消息转发。
        """

        request_headers: dict[str, str] = {}
        request_body: dict[str, Any] = {}
        account_id = ""
        try:
            status, payload, request_headers, body_bytes, request_path = await self._read_request(reader)
            if status is not None:
                if self._is_non_event_probe(status, payload):
                    await self._write_json(writer, status, payload)
                    return
                self.audit.write(
                    outcome="rejected",
                    body=payload,
                    headers=request_headers,
                    http_status=status.value,
                    reason=payload.get("error", "request rejected"),
                )
                self._record_feishu_event(
                    "feishu.event.rejected",
                    status="rejected",
                    message="Feishu webhook request rejected",
                    reason=str(payload.get("error", "request rejected")),
                )
                await self._write_json(writer, status, payload)
                return

            if payload.get("_method") == "GET":
                wework_channel = self._resolve_wework_channel(request_path)
                if wework_channel is not None:
                    try:
                        echo = wework_channel.verify_url(str(payload.get("_query", "")))
                    except Exception as exc:
                        self._record_wework_event(
                            "wework.event.rejected",
                            status="rejected",
                            message="WeWork callback URL verification rejected",
                            account_id=wework_channel.account_id,
                            reason=str(exc),
                        )
                        await self._write_json(
                            writer,
                            HTTPStatus.UNAUTHORIZED,
                            {"error": "wework callback verification failed"},
                        )
                        return
                    self._record_wework_event(
                        "wework.event.accepted",
                        status="ok",
                        message="WeWork callback URL verified",
                        account_id=wework_channel.account_id,
                    )
                    await self._write_text(writer, HTTPStatus.OK, echo)
                    return
                await self._write_json(
                    writer,
                    HTTPStatus.OK,
                    {"ok": True, "kind": "feishu-webhook-health"},
                )
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
                self._record_feishu_event(
                    "feishu.event.rejected",
                    status="rejected",
                    message="Feishu channel not configured",
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
                        self._record_feishu_event(
                            "feishu.event.rejected",
                            status="rejected",
                            message="Feishu challenge verification token mismatch",
                            account_id=account_id,
                            reason="verification token mismatch",
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
                self._record_feishu_event(
                    "feishu.event.rejected",
                    status="rejected",
                    message="Feishu signature rejected",
                    account_id=account_id,
                    reason=reason,
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
                    self._record_feishu_event(
                        "feishu.event.rejected",
                        status="rejected",
                        message="Feishu challenge verification token mismatch",
                        account_id=account_id,
                        reason="verification token mismatch",
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
            correlation_id = f"feishu_{event_id}" if event_id else new_correlation_id("feishu")
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
                self._record_feishu_event(
                    "feishu.event.ignored",
                    status="duplicate",
                    message="Feishu duplicate event ignored",
                    account_id=account_id,
                    correlation_id=correlation_id,
                    reason="duplicate event",
                    metadata={"event_id": event_id},
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
                        self._record_feishu_event(
                            "feishu.event.error",
                            status="error",
                            message="Feishu card control action failed",
                            account_id=account_id,
                            correlation_id=correlation_id,
                            reason=str(exc),
                            metadata={"event_type": event_type},
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
                    self._record_feishu_event(
                        "feishu.event.accepted",
                        status="ok",
                        message="Feishu card control action accepted",
                        account_id=account_id,
                        correlation_id=correlation_id,
                        metadata={"event_type": event_type},
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
                    self._record_feishu_event(
                        "feishu.event.ignored",
                        status="ignored",
                        message="Feishu card action ignored",
                        account_id=account_id,
                        correlation_id=correlation_id,
                        reason="card action ignored by parser",
                        metadata={"event_type": event_type},
                    )
                    await self._write_json(writer, HTTPStatus.ACCEPTED, {"toast": {"type": "warning", "content": "action ignored"}})
                    return
                inbound.metadata["correlation_id"] = correlation_id
                if event_id:
                    inbound.metadata.setdefault("feishu_event_id", event_id)
                    inbound.metadata.setdefault("idempotency_key", f"feishu:{account_id}:{event_id}")
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
                self._record_feishu_event(
                    "feishu.event.accepted",
                    status="ok",
                    message="Feishu card action accepted",
                    account_id=account_id,
                    correlation_id=correlation_id,
                    inbound=inbound,
                    metadata={"event_type": event_type},
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
                self._record_feishu_event(
                    "feishu.event.ignored",
                    status="ignored",
                    message="Feishu event ignored",
                    account_id=account_id,
                    correlation_id=correlation_id,
                    reason="event ignored by parser",
                    metadata={"event_type": event_type},
                )
                await self._write_json(writer, HTTPStatus.ACCEPTED, {"ok": True, "ignored": True})
                return

            inbound.metadata["correlation_id"] = correlation_id
            if event_id:
                inbound.metadata.setdefault("feishu_event_id", event_id)
                inbound.metadata.setdefault("idempotency_key", f"feishu:{account_id}:{event_id}")
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
            self._record_feishu_event(
                "feishu.event.accepted",
                status="ok",
                message="Feishu event accepted",
                account_id=account_id,
                correlation_id=correlation_id,
                inbound=inbound,
                metadata={"event_type": event_type},
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
            self._record_feishu_event(
                "feishu.event.error",
                status="error",
                message="Feishu webhook request failed",
                account_id=account_id,
                reason=str(exc),
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
        """读取并校验最小 HTTP 请求体。"""

        request_line = await reader.readline()
        if not request_line:
            return HTTPStatus.BAD_REQUEST, {"error": "empty request"}, {}, b"", ""
        try:
            method, raw_path, _ = request_line.decode("utf-8").strip().split(" ", 2)
        except ValueError:
            return HTTPStatus.BAD_REQUEST, {"error": "invalid request line"}, {}, b"", ""
        method = method.upper()
        path, _, query_string = raw_path.partition("?")
        path = self._normalize_path(path)
        if method in {"GET", "HEAD"}:
            return None, {"_method": method, "_query": query_string}, {}, b"", path
        if method == "OPTIONS":
            return HTTPStatus.NO_CONTENT, {"ok": True}, {}, b"", path
        if method != "POST":
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"}, {}, b"", path

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

    @staticmethod
    def _is_non_event_probe(status: HTTPStatus, payload: dict[str, Any]) -> bool:
        """识别浏览器访问、探活和 CORS 预检等非飞书事件请求。"""

        return status in {HTTPStatus.OK, HTTPStatus.NO_CONTENT} and payload.get("ok") is True

    async def _write_json(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        """返回一个最小的 JSON HTTP 响应。"""

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

    async def _write_text(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        text: str,
    ) -> None:
        """返回一个纯文本 HTTP 响应。"""

        body = text.encode("utf-8")
        reason = status.phrase
        head = [
            f"HTTP/1.1 {status.value} {reason}",
            "Content-Type: text/plain; charset=utf-8",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(head).encode("utf-8") + body)
        await writer.drain()

    def _resolve_channel(self, request_path: str = "") -> FeishuChannel | None:
        """根据 webhook path 解析应该使用哪个 Feishu 账号。"""

        normalized_path = self._normalize_path(request_path or self.path)
        for _account, channel in self._iter_feishu_channels():
            if self._channel_webhook_path(channel) == normalized_path:
                return channel

        # 兼容早期单账号部署：当没有 account-level path 时，回退到进程级 FEISHU_WEBHOOK_PATH。
        if normalized_path == self._normalize_path(self.path):
            channel = self.channels.get("feishu")
            if isinstance(channel, FeishuChannel) and self._is_webhook_channel(channel):
                return channel
        return None

    def _resolve_wework_channel(self, request_path: str = "") -> WeWorkChannel | None:
        """根据 webhook path 解析应该使用哪个企业微信账号。"""

        normalized_path = self._normalize_path(request_path)
        for account, channel in self.channels.iter_channels():
            if account.channel != "wework" or not isinstance(channel, WeWorkChannel):
                continue
            raw_path = str(account.config.get("webhook_path", "") or "/webhooks/wework")
            if self._normalize_path(raw_path) == normalized_path:
                return channel
        return None

    def list_webhook_paths(self) -> list[tuple[str, str]]:
        """暴露当前所有 Feishu 账号对应的 webhook path。"""

        paths = [
            (channel.account_id, self._channel_webhook_path(channel))
            for _account, channel in self._iter_feishu_channels()
        ]
        for account, channel in self.channels.iter_channels():
            if account.channel == "wework" and isinstance(channel, WeWorkChannel):
                raw_path = str(account.config.get("webhook_path", "") or "/webhooks/wework")
                paths.append((channel.account_id, self._normalize_path(raw_path)))
        return paths

    def _iter_feishu_channels(self) -> list[tuple[object, FeishuChannel]]:
        """遍历已注册的 Feishu 通道实例。"""

        rows: list[tuple[object, FeishuChannel]] = []
        for account, channel in self.channels.iter_channels():
            if (
                account.channel == "feishu"
                and isinstance(channel, FeishuChannel)
                and self._is_webhook_channel(channel)
            ):
                rows.append((account, channel))
        return rows

    def _channel_webhook_path(self, channel: FeishuChannel) -> str:
        """读取账号级 webhook path；未配置时回退到服务器默认 path。"""

        raw_path = str(channel.account.config.get("webhook_path", "") or self.path)
        return self._normalize_path(raw_path)

    @staticmethod
    def _is_webhook_channel(channel: FeishuChannel) -> bool:
        """过滤长连接账号，避免它参与 Webhook 解密和路由。"""

        mode = str(channel.account.config.get("connection_mode", "")).lower()
        return mode != "long_connection"

    def _has_signature_headers(self, headers: dict[str, str]) -> bool:
        """判断请求是否携带飞书签名头。"""

        return bool(
            headers.get("x-lark-request-timestamp")
            or headers.get("x-lark-request-nonce")
            or headers.get("x-lark-signature")
        )

    def _record_feishu_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        account_id: str = "",
        correlation_id: str = "",
        reason: str = "",
        inbound: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录运行事件。"""
        if self.event_store is None:
            return
        payload = dict(metadata or {})
        if reason:
            payload["reason"] = reason
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="feishu",
                message=message,
                correlation_id=correlation_id,
                channel="feishu",
                account_id=account_id,
                peer_id=str(getattr(inbound, "peer_id", "")),
                error=reason if status in {"error", "rejected"} else "",
                metadata={
                    **payload,
                    "sender_id": str(getattr(inbound, "sender_id", "")),
                    "is_group": bool(getattr(inbound, "is_group", False)),
                },
            )
        except Exception:
            pass

    def _record_wework_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        account_id: str = "",
        reason: str = "",
    ) -> None:
        """记录企业微信回调运行事件。"""

        if self.event_store is None:
            return
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="wework",
                message=message,
                channel="wework",
                account_id=account_id,
                error=reason if status in {"error", "rejected"} else "",
                metadata={"reason": reason} if reason else {},
            )
        except Exception:
            pass

    def _challenge_token_matches(
        self,
        channel: FeishuChannel,
        body: dict[str, Any],
        *,
        require_token: bool,
    ) -> bool:
        """校验 challenge 请求里的 token 是否匹配。"""

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
        """返回飞书 challenge 响应。"""

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
        """统一 webhook path 格式，避免尾随斜杠和 query 干扰匹配。"""

        normalized = (path or "/").split("?", 1)[0].strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized.rstrip("/") or "/"

    def _extract_event_type(self, body: dict[str, Any]) -> str:
        """兼容不同飞书事件包装中的 event_type 字段。"""

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
        """构造一个可直接返回给飞书的 toast 响应。"""

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
