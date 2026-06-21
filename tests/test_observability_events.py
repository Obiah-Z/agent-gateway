from datetime import date
from pathlib import Path

from agent_gateway.runtime.observability.events import RuntimeEventStore, ensure_correlation_id


def test_runtime_event_store_tails_events_and_filters_errors(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")

    first = store.record(
        "inbound.received",
        status="ok",
        component="dispatcher",
        message="received",
        channel="cli",
        metadata={"token": "secret-value", "text_length": 5},
    )
    second = store.record(
        "delivery.failed",
        status="failed",
        component="delivery",
        message="delivery failed",
        delivery_id="d1",
        error="channel unavailable",
    )

    events = store.tail(limit=10)
    errors = store.recent_errors(limit=10)

    assert [event["event_id"] for event in events] == [first["event_id"], second["event_id"]]
    assert events[0]["metadata"]["token"] == "[redacted]"
    assert [event["type"] for event in errors] == ["delivery.failed"]


def test_runtime_event_store_supports_tail_filters(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    store.record("agent.turn.started", status="ok", component="agent_loop", message="start")
    store.record(
        "cron.failed",
        status="error",
        component="cron",
        message="fail",
        correlation_id="corr-cron",
        agent_id="research",
        channel="cron",
        job_id="agent-news-digest",
    )

    events = store.tail(
        limit=10,
        component="cron",
        correlation_id="corr-cron",
        agent_id="research",
        channel="cron",
        job_id="agent-news-digest",
    )

    assert len(events) == 1
    assert events[0]["type"] == "cron.failed"

    assert store.tail(limit=10, component="delivery") == []
    assert store.recent_errors(limit=10, component="cron")[0]["type"] == "cron.failed"
    assert store.recent_errors(limit=10, correlation_id="missing") == []


def test_runtime_event_store_ignores_expected_rejections_in_recent_errors(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    store.record(
        "feishu.event.rejected",
        status="rejected",
        component="feishu",
        message="Feishu webhook request rejected",
        channel="feishu",
        error="method not allowed",
        metadata={"reason": "method not allowed"},
    )
    store.record(
        "feishu.event.rejected",
        status="rejected",
        component="feishu",
        message="Feishu signature rejected",
        channel="feishu",
        error="verification token mismatch",
        metadata={"reason": "verification token mismatch"},
    )

    events = store.tail(limit=10, component="feishu")
    errors = store.recent_errors(limit=10, component="feishu")

    assert [event["error"] for event in events] == [
        "method not allowed",
        "verification token mismatch",
    ]
    assert [event["error"] for event in errors] == ["verification token mismatch"]


def test_ensure_correlation_id_reuses_or_generates_value() -> None:
    metadata = {"correlation_id": "corr-existing"}
    assert ensure_correlation_id(metadata, prefix="cli") == "corr-existing"

    empty: dict[str, object] = {}
    generated = ensure_correlation_id(empty, prefix="CLI")

    assert generated.startswith("cli_")
    assert empty["correlation_id"] == generated


def test_runtime_event_store_rotates_by_day_and_tails_across_files(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events", retention_days=2000)
    old_timestamp = 1_704_067_200.0  # 2024-01-01T00:00:00Z
    new_timestamp = 1_704_153_600.0  # 2024-01-02T00:00:00Z

    store.record(
        "inbound.received",
        status="ok",
        component="dispatcher",
        message="old",
        metadata={"index": 1},
    )
    store.record(
        "route.resolved",
        status="ok",
        component="dispatcher",
        message="new",
        metadata={"index": 2},
    )

    # Rewrite timestamps through the private append path to keep the public API simple.
    for path in list((tmp_path / "events").glob("runtime-events-*.jsonl")):
        path.unlink()
    store._append(  # noqa: SLF001 - verifies rotation behavior without wall-clock coupling.
        {
            "event_id": "old",
            "timestamp": old_timestamp,
            "time": "2024-01-01T00:00:00+00:00",
            "type": "inbound.received",
            "status": "ok",
            "component": "dispatcher",
            "message": "old",
            "correlation_id": "corr-old",
            "agent_id": "",
            "session_key": "",
            "channel": "",
            "account_id": "",
            "peer_id": "",
            "delivery_id": "",
            "job_id": "",
            "error": "",
            "metadata": {},
        }
    )
    store._append(  # noqa: SLF001
        {
            "event_id": "new",
            "timestamp": new_timestamp,
            "time": "2024-01-02T00:00:00+00:00",
            "type": "route.resolved",
            "status": "ok",
            "component": "dispatcher",
            "message": "new",
            "correlation_id": "corr-new",
            "agent_id": "",
            "session_key": "",
            "channel": "",
            "account_id": "",
            "peer_id": "",
            "delivery_id": "",
            "job_id": "",
            "error": "",
            "metadata": {},
        }
    )

    files = sorted(path.name for path in (tmp_path / "events").glob("runtime-events-*.jsonl"))
    events = store.tail(limit=10)

    assert files == ["runtime-events-2024-01-01.jsonl", "runtime-events-2024-01-02.jsonl"]
    assert [event["event_id"] for event in events] == ["old", "new"]


def test_runtime_event_store_cleans_up_expired_files(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events", retention_days=2)
    old_file = tmp_path / "events" / "runtime-events-2024-01-01.jsonl"
    keep_file = tmp_path / "events" / "runtime-events-2024-01-03.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    keep_file.write_text("{}", encoding="utf-8")

    store.cleanup(now=date(2024, 1, 3))

    assert not old_file.exists()
    assert keep_file.exists()
