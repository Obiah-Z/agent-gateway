from pathlib import Path

from agent_gateway.application.alerts_runtime import AlertsRuntime
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.core.models import ProactiveTarget
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.application.dispatcher import GatewayDispatcher
from agent_gateway.observability.alerts import AlertStore
from agent_gateway.observability.events import RuntimeEventStore
from agent_gateway.observability.metrics import MetricsStore


def test_alerts_runtime_triggers_and_recovers_metric_rules(tmp_path: Path) -> None:
    metrics = MetricsStore(tmp_path / "metrics", retention_days=2000)
    alerts = AlertStore(tmp_path / "alerts", retention_days=2000)
    runtime = AlertsRuntime(
        metrics_store=metrics,
        alert_store=alerts,
        interval_seconds=60,
    )

    metrics.record(delivery={"pending": 5, "failed": 0}, lanes={"queued": 1}, profiles={"available": 1})
    runtime.evaluate_once()
    assert runtime.active_alerts() == []

    metrics.record(delivery={"pending": 30, "failed": 0}, lanes={"queued": 1}, profiles={"available": 1})
    runtime.evaluate_once()
    assert runtime.active_alerts() == []

    metrics.record(delivery={"pending": 35, "failed": 0}, lanes={"queued": 1}, profiles={"available": 1})
    runtime.evaluate_once()
    metrics.record(delivery={"pending": 40, "failed": 0}, lanes={"queued": 1}, profiles={"available": 1})
    runtime.evaluate_once()

    active = runtime.active_alerts()
    history = runtime.recent_history(limit=10)

    assert [row["rule_id"] for row in active] == ["delivery_pending_backlog"]
    assert history[-1]["event"] == "triggered"

    metrics.record(delivery={"pending": 0, "failed": 0}, lanes={"queued": 0}, profiles={"available": 1})
    runtime.evaluate_once()

    assert runtime.active_alerts() == []
    assert runtime.recent_history(limit=10)[-1]["event"] == "recovered"


def test_alerts_runtime_uses_event_store_for_feishu_signature_rejections(tmp_path: Path) -> None:
    metrics = MetricsStore(tmp_path / "metrics")
    alerts = AlertStore(tmp_path / "alerts", retention_days=2000)
    events = RuntimeEventStore(tmp_path / "events", retention_days=2000)
    runtime = AlertsRuntime(
        metrics_store=metrics,
        alert_store=alerts,
        event_store=events,
        interval_seconds=60,
    )
    metrics.record(delivery={"pending": 0}, lanes={"queued": 0}, profiles={"available": 1})
    for index in range(3):
        events.record(
            "feishu.event.rejected",
            status="rejected",
            component="feishu",
            message="Feishu signature rejected",
            metadata={"index": index},
        )

    runtime.evaluate_once()

    history = runtime.recent_history(limit=10)
    assert history[-1]["rule"]["id"] == "feishu_signature_rejected_spike"
    assert history[-1]["event"] == "triggered"


class _FakeRunner:
    event_store = None


def test_alerts_runtime_delivers_notifications_via_dispatcher(tmp_path: Path) -> None:
    metrics = MetricsStore(tmp_path / "metrics", retention_days=2000)
    alerts = AlertStore(tmp_path / "alerts", retention_days=2000)
    delivery_queue = DeliveryQueue(tmp_path / "delivery")
    dispatcher = GatewayDispatcher(
        agents=type("Agents", (), {})(),
        bindings=type("Bindings", (), {})(),
        runner=_FakeRunner(),
        command_queue=type("Queue", (), {})(),
        delivery_queue=delivery_queue,
    )
    runtime = AlertsRuntime(
        metrics_store=metrics,
        alert_store=alerts,
        dispatcher=dispatcher,
        channels=ChannelManager(),
        target=ProactiveTarget(channel="feishu", account_id="feishu-main", peer_id="ou_xxx", agent_id="main"),
        interval_seconds=60,
    )

    metrics.record(delivery={"pending": 30, "failed": 0}, lanes={"queued": 0}, profiles={"available": 1})
    runtime.evaluate_once()
    metrics.record(delivery={"pending": 35, "failed": 0}, lanes={"queued": 0}, profiles={"available": 1})
    runtime.evaluate_once()
    metrics.record(delivery={"pending": 40, "failed": 0}, lanes={"queued": 0}, profiles={"available": 1})
    emitted = runtime.evaluate_once()

    import asyncio
    asyncio.run(runtime._deliver_notifications(emitted))

    pending = delivery_queue.pending_entries()
    assert len(pending) == 1
    assert pending[0].channel == "feishu"
    assert pending[0].metadata["kind"] == "alert"
    assert "告警触发" in pending[0].text
