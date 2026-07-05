"""Webhook 请求安全、去重与飞书审计辅助。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """向 JSONL 文件追加一行，供审计和去重状态持久化使用。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class WebhookEventDeduplicator:
    """基于本地状态文件的通用 Webhook 事件去重器。"""

    def __init__(self, state_dir: Path, *, ttl_seconds: int = 86400) -> None:
        """初始化实例。"""
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.seen_file = self.state_dir / "seen-events.jsonl"
        self.ttl_seconds = max(60, ttl_seconds)
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._loaded = False

    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        """只有首次见到的事件才返回 True。"""

        if not event_id:
            return True
        current = now if now is not None else time.time()
        with self._lock:
            self._ensure_loaded()
            self._prune(current)
            expires_at = self._seen.get(event_id, 0.0)
            if expires_at > current:
                return False
            next_expiry = current + self.ttl_seconds
            self._seen[event_id] = next_expiry
            append_jsonl(
                self.seen_file,
                {
                    "event_id": event_id,
                    "seen_at": current,
                    "expires_at": next_expiry,
                },
            )
            return True

    def _ensure_loaded(self) -> None:
        """懒加载历史去重状态。"""

        if self._loaded:
            return
        if self.seen_file.exists():
            for line in self.seen_file.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_id = str(row.get("event_id", ""))
                expires_at = float(row.get("expires_at", 0.0) or 0.0)
                if event_id and expires_at:
                    self._seen[event_id] = expires_at
        self._loaded = True

    def _prune(self, now: float) -> None:
        """清理过期事件，避免内存表无限增长。"""

        expired = [event_id for event_id, expires_at in self._seen.items() if expires_at <= now]
        for event_id in expired:
            self._seen.pop(event_id, None)


