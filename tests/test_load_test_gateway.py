import asyncio
import json
import threading
from types import SimpleNamespace

import scripts.load_test_gateway as load_test_gateway
from scripts.load_test_gateway import (
    build_gateway_application_async,
    build_result,
    main,
    percentile,
    run_delivery_local,
    run_delivery_rabbitmq,
    run_feishu_send_real,
    run_inbound_rabbitmq,
    run_model_real,
    render_markdown,
    run_mock_local,
    write_reports,
)


class FakeRabbitMQBroker:
    messages: list[dict] = []
    dead: list[dict] = []

    def __init__(self, **kwargs) -> None:
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

    def purge(self):
        purged = len(self.messages)
        dead = len(self.dead)
        self.messages.clear()
        self.dead.clear()
        return {"messages": purged, "dead_letter_messages": dead}

    def close(self) -> None:
        return None


class FakeInboundRabbitMQBroker:
    queues: dict[str, list[dict]] = {}
    dead: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.enabled = kwargs.get("enabled", False)
        self.exchange = kwargs.get("exchange", "")
        self.queue_prefix = kwargs.get("queue_prefix", "")
        self.dead_letter_exchange = kwargs.get("dead_letter_exchange", "")
        self.dead_letter_queue = kwargs.get("dead_letter_queue", "")
        self.partitions = max(1, int(kwargs.get("partitions", 1)))
        self.prefetch = max(1, int(kwargs.get("prefetch", 1)))
        for partition in range(self.partitions):
            self.queues.setdefault(self.queue_name(partition), [])

    def publish(self, task) -> None:
        if not self.enabled:
            return
        partition = self.partition_for(task.session_key or task.id)
        self.queues.setdefault(self.queue_name(partition), []).append(
            {
                "task_id": task.id,
                "task_type": task.task_type,
                "session_key": task.session_key,
                "partition": partition,
                "idempotency_key": task.idempotency_key,
            }
        )

    def consume_once(self, partition: int, handler) -> bool:
        queue = self.queues.setdefault(self.queue_name(partition), [])
        if not queue:
            return False
        payload = queue.pop(0)
        if not handler(payload):
            queue.append(payload)
        return True

    def stats(self):
        queues = [
            {
                "partition": partition,
                "queue": self.queue_name(partition),
                "messages": len(self.queues.get(self.queue_name(partition), [])),
                "consumers": 0,
            }
            for partition in range(self.partitions)
        ]
        return {
            "backend": "fake-rabbitmq-inbound",
            "enabled": self.enabled,
            "exchange": self.exchange,
            "queue_prefix": self.queue_prefix,
            "partitions": self.partitions,
            "prefetch": self.prefetch,
            "messages": sum(int(row["messages"]) for row in queues),
            "dead_letter_messages": len(self.dead),
            "queues": queues,
        }

    def purge(self):
        purged = sum(len(rows) for rows in self.queues.values())
        for rows in self.queues.values():
            rows.clear()
        dead = len(self.dead)
        self.dead.clear()
        return {"messages": purged, "dead_letter_messages": dead}

    def close(self) -> None:
        return None

    def queue_name(self, partition: int) -> str:
        return f"{self.queue_prefix}.{int(partition) % self.partitions}"

    def partition_for(self, session_key: str) -> int:
        return sum(session_key.encode("utf-8")) % self.partitions


class FakeRunner:
    async def run_turn(self, agent_id, session_key, user_text, *, channel="", correlation_id=""):
        return SimpleNamespace(text="pong", stop_reason="end_turn", tool_calls=[])


class FakeApp:
    def __init__(self) -> None:
        self.runner = FakeRunner()
        self.channel_manager = SimpleNamespace(
            get=lambda name, account_id="": FakeFeishuChannel()
            if name == "feishu" and account_id == "feishu-main"
            else None
        )
        self.settings = SimpleNamespace(
            model_id="fake-model",
            anthropic_base_url="https://example.test",
            anthropic_api_key="fake-key",
        )


class FakeFeishuChannel:
    def __init__(self) -> None:
        self.sent = []

    def send(self, outbound) -> bool:
        self.sent.append(outbound)
        return True


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


