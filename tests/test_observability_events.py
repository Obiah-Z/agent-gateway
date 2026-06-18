from pathlib import Path

from agent_gateway.observability.events import RuntimeEventStore


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
    store.record("cron.failed", status="error", component="cron", message="fail")

    events = store.tail(limit=10, component="cron")

    assert len(events) == 1
    assert events[0]["type"] == "cron.failed"
