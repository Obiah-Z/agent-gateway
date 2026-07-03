import asyncio
import json
import threading
from pathlib import Path

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import AgentReply, InboundMessage, RouteResolution
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.handlers import AgentInboundTaskHandler
from agent_gateway.runtime.tasks.session_scheduler import SessionTaskClaim
from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore, TaskWorkerRuntime


class FakeInboundTaskBroker:
    def __init__(self, payloads: list[dict]) -> None:
        self.enabled = True
        self.partitions = 2
        self.payloads = list(payloads)
        self.acked: list[str] = []
        self.nacked: list[str] = []

    def consume_once(self, partition: int, handler) -> bool:
        del partition
        if not self.payloads:
            return False
        payload = self.payloads.pop(0)
        if handler(payload):
            self.acked.append(str(payload.get("task_id", "")))
        else:
            self.nacked.append(str(payload.get("task_id", "")))
        return True

    def stats(self) -> dict:
        return {"backend": "fake", "messages": len(self.payloads)}


class FakeSessionScheduler:
    enabled = True

    def __init__(self, claims: list[SessionTaskClaim | None]) -> None:
        self.claims = list(claims)
        self.released: list[str] = []
        self.enqueued: list[str] = []
        self.renewed: list[str] = []

    def enqueue(self, task) -> bool:
        self.enqueued.append(task.id)
        return True

    def claim_next(self, **kwargs):
        del kwargs
        if not self.claims:
            return None
        return self.claims.pop(0)

    def release(self, claim: SessionTaskClaim) -> bool:
        self.released.append(claim.task_id)
        return True

    def renew(self, claim: SessionTaskClaim, *, ttl_seconds: int | None = None) -> bool:
        del ttl_seconds
        self.renewed.append(claim.task_id)
        return True


def make_claim(task_id: str, session_key: str) -> SessionTaskClaim:
    return SessionTaskClaim(
        task_id=task_id,
        session_key=session_key,
        owner_value=f"owner:{task_id}",
        busy_key=f"busy:{session_key}",
        pending_key=f"pending:{session_key}",
        ttl_seconds=60,
    )


def test_task_worker_run_once_acknowledges_success(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="echo", source="test", payload={"text": "hello"})
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert stored.result_preview == "echo:hello"
    assert stored.metadata["worker_id"] == "worker-1"


def test_task_worker_records_lifecycle_events(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="echo",
        source="test",
        agent_id="main",
        session_key="session-a",
        payload={"text": "hello"},
        idempotency_key="idem-a",
        priority=7,
    )
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())

    assert handled is True
    event_types = [row["type"] for row in events.rows]
    assert event_types == ["task.worker.started", "task.worker.completed"]
    started = events.rows[0]
    completed = events.rows[1]
    assert started["correlation_id"] == task.id
    assert started["agent_id"] == "main"
    assert started["session_key"] == "session-a"
    assert started["metadata"]["worker_id"] == "worker-1"
    assert started["metadata"]["task_type"] == "echo"
    assert started["metadata"]["source"] == "test"
    assert started["metadata"]["idempotency_key"] == "idem-a"
    assert started["metadata"]["priority"] == 7
    assert completed["status"] == "ok"
    assert completed["metadata"]["duration_seconds"] >= 0


def test_task_worker_records_retrying_lifecycle_event(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unstable", source="test", session_key="session-r")
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", retry_exceptions=True, event_store=events)

    def fail(_task):
        raise RuntimeError("temporary")

    worker.register_handler("unstable", fail)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert [row["type"] for row in events.rows] == ["task.worker.started", "task.worker.retrying"]
    retrying = events.rows[-1]
    assert retrying["status"] == "warning"
    assert retrying["metadata"]["reason"] == "temporary"
    assert retrying["metadata"]["retry_count"] == 0


def test_task_worker_records_failed_lifecycle_event(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="boom", source="test", session_key="session-f")
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)

    def fail(_task):
        raise RuntimeError("fatal")

    worker.register_handler("boom", fail)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "failed"
    assert [row["type"] for row in events.rows] == ["task.worker.started", "task.worker.failed"]
    failed = events.rows[-1]
    assert failed["status"] == "error"
    assert failed["metadata"]["reason"] == "fatal"
    assert failed["metadata"]["worker_id"] == "worker-1"


