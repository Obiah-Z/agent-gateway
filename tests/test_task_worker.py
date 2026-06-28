import asyncio
import json
import threading
from pathlib import Path

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import AgentReply, InboundMessage, RouteResolution
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.handlers import AgentInboundTaskHandler
from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore, TaskWorkerRuntime


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


class FakeInboundDispatcher:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.dispatched: list[InboundMessage] = []
        self.delivered = 0
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
        self.acquired: list[tuple[str, str, int]] = []
        self.released: list[tuple[str, str]] = []
        self.renewed: list[tuple[str, str, int]] = []
        self.replaced: list[tuple[str, str, str, int]] = []
        self.acquired_event = threading.Event()

    def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.acquired.append((key, value, ttl_seconds))
        if self.locked or key in self.existing_locks:
            return False
        self.existing_locks.add(key)
        self.values[key] = value
        self.acquired_event.set()
        return True

    def release_lock(self, key: str, *, value: str) -> bool:
        self.released.append((key, value))
        if self.values.get(key) != value:
            return False
        self.existing_locks.discard(key)
        self.values.pop(key, None)
        return True

    def renew_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        self.renewed.append((key, value, ttl_seconds))
        if self.fail_renew:
            raise RuntimeError("renew failed")
        return True

    def replace_lock_value(
        self,
        key: str,
        *,
        expected_value: str,
        new_value: str,
        ttl_seconds: int,
    ) -> bool:
        self.replaced.append((key, expected_value, new_value, ttl_seconds))
        if self.values.get(key) != expected_value:
            return False
        if self.fail_renew:
            raise RuntimeError("renew failed")
        self.values[key] = new_value
        return True

    def lock_exists(self, key: str) -> bool:
        if self.fail_lock_exists:
            raise RuntimeError("probe failed")
        return key in self.existing_locks

    def get_value(self, key: str) -> str:
        return self.values.get(key, "")


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
    assert stats["session_locks"]["last_blocked_sessions"] == [
        {
            "task_id": locked.id,
            "task_type": "agent_inbound",
            "source": "feishu",
            "agent_id": "",
            "session_key": "inbound:feishu:bot-a:user-1",
            "status": "pending",
            "retry_count": 0,
            "lane_owner": {
                "session_key": "inbound:feishu:bot-a:user-1",
                "lane_key": lane_key,
                "owned": True,
                "worker_id": "worker-x",
                "task_id": "running-task",
                "owner_value": redis_client.values[lane_key],
                "acquired_at": 100.0,
                "renewed_at": 120.0,
                "legacy": False,
            },
        }
    ]


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
    assert event_store.rows == []
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
