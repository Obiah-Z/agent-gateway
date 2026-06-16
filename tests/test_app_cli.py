import asyncio

from agent_gateway.app import trigger_cron_once, trigger_cron_once_with_timeout


class FakeCron:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.triggered: list[str] = []

    async def trigger_job(self, job_id: str) -> str:
        self.triggered.append(job_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        return f"{job_id} triggered"


class FakeAutonomyRuntime:
    def __init__(self, cron: FakeCron) -> None:
        self.cron = cron


class FakeDeliveryRuntime:
    def __init__(self, queue: "FakeDeliveryQueue") -> None:
        self.queue = queue
        self.flush_calls = 0

    def pending_count(self) -> int:
        return len(self.queue.pending_entries())

    async def flush_once(self) -> None:
        self.flush_calls += 1
        self.queue.pop_one()


class FakeDeliveryEntry:
    def __init__(self, entry_id: str, last_error: str | None = None) -> None:
        self.id = entry_id
        self.last_error = last_error


class FakeDeliveryQueue:
    def __init__(self, pending: int) -> None:
        self.entries = [
            FakeDeliveryEntry(f"delivery-{index + 1}")
            for index in range(pending)
        ]

    def pending_entries(self) -> list[FakeDeliveryEntry]:
        return list(self.entries)

    def pop_one(self) -> None:
        if self.entries:
            self.entries.pop(0)


class FakeApp:
    def __init__(self, *, pending: int = 0, cron_delay: float = 0.0) -> None:
        self.autonomy_runtime = FakeAutonomyRuntime(FakeCron(delay=cron_delay))
        self.delivery_queue = FakeDeliveryQueue(pending)
        self.delivery_runtime = FakeDeliveryRuntime(self.delivery_queue)


def test_trigger_cron_once_flushes_delivery_queue() -> None:
    app = FakeApp(pending=2)

    result = asyncio.run(trigger_cron_once(app, "agent-news-digest", flush_rounds=3))

    assert result == {
        "job_id": "agent-news-digest",
        "result": "agent-news-digest triggered",
        "pending_before_flush": 2,
        "pending_after_flush": 0,
        "pending_ids": [],
        "pending_errors": {},
    }
    assert app.delivery_runtime.flush_calls == 2


def test_trigger_cron_once_reports_remaining_pending_delivery() -> None:
    app = FakeApp(pending=3)
    app.delivery_queue.entries[-1].last_error = "delivery failed"

    result = asyncio.run(trigger_cron_once(app, "agent-news-digest", flush_rounds=1))

    assert result == {
        "job_id": "agent-news-digest",
        "result": "agent-news-digest triggered",
        "pending_before_flush": 3,
        "pending_after_flush": 1,
        "pending_ids": ["delivery-3"],
        "pending_errors": {"delivery-3": "delivery failed"},
    }
    assert app.delivery_runtime.flush_calls == 2


def test_trigger_cron_once_with_timeout_returns_timeout_result() -> None:
    app = FakeApp(cron_delay=0.05)

    result = asyncio.run(
        trigger_cron_once_with_timeout(
            app,
            "agent-news-digest",
            timeout_seconds=0.01,
        )
    )

    assert result == {
        "job_id": "agent-news-digest",
        "result": "timeout",
        "timeout_seconds": 0.01,
        "pending_after_timeout": 0,
    }