def test_task_worker_consumes_broker_task_reference_by_id(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    queue = LocalTaskQueue(store)
    skipped = queue.enqueue(task_type="echo", source="test", priority=1, payload={"text": "skip"})
    target = queue.enqueue(task_type="echo", source="test", priority=100, payload={"text": "target"})
    broker = FakeInboundTaskBroker([{"task_id": target.id, "task_type": "echo"}])
    queue.broker = broker
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert broker.acked == [target.id]
    assert broker.nacked == []
    assert store.get(target.id).status == "done"
    assert store.get(target.id).result_preview == "echo:target"
    assert store.get(skipped.id).status == "pending"


def test_task_worker_uses_session_scheduler_before_direct_reserve(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    scheduler = FakeSessionScheduler([])
    queue = LocalTaskQueue(store, session_scheduler=scheduler)
    a2 = queue.enqueue(
        task_type="echo",
        source="test",
        session_key="session-a",
        priority=1,
        payload={"text": "a2"},
    )
    b1 = queue.enqueue(
        task_type="echo",
        source="test",
        session_key="session-b",
        priority=100,
        payload={"text": "b1"},
    )
    scheduler.claims = [make_claim(b1.id, "session-b")]
    calls: list[str] = []
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: calls.append(item.payload["text"]) or item.payload["text"])

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert calls == ["b1"]
    assert store.get(b1.id).status == "done"
    assert store.get(a2.id).status == "pending"
    assert scheduler.released == [b1.id]


def test_task_worker_renews_session_scheduler_claim_during_slow_task(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    scheduler = FakeSessionScheduler([])
    queue = LocalTaskQueue(store, session_scheduler=scheduler)
    task = queue.enqueue(
        task_type="slow",
        source="test",
        session_key="session-a",
        payload={"text": "slow"},
    )
    scheduler.claims = [
        SessionTaskClaim(
            task_id=task.id,
            session_key="session-a",
            owner_value="owner",
            busy_key="busy:session-a",
            pending_key="pending:session-a",
            ttl_seconds=1,
        )
    ]
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")

    async def slow_handler(_task):
        await asyncio.sleep(0.5)
        return "ok"

    worker.register_handler("slow", slow_handler)

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert store.get(task.id).status == "done"
    assert scheduler.renewed
    assert scheduler.released == [task.id]


def test_task_worker_records_broker_ack_event(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    queue = LocalTaskQueue(store)
    target = queue.enqueue(
        task_type="echo",
        source="test",
        session_key="session-a",
        payload={"text": "target"},
    )
    broker = FakeInboundTaskBroker(
        [
            {
                "task_id": target.id,
                "task_type": "echo",
                "session_key": "session-a",
                "partition": 1,
                "idempotency_key": "idem-a",
            }
        ]
    )
    queue.broker = broker
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert broker.acked == [target.id]
    assert events.rows[-1]["type"] == "task.broker.acked"
    assert events.rows[-1]["status"] == "ok"
    assert events.rows[-1]["correlation_id"] == target.id
    assert events.rows[-1]["session_key"] == "session-a"
    assert events.rows[-1]["metadata"]["partition"] == 1
    assert events.rows[-1]["metadata"]["reason"] == "task status: done"


def test_task_worker_discards_duplicate_broker_message_after_task_done(tmp_path: Path) -> None:
    store = LocalTaskStore(tmp_path / "tasks")
    queue = LocalTaskQueue(store)
    target = queue.enqueue(
        task_type="echo",
        source="test",
        session_key="session-duplicate",
        payload={"text": "target"},
    )
    payload = {
        "task_id": target.id,
        "task_type": "echo",
        "session_key": "session-duplicate",
        "partition": 1,
        "idempotency_key": "idem-duplicate",
    }
    broker = FakeInboundTaskBroker([payload, dict(payload)])
    queue.broker = broker
    events = FakeEventStore()
    calls: list[str] = []
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)

    def handler(item):
        calls.append(item.id)
        return f"echo:{item.payload['text']}"

    worker.register_handler("echo", handler)

    first_handled = asyncio.run(worker.run_once())
    second_handled = asyncio.run(worker.run_once())

    assert first_handled is True
    assert second_handled is True
    assert calls == [target.id]
    assert broker.acked == [target.id, target.id]
    assert broker.nacked == []
    assert store.get(target.id).status == "done"
    assert [row["type"] for row in events.rows[-2:]] == [
        "task.broker.acked",
        "task.broker.discarded",
    ]
    assert events.rows[-1]["status"] == "ok"
    assert events.rows[-1]["metadata"]["reason"] == "task status: done"


def test_task_worker_records_broker_requeue_for_unregistered_handler(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    broker = FakeInboundTaskBroker(
        [{"task_id": "task-1", "task_type": "unknown", "partition": 0}]
    )
    queue.broker = broker
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)
    worker.register_handler("echo", lambda item: "ok")

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert broker.acked == []
    assert broker.nacked == ["task-1"]
    assert events.rows[-1]["type"] == "task.broker.requeued"
    assert events.rows[-1]["status"] == "warning"
    assert events.rows[-1]["metadata"]["reason"] == "handler not registered"


def test_task_worker_records_broker_discard_for_invalid_payload(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    broker = FakeInboundTaskBroker([{"task_type": "echo", "partition": 0}])
    queue.broker = broker
    events = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=events)
    worker.register_handler("echo", lambda item: "ok")

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert broker.acked == [""]
    assert events.rows[-1]["type"] == "task.broker.discarded"
    assert events.rows[-1]["status"] == "warning"
    assert events.rows[-1]["metadata"]["reason"] == "missing task_id"


def test_task_worker_run_once_returns_false_without_available_task(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    worker = TaskWorkerRuntime(queue)
    worker.register_handler("echo", lambda item: "ok")

    assert asyncio.run(worker.run_once()) is False


def test_task_worker_ignores_unregistered_task_type(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unknown", source="test")
    worker = TaskWorkerRuntime(queue)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is False
    assert stored.status == "pending"


def test_task_worker_retries_handler_exception_when_configured(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unstable", source="test")
    worker = TaskWorkerRuntime(queue, retry_exceptions=True)

    def fail(_task):
        raise RuntimeError("temporary")

    worker.register_handler("unstable", fail)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert stored.retry_count == 1
    assert stored.error == "temporary"


def test_task_worker_stats_include_registered_handlers_and_queue(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    queue.enqueue(task_type="echo", source="test")
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", concurrency=3)
    worker.register_handler("echo", lambda item: "ok")

    stats = worker.stats()

    assert stats["running"] is False
    assert stats["worker_id"] == "worker-1"
    assert stats["concurrency"] == 3
    assert stats["registered_task_types"] == ["echo"]
    assert stats["queue"]["pending"] == 1


def test_task_worker_stats_include_broker_summary(tmp_path: Path) -> None:
    broker = FakeInboundTaskBroker([])
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"), broker=broker)
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: "ok")

    stats = worker.stats()

    assert stats["broker"] == {"backend": "fake", "messages": 0}
    assert stats["queue"]["broker"] == {"backend": "fake", "messages": 0}


class FakeInboundDispatcher:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.dispatched: list[InboundMessage] = []
        self.delivered = 0
        self.progress_notices: list[tuple[InboundMessage, str, str]] = []
        self.delay_seconds = delay_seconds

    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        del forced_agent_id
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.dispatched.append(inbound)
        return type(
            "DispatchResult",
            (),
            {
                "inbound": inbound,
                "route": RouteResolution(agent_id="main", session_key="main:user-1"),
                "reply": AgentReply(
                    agent_id="main",
                    session_key="main:user-1",
                    text="done",
                    stop_reason="end_turn",
                ),
            },
        )()

    async def deliver_reply(self, channels: ChannelManager, result) -> str:
        del channels, result
        self.delivered += 1
        return "delivery-1"

    async def deliver_progress(
        self,
        channels: ChannelManager,
        inbound: InboundMessage,
        text: str,
        *,
        stage: str = "started",
    ) -> str:
        del channels
        self.progress_notices.append((inbound, text, stage))
        return f"progress-delivery-{len(self.progress_notices)}"


class FakeLockRedisClient(RedisClient):
    def __init__(
        self,
        *,
        locked: bool = False,
        fail: bool = False,
        fail_renew: bool = False,
        fail_lock_exists: bool = False,
        existing_locks: set[str] | None = None,
    ) -> None:
        super().__init__(enabled=True, url="redis://example.test:6379/0")
        self.locked = locked
        self.fail = fail
        self.fail_renew = fail_renew
        self.fail_lock_exists = fail_lock_exists
        self.existing_locks = set(existing_locks or set())
        self.values: dict[str, str] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 0.0
        self.acquired: list[tuple[str, str, int]] = []
        self.released: list[tuple[str, str]] = []
        self.renewed: list[tuple[str, str, int]] = []
        self.replaced: list[tuple[str, str, str, int]] = []
        self.acquired_event = threading.Event()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._purge_expired()

    def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        if self.fail:
            raise RuntimeError("redis unavailable")
        self._purge_expired()
        self.acquired.append((key, value, ttl_seconds))
        if self.locked or key in self.existing_locks:
            return False
        self.existing_locks.add(key)
        self.values[key] = value
        self.expires_at[key] = self.now + ttl_seconds
        self.acquired_event.set()
        return True

    def release_lock(self, key: str, *, value: str) -> bool:
        self._purge_expired()
        self.released.append((key, value))
        if self.values.get(key) != value:
            return False
        self.existing_locks.discard(key)
        self.values.pop(key, None)
        self.expires_at.pop(key, None)
        return True

    def renew_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        self._purge_expired()
        self.renewed.append((key, value, ttl_seconds))
        if self.fail_renew:
            raise RuntimeError("renew failed")
        self.expires_at[key] = self.now + ttl_seconds
        return True

    def replace_lock_value(
        self,
        key: str,
        *,
        expected_value: str,
        new_value: str,
        ttl_seconds: int,
    ) -> bool:
        self._purge_expired()
        self.replaced.append((key, expected_value, new_value, ttl_seconds))
        if self.values.get(key) != expected_value:
            return False
        if self.fail_renew:
            raise RuntimeError("renew failed")
        self.values[key] = new_value
        self.expires_at[key] = self.now + ttl_seconds
        return True

    def lock_exists(self, key: str) -> bool:
        self._purge_expired()
        if self.fail_lock_exists:
            raise RuntimeError("probe failed")
        return key in self.existing_locks

    def get_value(self, key: str) -> str:
        self._purge_expired()
        return self.values.get(key, "")

    def _purge_expired(self) -> None:
        expired = [key for key, expires_at in self.expires_at.items() if expires_at <= self.now]
        for key in expired:
            self.existing_locks.discard(key)
            self.values.pop(key, None)
            self.expires_at.pop(key, None)


class FakeEventStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, event_type: str, **kwargs) -> dict:
        row = {"type": event_type, **kwargs}
        self.rows.append(row)
        return row


async def _run_once(worker: TaskWorkerRuntime) -> bool:
    return await worker.run_once()


def test_task_worker_executes_agent_inbound_task(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        payload={
            "text": "/github-repo-analyzer https://github.com/openai/openai-python",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
            "metadata": {"receive_id_type": "open_id"},
        },
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(dispatcher, ChannelManager()),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert stored.result_preview == "agent inbound delivered: delivery-1"
    assert dispatcher.delivered == 1
    assert dispatcher.dispatched[0].text.startswith("/github-repo-analyzer ")
    assert dispatcher.dispatched[0].metadata["receive_id_type"] == "open_id"
    assert dispatcher.dispatched[0].metadata["background_task_id"] == task.id
    assert dispatcher.progress_notices == [
        (
            dispatcher.dispatched[0],
            "已收到，正在处理。本轮结果生成后会继续推送。",
            "worker_started",
        )
    ]


def test_agent_inbound_task_can_disable_feishu_progress_notice(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            feishu_progress_notice_enabled=False,
        ),
    )

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert dispatcher.delivered == 1
    assert dispatcher.progress_notices == []


def test_agent_inbound_task_uses_redis_session_lock(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient()
    dispatcher = FakeInboundDispatcher()
    event_store = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=event_store)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            lock_ttl_seconds=120,
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    acquired_payload = json.loads(redis_client.acquired[0][1])
    assert redis_client.acquired == [
        (
            "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1",
            redis_client.acquired[0][1],
            120,
        )
    ]
    assert acquired_payload["owner_token"] == f"worker-1:{task.id}"
    assert redis_client.released == [
        (
            "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1",
            redis_client.released[0][1],
        )
    ]


def test_agent_inbound_task_retries_when_session_lock_is_held(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=FakeLockRedisClient(locked=True),
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert stored.retry_count == 1
    assert stored.error == "agent inbound session locked: inbound:feishu:bot-a:user-1"
    assert dispatcher.dispatched == []


def test_agent_inbound_duplicate_running_task_is_not_retried(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    running = queue.reserve_task_id(task.id, worker_id="worker-a")
    assert running is not None
    lane_key = "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1"
    redis_client = FakeLockRedisClient(existing_locks={lane_key})
    redis_client.values[lane_key] = json.dumps(
        {
            "version": 1,
            "session_key": "inbound:feishu:bot-a:user-1",
            "lane_key": lane_key,
            "worker_id": "worker-a",
            "task_id": task.id,
            "owner_token": f"worker-a:{task.id}",
            "acquired_at": 100.0,
            "renewed_at": 100.0,
        },
        ensure_ascii=False,
    )
    dispatcher = FakeInboundDispatcher()
    event_store = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-b", event_store=event_store)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            worker_id="worker-b",
        ),
    )

    asyncio.run(worker._execute(running))
    stored = queue.store.get(task.id)

    assert stored.status == "running"
    assert stored.retry_count == 0
    assert stored.error == ""
    assert dispatcher.dispatched == []
    assert [row["type"] for row in event_store.rows] == [
        "task.worker.started",
        "task.worker.duplicate_discarded",
    ]


def test_agent_inbound_task_retries_when_redis_lock_unavailable(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=FakeLockRedisClient(fail=True),
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert stored.retry_count == 1
    assert stored.error == "agent inbound session lock unavailable: redis unavailable"
    assert dispatcher.dispatched == []


def test_agent_inbound_task_renews_redis_session_lock_during_slow_dispatch(
    tmp_path: Path,
) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "hello",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient()
    dispatcher = FakeInboundDispatcher(delay_seconds=0.2)
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            lock_ttl_seconds=3,
            lock_renew_interval_seconds=0.01,
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert redis_client.replaced
    assert redis_client.replaced[0][0] == "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1"
    renewed_payload = json.loads(redis_client.replaced[0][2])
    assert renewed_payload["owner_token"] == f"worker-1:{task.id}"


def test_task_worker_skips_agent_inbound_task_when_session_lock_exists(
    tmp_path: Path,
) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    locked = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        priority=10,
        payload={
            "text": "locked",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    available = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-2",
        priority=20,
        payload={
            "text": "available",
            "sender_id": "user-2",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-2",
        },
    )
    lane_key = "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1"
    redis_client = FakeLockRedisClient(existing_locks={lane_key})
    redis_client.values[lane_key] = json.dumps(
        {
            "version": 1,
            "session_key": "inbound:feishu:bot-a:user-1",
            "lane_key": lane_key,
            "worker_id": "worker-x",
            "task_id": "running-task",
            "owner_token": "worker-x:running-task",
            "acquired_at": 100.0,
            "renewed_at": 120.0,
        },
        ensure_ascii=False,
    )
    dispatcher = FakeInboundDispatcher()
    event_store = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=event_store)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored_locked = queue.store.get(locked.id)
    stored_available = queue.store.get(available.id)

    assert handled is True
    assert stored_locked.status == "pending"
    assert stored_available.status == "done"
    assert dispatcher.dispatched[0].text == "available"
    stats = worker.stats()
    assert stats["session_locks"]["blocked_session_count"] == 1
    assert stats["session_locks"]["skip_count"] == 1
    sample = stats["session_locks"]["last_blocked_sessions"][0]
    assert sample["task_id"] == locked.id
    assert sample["task_type"] == "agent_inbound"
    assert sample["session_key"] == "inbound:feishu:bot-a:user-1"
    assert sample["status"] == "pending"
    assert sample["retry_count"] == 0
    assert sample["lane_owner"]["session_key"] == "inbound:feishu:bot-a:user-1"
    assert sample["lane_owner"]["lane_key"] == lane_key
    assert sample["lane_owner"]["owned"] is True
    assert sample["lane_owner"]["worker_id"] == "worker-x"
    assert sample["lane_owner"]["task_id"] == "running-task"
    assert sample["lane_owner"]["legacy"] is False


def test_concurrent_agent_inbound_workers_do_not_execute_same_session(
    tmp_path: Path,
) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    first = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        priority=10,
        payload={
            "text": "first",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    second = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        priority=20,
        payload={
            "text": "second",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient()
    dispatcher = FakeInboundDispatcher(delay_seconds=0.1)
    worker_a = TaskWorkerRuntime(queue, worker_id="worker-a")
    worker_b = TaskWorkerRuntime(queue, worker_id="worker-b")
    for worker in (worker_a, worker_b):
        worker.register_handler(
            "agent_inbound",
            AgentInboundTaskHandler(
                dispatcher,
                ChannelManager(),
                redis_client=redis_client,
                lock_ttl_seconds=30,
                worker_id=worker.worker_id,
            ),
        )

    async def run_race() -> tuple[bool, bool]:
        task_a = asyncio.create_task(_run_once(worker_a))
        await asyncio.to_thread(redis_client.acquired_event.wait, 1.0)
        task_b = asyncio.create_task(_run_once(worker_b))
        return await asyncio.gather(task_a, task_b)

    handled_a, handled_b = asyncio.run(run_race())

    assert handled_a is True
    assert handled_b is False
    assert queue.store.get(first.id).status == "done"
    assert queue.store.get(second.id).status == "pending"
    assert [inbound.text for inbound in dispatcher.dispatched] == ["first"]


def test_agent_inbound_lock_renew_failure_does_not_abort_current_turn(
    tmp_path: Path,
) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "slow",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient(fail_renew=True)
    dispatcher = FakeInboundDispatcher(delay_seconds=0.2)
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            lock_ttl_seconds=3,
            lock_renew_interval_seconds=0.01,
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert redis_client.replaced
    assert dispatcher.dispatched[0].text == "slow"


def test_task_worker_does_not_skip_when_lock_probe_fails(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        payload={
            "text": "probe failure falls through",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient(fail_lock_exists=True)
    dispatcher = FakeInboundDispatcher()
    event_store = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=event_store)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            worker_id="worker-1",
        ),
    )

    handled = asyncio.run(worker.run_once())

    assert handled is True
    assert queue.store.get(task.id).status == "done"
    assert [row["type"] for row in event_store.rows] == [
        "task.worker.started",
        "task.worker.completed",
    ]
    assert not any(row["type"] == "agent_inbound.session_locked_skipped" for row in event_store.rows)
    assert worker.stats()["session_locks"]["skip_count"] == 0


def test_task_worker_deduplicates_session_lock_skip_events(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    locked = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        priority=10,
        payload={
            "text": "locked",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    lane_key = "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1"
    redis_client = FakeLockRedisClient(existing_locks={lane_key})
    redis_client.values[lane_key] = "worker-x:running-task"
    event_store = FakeEventStore()
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", event_store=event_store)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            FakeInboundDispatcher(),
            ChannelManager(),
            redis_client=redis_client,
            worker_id="worker-1",
        ),
    )

    assert asyncio.run(worker.run_once()) is False
    assert asyncio.run(worker.run_once()) is False
    assert queue.store.get(locked.id).status == "pending"
    assert len(event_store.rows) == 1
    assert worker.stats()["session_locks"]["skip_count"] == 2
    row = event_store.rows[0]
    assert row["type"] == "agent_inbound.session_locked_skipped"
    assert row["status"] == "warning"
    assert row["component"] == "task_worker"
    assert row["correlation_id"] == locked.id
    assert row["session_key"] == "inbound:feishu:bot-a:user-1"
    assert row["metadata"]["worker_id"] == "worker-1"
    assert row["metadata"]["task_id"] == locked.id
    assert row["metadata"]["lane_owner"]["owned"] is True


def test_task_worker_can_take_over_session_lane_after_owner_ttl_expires(
    tmp_path: Path,
) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    stale = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        session_key="inbound:feishu:bot-a:user-1",
        priority=10,
        payload={
            "text": "stale owner task",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
        },
    )
    redis_client = FakeLockRedisClient()
    lane_key = "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1"
    assert redis_client.acquire_lock(
        lane_key,
        value="crashed-worker:stale-task",
        ttl_seconds=5,
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue, worker_id="worker-new")
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            ChannelManager(),
            redis_client=redis_client,
            lock_ttl_seconds=5,
            worker_id="worker-new",
        ),
    )

    assert asyncio.run(worker.run_once()) is False
    assert queue.store.get(stale.id).status == "pending"
    redis_client.advance(5.0)
    assert asyncio.run(worker.run_once()) is True

    stored = queue.store.get(stale.id)
    assert stored.status == "done"
    assert dispatcher.dispatched[0].text == "stale owner task"