def test_main_loads_gateway_env_file(tmp_path, monkeypatch) -> None:
    loaded = []

    def fake_load_env(path):
        loaded.append(path)

    monkeypatch.setattr(load_test_gateway, "load_gateway_env", fake_load_env)

    assert main(
        [
            "--scenario",
            "mock-local",
            "--requests",
            "1",
            "--concurrency",
            "1",
            "--env-file",
            str(tmp_path / ".env.test"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--basename",
            "env-load",
        ]
    ) == 0
    assert loaded == [tmp_path / ".env.test"]


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
    FakeRabbitMQBroker.messages = []
    FakeRabbitMQBroker.dead = []
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


def test_inbound_rabbitmq_load_test_uses_broker_partition_path(tmp_path, monkeypatch) -> None:
    FakeInboundRabbitMQBroker.queues = {}
    FakeInboundRabbitMQBroker.dead = []
    monkeypatch.setattr(load_test_gateway, "RabbitMQInboundTaskBroker", FakeInboundRabbitMQBroker)

    samples, wall_seconds, context = asyncio.run(
        run_inbound_rabbitmq(
            requests=6,
            concurrency=2,
            agent_delay_ms=0,
            work_dir=tmp_path / "work",
            rabbitmq_url="amqp://example",
            rabbitmq_exchange="inbound-ex",
            rabbitmq_queue_prefix="inbound-q",
            rabbitmq_dead_letter_exchange="inbound-dlx",
            rabbitmq_dead_letter_queue="inbound-dlq",
            rabbitmq_partitions=4,
            rabbitmq_prefetch=1,
            session_count=3,
            connect_timeout_seconds=0.2,
        )
    )
    result = build_result(
        scenario="inbound-rabbitmq",
        requests=6,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 6
    assert result["summary"]["failed"] == 0
    assert result["summary"]["max_inbound_backlog"] == 6
    assert result["scenario"]["uses_rabbitmq"] is True
    assert result["scenario"]["uses_real_delivery_queue"] is False
    assert context["processed"] == 6
    assert context["effective_task_workers"] == 2
    assert context["inbound_session_count"] == 3
    assert context["inbound_partitions"] == 4
    assert context["broker_after_publish"]["messages"] == 6
    assert context["broker_after_consume"]["messages"] == 0
    assert "inbound-rabbitmq 基线" in render_markdown(result)


def test_model_real_requires_explicit_external_opt_in() -> None:
    try:
        main(["--scenario", "model-real", "--requests", "1"])
    except SystemExit as exc:
        assert "allow-real-external" in str(exc)
    else:  # pragma: no cover - regression guard.
        raise AssertionError("model-real should require --allow-real-external")


def test_model_real_load_test_uses_real_model_flag(tmp_path, monkeypatch) -> None:
    async def fake_build_app():
        return FakeApp()

    monkeypatch.setattr(load_test_gateway, "build_gateway_application_async", fake_build_app)

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


def test_feishu_send_real_requires_explicit_external_opt_in() -> None:
    try:
        main(["--scenario", "feishu-send-real", "--requests", "1"])
    except SystemExit as exc:
        assert "allow-real-external" in str(exc)
    else:  # pragma: no cover - regression guard.
        raise AssertionError("feishu-send-real should require --allow-real-external")


def test_feishu_send_real_requires_target() -> None:
    try:
        main(["--scenario", "feishu-send-real", "--allow-real-external", "--requests", "1"])
    except SystemExit as exc:
        assert "feishu-account-id" in str(exc)
    else:  # pragma: no cover - regression guard.
        raise AssertionError("feishu-send-real should require target args")


def test_feishu_send_real_load_test_uses_real_feishu_flag(monkeypatch) -> None:
    async def fake_build_app():
        return FakeApp()

    monkeypatch.setattr(load_test_gateway, "build_gateway_application_async", fake_build_app)

    samples, wall_seconds, context = asyncio.run(
        run_feishu_send_real(
            requests=2,
            concurrency=1,
            account_id="feishu-main",
            peer_id="ou_fake",
            text="hello",
        )
    )
    result = build_result(
        scenario="feishu-send-real",
        requests=2,
        concurrency=1,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 2
    assert result["scenario"]["uses_real_model"] is False
    assert result["scenario"]["uses_real_feishu"] is True
    assert context["feishu_account_id"] == "feishu-main"
    assert "feishu-send-real 基线" in render_markdown(result)


def test_build_gateway_application_async_runs_in_worker_thread(monkeypatch) -> None:
    caller_thread = threading.get_ident()
    observed: dict[str, int] = {}

    def fake_build_app():
        observed["thread"] = threading.get_ident()
        return FakeApp()

    monkeypatch.setattr(load_test_gateway, "build_gateway_application", fake_build_app)

    app = asyncio.run(build_gateway_application_async())

    assert isinstance(app, FakeApp)
    assert observed["thread"] != caller_thread
