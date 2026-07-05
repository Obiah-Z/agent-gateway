import hashlib
import json
from pathlib import Path

from agent_gateway.gateways.feishu.security import (
    FeishuWebhookAuditLog,
    FeishuSignatureVerifier,
    FallbackWebhookEventDeduplicator,
    PostgresWebhookEventDeduplicator,
    RedisWebhookEventDeduplicator,
    WebhookEventDeduplicator,
    extract_event_id,
)


def test_feishu_signature_verifier_accepts_valid_signature() -> None:
    body = json.dumps({"challenge": "abc123"}, ensure_ascii=False).encode("utf-8")
    timestamp = "1710000000"
    nonce = "nonce-123"
    secret = "encrypt-key"
    signature = hashlib.sha256(
        timestamp.encode("utf-8")
        + nonce.encode("utf-8")
        + secret.encode("utf-8")
        + body
    ).hexdigest()

    ok, reason = FeishuSignatureVerifier(secret=secret, window_seconds=300).verify(
        headers={
            "x-lark-request-timestamp": timestamp,
            "x-lark-request-nonce": nonce,
            "x-lark-signature": signature,
        },
        body_bytes=body,
        now=1710000000.0,
    )

    assert ok is True
    assert reason == "signature verified"


def test_feishu_signature_verifier_rejects_expired_signature() -> None:
    body = b"{}"
    ok, reason = FeishuSignatureVerifier(secret="encrypt-key", window_seconds=300).verify(
        headers={
            "x-lark-request-timestamp": "1710000000",
            "x-lark-request-nonce": "nonce-123",
            "x-lark-signature": "bad",
        },
        body_bytes=body,
        now=1710001000.0,
    )

    assert ok is False
    assert reason == "signature timestamp expired"


def test_webhook_event_deduplicator_marks_duplicates(tmp_path: Path) -> None:
    dedup = WebhookEventDeduplicator(tmp_path / "dedup", ttl_seconds=60)

    first = dedup.mark_if_new("evt-1", now=1000.0)
    second = dedup.mark_if_new("evt-1", now=1001.0)
    third = dedup.mark_if_new("evt-1", now=1062.0)

    assert first is True
    assert second is False
    assert third is True


class FakeRedisConnection:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        self.calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class FakeRedisClient:
    enabled = True

    def __init__(self, connection: FakeRedisConnection | None = None, *, fail: bool = False) -> None:
        self.connection = connection or FakeRedisConnection()
        self.fail = fail

    def _get_client(self) -> FakeRedisConnection:
        if self.fail:
            raise RuntimeError("redis unavailable")
        return self.connection


class FakeFeishuStateRepository:
    enabled = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.seen: set[str] = set()
        self.events: list[dict[str, object]] = []
        self.dedup_calls: list[dict[str, object]] = []

    def mark_webhook_event_if_new(self, event_id: str, *, seen_at: float, expires_at: float) -> bool:
        self.dedup_calls.append({"event_id": event_id, "seen_at": seen_at, "expires_at": expires_at})
        if self.fail:
            raise RuntimeError("postgres unavailable")
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        return True

    def write_feishu_webhook_event(self, row: dict[str, object]) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("postgres unavailable")
        self.events.append(row)
        return row


def test_redis_webhook_event_deduplicator_uses_set_nx_ex() -> None:
    connection = FakeRedisConnection()
    dedup = RedisWebhookEventDeduplicator(
        FakeRedisClient(connection),
        ttl_seconds=60,
        key_prefix="test:webhook:event",
    )

    assert dedup.mark_if_new("account:evt-1") is True
    assert dedup.mark_if_new("account:evt-1") is False
    assert connection.calls[0] == {
        "key": "test:webhook:event:account:evt-1",
        "value": "1",
        "nx": True,
        "ex": 60,
    }


def test_postgres_webhook_event_deduplicator_marks_duplicates() -> None:
    repo = FakeFeishuStateRepository()
    dedup = PostgresWebhookEventDeduplicator(repo, ttl_seconds=60)

    assert dedup.mark_if_new("account:evt-1", now=1000.0) is True
    assert dedup.mark_if_new("account:evt-1", now=1001.0) is False
    assert repo.dedup_calls[0] == {
        "event_id": "account:evt-1",
        "seen_at": 1000.0,
        "expires_at": 1060.0,
    }


def test_fallback_webhook_event_deduplicator_uses_local_state_when_redis_fails(
    tmp_path: Path,
) -> None:
    dedup = FallbackWebhookEventDeduplicator(
        FakeRedisClient(fail=True),
        WebhookEventDeduplicator(tmp_path / "dedup", ttl_seconds=60),
    )

    assert dedup.mark_if_new("evt-1", now=1000.0) is True
    assert dedup.mark_if_new("evt-1", now=1001.0) is False


def test_fallback_webhook_event_deduplicator_uses_postgres_before_local(
    tmp_path: Path,
) -> None:
    repo = FakeFeishuStateRepository()
    dedup = FallbackWebhookEventDeduplicator(
        PostgresWebhookEventDeduplicator(repo, ttl_seconds=60),
        WebhookEventDeduplicator(tmp_path / "dedup", ttl_seconds=60),
    )

    assert dedup.mark_if_new("evt-1", now=1000.0) is True
    assert dedup.mark_if_new("evt-1", now=1001.0) is False
    assert not (tmp_path / "dedup" / "seen-events.jsonl").exists()


def test_feishu_webhook_audit_log_writes_postgres_and_local(tmp_path: Path) -> None:
    repo = FakeFeishuStateRepository()
    audit = FeishuWebhookAuditLog(tmp_path / "audit", repository=repo)

    audit.write(
        outcome="accepted",
        body={
            "header": {"event_id": "evt-1"},
            "event": {
                "message": {"message_id": "om_1", "chat_id": "oc_1", "chat_type": "p2p"},
                "sender": {"sender_id": {"open_id": "ou_1", "user_id": "u_1"}},
            },
        },
        headers={"x-lark-request-timestamp": "1"},
        http_status=200,
        reason="event accepted",
        channel_account="default",
        inbound={"sender_id": "ou_1"},
    )

    assert repo.events[0]["event_id"] == "evt-1"
    assert repo.events[0]["message_id"] == "om_1"
    assert repo.events[0]["metadata"] == {
        "headers": {
            "x-lark-request-timestamp": "1",
            "x-lark-request-nonce": "",
            "x-lark-signature": "",
        },
        "inbound": {"sender_id": "ou_1"},
    }
    local_rows = (tmp_path / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(local_rows) == 1
    assert json.loads(local_rows[0])["event_id"] == "evt-1"


def test_feishu_webhook_audit_log_keeps_local_when_postgres_fails(tmp_path: Path) -> None:
    audit = FeishuWebhookAuditLog(
        tmp_path / "audit",
        repository=FakeFeishuStateRepository(fail=True),
    )

    audit.write(
        outcome="error",
        body={"event_id": "evt-1"},
        headers={},
        http_status=400,
        reason="bad request",
    )

    local_rows = (tmp_path / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(local_rows) == 1
    assert json.loads(local_rows[0])["outcome"] == "error"


def test_extract_event_id_prefers_header_then_message_id() -> None:
    assert extract_event_id({"header": {"event_id": "evt-1"}}) == "evt-1"
    assert extract_event_id({"event": {"message": {"message_id": "om_1"}}}) == "om_1"
