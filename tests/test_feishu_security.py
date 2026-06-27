import hashlib
import json
from pathlib import Path

from agent_gateway.gateways.feishu.security import (
    FeishuEventDeduplicator,
    FallbackFeishuEventDeduplicator,
    RedisFeishuEventDeduplicator,
    FeishuSignatureVerifier,
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


def test_feishu_event_deduplicator_marks_duplicates(tmp_path: Path) -> None:
    dedup = FeishuEventDeduplicator(tmp_path / "dedup", ttl_seconds=60)

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


def test_redis_feishu_event_deduplicator_uses_set_nx_ex() -> None:
    connection = FakeRedisConnection()
    dedup = RedisFeishuEventDeduplicator(
        FakeRedisClient(connection),
        ttl_seconds=60,
        key_prefix="test:feishu:event",
    )

    assert dedup.mark_if_new("account:evt-1") is True
    assert dedup.mark_if_new("account:evt-1") is False
    assert connection.calls[0] == {
        "key": "test:feishu:event:account:evt-1",
        "value": "1",
        "nx": True,
        "ex": 60,
    }


def test_fallback_feishu_event_deduplicator_uses_local_state_when_redis_fails(
    tmp_path: Path,
) -> None:
    dedup = FallbackFeishuEventDeduplicator(
        FakeRedisClient(fail=True),
        FeishuEventDeduplicator(tmp_path / "dedup", ttl_seconds=60),
    )

    assert dedup.mark_if_new("evt-1", now=1000.0) is True
    assert dedup.mark_if_new("evt-1", now=1001.0) is False


def test_extract_event_id_prefers_header_then_message_id() -> None:
    assert extract_event_id({"header": {"event_id": "evt-1"}}) == "evt-1"
    assert extract_event_id({"event": {"message": {"message_id": "om_1"}}}) == "om_1"
