import asyncio
import json
from types import SimpleNamespace

import scripts.load_test_gateway as load_test_gateway
from scripts.load_test_gateway import (
    build_result,
    main,
    percentile,
    run_delivery_local,
    run_delivery_rabbitmq,
    run_model_real,
    render_markdown,
    run_mock_local,
    write_reports,
)


class FakeRabbitMQBroker:
    def __init__(self, **kwargs) -> None:
        self.messages: list[dict] = []
        self.dead: list[dict] = []
        self.enabled = kwargs.get("enabled", False)

    def publish(self, entry) -> None:
        if not self.enabled:
            return
        self.messages.append({"delivery_id": entry.id})

    def ack(self, delivery_id: str) -> None:
        return None

    def retry(self, entry) -> None:
        return None

    def dead_letter(self, entry) -> None:
        self.dead.append({"delivery_id": entry.id})

    def discard(self, delivery_id: str) -> None:
        return None

    def stats(self):
        return {
            "backend": "fake-rabbitmq",
            "enabled": self.enabled,
            "messages": len(self.messages),
            "dead_letter_messages": len(self.dead),
        }

    def consume_once(self, handler):
        if not self.messages:
            return False
        payload = self.messages.pop(0)
        if not handler(payload):
            self.messages.append(payload)
        return True

    def close(self) -> None:
        return None


class FakeRunner:
    async def run_turn(self, agent_id, session_key, user_text, *, channel="", correlation_id=""):
        return SimpleNamespace(text="pong", stop_reason="end_turn", tool_calls=[])


class FakeApp:
    def __init__(self) -> None:
        self.runner = FakeRunner()
        self.settings = SimpleNamespace(
            model_id="fake-model",
            anthropic_base_url="https://example.test",
            anthropic_api_key="fake-key",
        )


def test_percentile_uses_nearest_rank() -> None:
    values = [1, 2, 3, 4, 5]

    assert percentile(values, 50) == 3
    assert percentile(values, 95) == 5
    assert percentile([], 95) == 0.0


def test_mock_local_load_test_generates_summary_and_reports(tmp_path) -> None:
    samples, wall_seconds = asyncio.run(
        run_mock_local(
            requests=5,
            concurrency=2,
            agent_delay_ms=0,
            delivery_delay_ms=0,
        )
    )
    result = build_result(
        scenario="mock-local",
        requests=5,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
    )

    assert result["summary"]["success"] == 5
    assert result["summary"]["failed"] == 0
    assert result["summary"]["throughput_rps"] > 0
    assert result["scenario"]["uses_real_model"] is False
    assert result["scenario"]["uses_real_feishu"] is False

    json_path, md_path = write_reports(result, tmp_path, "mock")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")

    assert payload["scenario"]["name"] == "mock-local"
    assert "# AI Agent Gateway 压测报告" in markdown
    assert "E2E P50 / P95 / P99" in markdown
    assert render_markdown(result).startswith("# AI Agent Gateway 压测报告")


def test_delivery_local_load_test_uses_real_delivery_queue(tmp_path) -> None:
    samples, wall_seconds, context = asyncio.run(
        run_delivery_local(
            requests=4,
            concurrency=2,
            delivery_delay_ms=0,
            work_dir=tmp_path / "work",
        )
    )
    result = build_result(
        scenario="delivery-local",
        requests=4,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 4
    assert result["summary"]["failed"] == 0
    assert result["summary"]["max_delivery_backlog"] == 4
    assert result["scenario"]["uses_real_delivery_queue"] is True
    assert context["sent"] == 4
    assert "delivery-local 基线" in render_markdown(result)


def test_delivery_rabbitmq_load_test_uses_broker_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(load_test_gateway, "RabbitMQDeliveryBroker", FakeRabbitMQBroker)

    samples, wall_seconds, context = asyncio.run(
        run_delivery_rabbitmq(
            requests=4,
            concurrency=2,
            delivery_delay_ms=0,
            work_dir=tmp_path / "work",
            rabbitmq_url="amqp://example",
            rabbitmq_exchange="ex",
            rabbitmq_queue="q",
            rabbitmq_dead_letter_exchange="dlx",
            rabbitmq_dead_letter_queue="dlq",
            connect_timeout_seconds=0.2,
        )
    )
    result = build_result(
        scenario="delivery-rabbitmq",
        requests=4,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 4
    assert result["scenario"]["uses_rabbitmq"] is True
    assert result["scenario"]["uses_real_delivery_queue"] is True
    assert context["sent"] == 4
    assert context["broker_after_publish"]["messages"] == 4
    assert context["broker_after_consume"]["messages"] == 0
    assert "delivery-rabbitmq 基线" in render_markdown(result)


def test_model_real_requires_explicit_external_opt_in() -> None:
    try:
        main(["--scenario", "model-real", "--requests", "1"])
    except SystemExit as exc:
        assert "allow-real-external" in str(exc)
    else:  # pragma: no cover - regression guard.
        raise AssertionError("model-real should require --allow-real-external")


def test_model_real_load_test_uses_real_model_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(load_test_gateway, "build_gateway_application", lambda: FakeApp())

    samples, wall_seconds, context = asyncio.run(
        run_model_real(
            requests=3,
            concurrency=2,
            agent_id="main",
            session_prefix="test-load",
            prompt="pong",
        )
    )
    result = build_result(
        scenario="model-real",
        requests=3,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 3
    assert result["scenario"]["uses_real_model"] is True
    assert result["scenario"]["uses_real_feishu"] is False
    assert context["model_id"] == "fake-model"
    assert "model-real 基线" in render_markdown(result)