class RedisWebhookEventDeduplicator:
    """基于 Redis `SET NX EX` 的通用 Webhook 事件去重器。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        ttl_seconds: int = 86400,
        key_prefix: str = "gateway:webhook:event",
    ) -> None:
        """初始化实例。"""

        self.redis_client = redis_client
        self.ttl_seconds = max(60, ttl_seconds)
        self.key_prefix = key_prefix.rstrip(":")

    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        """只有 Redis 首次写入成功时返回 True。"""

        del now
        if not event_id:
            return True
        client = self.redis_client._get_client()
        return bool(
            client.set(
                f"{self.key_prefix}:{event_id}",
                "1",
                nx=True,
                ex=self.ttl_seconds,
            )
        )


class PostgresWebhookEventDeduplicator:
    """基于 PostgreSQL 唯一键的通用 Webhook 事件去重器。"""

    def __init__(self, repository: Any, *, ttl_seconds: int = 86400) -> None:
        """初始化实例。"""

        self.repository = repository
        self.ttl_seconds = max(60, ttl_seconds)

    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        """只有 PostgreSQL 首次插入成功时返回 True。"""

        if not event_id:
            return True
        current = now if now is not None else time.time()
        return bool(
            self.repository.mark_webhook_event_if_new(
                event_id,
                seen_at=current,
                expires_at=current + self.ttl_seconds,
            )
        )


class FallbackWebhookEventDeduplicator:
    """优先使用主去重器，主去重器失败时回退到本地去重器。"""

    def __init__(self, primary: Any, fallback: WebhookEventDeduplicator) -> None:
        """初始化实例。"""

        self.primary = primary
        self.fallback = fallback

    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        """执行去重判断，并在 Redis 不可用时保持 Webhook 可用。"""

        try:
            return bool(self.primary.mark_if_new(event_id, now=now))
        except Exception:
            return self.fallback.mark_if_new(event_id, now=now)


@dataclass(slots=True)
class FeishuSignatureVerifier:
    """飞书 Webhook 签名校验器。"""

    secret: str
    window_seconds: int = 300

    def verify(
        self,
        *,
        headers: dict[str, str],
        body_bytes: bytes,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """验证签名头、时间窗和 HMAC 结果。"""

        if not self.secret:
            return True, "signature not configured"
        timestamp = headers.get("x-lark-request-timestamp", "")
        nonce = headers.get("x-lark-request-nonce", "")
        signature = headers.get("x-lark-signature", "")
        if not timestamp or not nonce or not signature:
            return False, "missing signature headers"
        try:
            request_ts = int(timestamp)
        except ValueError:
            return False, "invalid timestamp"
        current = int(now if now is not None else time.time())
        if abs(current - request_ts) > self.window_seconds:
            return False, "signature timestamp expired"
        expected = self._compute_signature(timestamp, nonce, body_bytes)
        if not _consteq(expected, signature):
            return False, "signature mismatch"
        return True, "signature verified"

    def _compute_signature(self, timestamp: str, nonce: str, body_bytes: bytes) -> str:
        """按飞书协议计算签名摘要。"""

        payload = b"".join(
            [
                timestamp.encode("utf-8"),
                nonce.encode("utf-8"),
                self.secret.encode("utf-8"),
                body_bytes,
            ]
        )
        return hashlib.sha256(payload).hexdigest()


class FeishuWebhookAuditLog:
    """把飞书请求写入 PostgreSQL，并保留本地 JSONL 审计兜底。"""

    def __init__(self, state_dir: Path, *, repository: Any = None) -> None:
        """初始化实例。"""
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.state_dir / "events.jsonl"
        self.repository = repository

    def write(
        self,
        *,
        outcome: str,
        body: dict[str, Any],
        headers: dict[str, str],
        http_status: int,
        reason: str,
        channel_account: str = "",
        inbound: dict[str, Any] | None = None,
    ) -> None:
        """记录一次 webhook 请求的处理结果。"""

        event = body.get("event", {}) if isinstance(body.get("event"), dict) else {}
        message = event.get("message", {}) if isinstance(event.get("message"), dict) else {}
        sender = event.get("sender", {}) if isinstance(event.get("sender"), dict) else {}
        sender_id = sender.get("sender_id", {}) if isinstance(sender.get("sender_id"), dict) else {}
        received_at = time.time()
        row = {
            "ts": received_at,
            "outcome": outcome,
            "reason": reason,
            "http_status": http_status,
            "channel_account": channel_account,
            "event_id": str(
                body.get("event_id")
                or body.get("header", {}).get("event_id", "")
                if isinstance(body.get("header"), dict)
                else body.get("event_id", "")
            ),
            "message_id": str(message.get("message_id", "")),
            "chat_id": str(message.get("chat_id", "")),
            "chat_type": str(message.get("chat_type", "")),
            "sender_open_id": str(sender_id.get("open_id", "")),
            "sender_user_id": str(sender_id.get("user_id", "")),
            "headers": {
                "x-lark-request-timestamp": headers.get("x-lark-request-timestamp", ""),
                "x-lark-request-nonce": headers.get("x-lark-request-nonce", ""),
                "x-lark-signature": headers.get("x-lark-signature", ""),
            },
            "body_sha256": hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "inbound": inbound or {},
        }
        if self.repository is not None:
            try:
                self.repository.write_feishu_webhook_event(
                    {
                        "id": f"feishu_evt_{uuid.uuid4().hex}",
                        "received_at": received_at,
                        "outcome": outcome,
                        "reason": reason,
                        "http_status": http_status,
                        "channel_account": channel_account,
                        "event_id": row["event_id"],
                        "message_id": row["message_id"],
                        "chat_id": row["chat_id"],
                        "chat_type": row["chat_type"],
                        "sender_open_id": row["sender_open_id"],
                        "sender_user_id": row["sender_user_id"],
                        "body_sha256": row["body_sha256"],
                        "metadata": {
                            "headers": row["headers"],
                            "inbound": row["inbound"],
                        },
                    }
                )
            except Exception:
                pass
        append_jsonl(self.events_file, row)


def extract_event_id(body: dict[str, Any]) -> str:
    """从不同层级的飞书事件结构中提取稳定事件 ID。"""

    if isinstance(body.get("header"), dict) and body["header"].get("event_id"):
        return str(body["header"]["event_id"])
    if body.get("event_id"):
        return str(body["event_id"])
    event = body.get("event", {})
    if isinstance(event, dict):
        message = event.get("message", {})
        if isinstance(message, dict) and message.get("message_id"):
            return str(message["message_id"])
    return ""


def _consteq(left: str, right: str) -> bool:
    """用哈希比较模拟常量时间比较，降低时序泄露风险。"""

    return hashlib.sha256(left.encode("utf-8")).digest() == hashlib.sha256(
        right.encode("utf-8")
    ).digest()
