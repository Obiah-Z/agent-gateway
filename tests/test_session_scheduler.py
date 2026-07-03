from __future__ import annotations

import json

from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.session_scheduler import (
    RedisSessionReadyScheduler,
    decode_session_owner,
)


class FakeRedisSchedulerClient(RedisClient):
    """覆盖 scheduler 需要的 Redis list/string/eval 行为。"""

    def __init__(self) -> None:
        super().__init__(enabled=True, url="redis://example.test:6379/0")
        self.lists: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def _get_client(self):
        outer = self

        class Client:
            def lrange(self, key: str, start: int, end: int) -> list[str]:
                values = list(outer.lists.get(key, []))
                if end == -1:
                    return values[start:]
                return values[start : end + 1]

            def rpush(self, key: str, value: str) -> int:
                outer.lists.setdefault(key, []).append(value)
                return len(outer.lists[key])

            def lpop(self, key: str) -> str | None:
                values = outer.lists.get(key, [])
                if not values:
                    return None
                return values.pop(0)

            def lindex(self, key: str, index: int) -> str | None:
                values = outer.lists.get(key, [])
                if index < 0 or index >= len(values):
                    return None
                return values[index]

            def llen(self, key: str) -> int:
                return len(outer.lists.get(key, []))

            def exists(self, key: str) -> int:
                if key in outer.values:
                    return 1
                if outer.lists.get(key):
                    return 1
                return 0

            def get(self, key: str) -> str | None:
                return outer.values.get(key)

            def delete(self, key: str) -> int:
                deleted = 0
                if key in outer.values:
                    outer.values.pop(key, None)
                    deleted += 1
                if key in outer.lists:
                    outer.lists.pop(key, None)
                    deleted += 1
                return deleted

            def scan_iter(self, *, match: str, count: int):
                del count
                if match.endswith("*:pending"):
                    prefix = match[: -len("*:pending")]
                    for key in sorted(outer.lists):
                        if key.startswith(prefix) and key.endswith(":pending"):
                            yield key

            def eval(self, script: str, numkeys: int, *args: str):
                del numkeys
                if "local max_scan" in script:
                    return self._claim(*args)
                if "local owner_value = ARGV[2]" in script and "LLEN" in script:
                    return self._release(*args)
                if "EXPIRE" in script:
                    return self._renew(*args)
                raise AssertionError("unexpected script")

            def _claim(self, ready_key: str, namespace: str, worker_id: str, ttl: str, now: str, max_scan: str, allowed_raw: str):
                allowed = {item for item in allowed_raw.split(",") if item}
                for _ in range(int(max_scan)):
                    session_key = self.lpop(ready_key)
                    if session_key is None:
                        return None
                    pending_key = f"{namespace}:session:{session_key}:pending"
                    busy_key = f"{namespace}:session:{session_key}:busy"
                    if busy_key in outer.values:
                        if self.llen(pending_key) > 0:
                            self.rpush(ready_key, session_key)
                        continue
                    raw = self.lindex(pending_key, 0)
                    if raw is None:
                        continue
                    task_id, _, task_type = raw.partition("|")
                    if allowed and task_type not in allowed:
                        self.rpush(ready_key, session_key)
                        continue
                    self.lpop(pending_key)
                    owner = json.dumps(
                        {
                            "version": 1,
                            "worker_id": worker_id,
                            "task_id": task_id,
                            "session_key": session_key,
                            "acquired_at": float(now),
                            "renewed_at": float(now),
                        },
                        sort_keys=True,
                    )
                    outer.values[busy_key] = owner
                    outer.ttls[busy_key] = int(ttl)
                    return [task_id, session_key, owner, busy_key, pending_key]
                return None

            def _release(self, ready_key: str, busy_key: str, pending_key: str, session_key: str, owner_value: str):
                if outer.values.get(busy_key) != owner_value:
                    return 0
                outer.values.pop(busy_key, None)
                if self.llen(pending_key) > 0:
                    self.rpush(ready_key, session_key)
                return 1

            def _renew(self, busy_key: str, owner_value: str, ttl: str):
                if outer.values.get(busy_key) != owner_value:
                    return 0
                outer.ttls[busy_key] = int(ttl)
                return 1

        return Client()


def make_task(task_id: str, session_key: str, *, task_type: str = "agent_inbound") -> TaskInstance:
    task = TaskInstance.create(
        task_type=task_type,
        source="test",
        session_key=session_key,
    )
    task.id = task_id
    return task


def test_session_scheduler_claims_one_head_per_session_and_skips_busy_session() -> None:
    redis_client = FakeRedisSchedulerClient()
    scheduler = RedisSessionReadyScheduler(redis_client, namespace="gateway:tasks:test")
    a1 = make_task("a1", "session-a")
    a2 = make_task("a2", "session-a")
    b1 = make_task("b1", "session-b")
    c1 = make_task("c1", "session-c")

    for task in [a1, a2, b1, c1]:
        assert scheduler.enqueue(task) is True

    first = scheduler.claim_next(worker_id="worker-1", task_types=["agent_inbound"], now=100.0)
    second = scheduler.claim_next(worker_id="worker-2", task_types=["agent_inbound"], now=101.0)
    third = scheduler.claim_next(worker_id="worker-3", task_types=["agent_inbound"], now=102.0)

    assert first is not None
    assert first.task_id == "a1"
    assert decode_session_owner(first.owner_value)["worker_id"] == "worker-1"
    assert second is not None
    assert third is not None
    assert {second.task_id, third.task_id} == {"b1", "c1"}
    assert redis_client.lists[scheduler.pending_key("session-a")] == ["a2|agent_inbound"]

    assert scheduler.release(first) is True
    fourth = scheduler.claim_next(worker_id="worker-4", task_types=["agent_inbound"], now=103.0)

    assert fourth is not None
    assert fourth.task_id == "a2"


def test_session_scheduler_renew_requires_current_owner() -> None:
    redis_client = FakeRedisSchedulerClient()
    scheduler = RedisSessionReadyScheduler(redis_client, namespace="gateway:tasks:test", default_ttl_seconds=30)
    scheduler.enqueue(make_task("a1", "session-a"))
    claim = scheduler.claim_next(worker_id="worker-1", now=100.0)

    assert claim is not None
    assert scheduler.renew(claim, ttl_seconds=60) is True
    assert redis_client.ttls[claim.busy_key] == 60

    stolen = type(claim)(
        task_id=claim.task_id,
        session_key=claim.session_key,
        owner_value="other-owner",
        busy_key=claim.busy_key,
        pending_key=claim.pending_key,
        ttl_seconds=claim.ttl_seconds,
    )

    assert scheduler.renew(stolen, ttl_seconds=90) is False
    assert scheduler.release(stolen) is False
    assert scheduler.release(claim) is True


def test_session_scheduler_rebuild_orders_pending_tasks_by_priority_and_created_at() -> None:
    redis_client = FakeRedisSchedulerClient()
    scheduler = RedisSessionReadyScheduler(redis_client, namespace="gateway:tasks:test")
    late = make_task("late", "session-a")
    late.priority = 100
    late.created_at = 200.0
    early = make_task("early", "session-a")
    early.priority = 10
    early.created_at = 300.0
    done = make_task("done", "session-b")
    done.status = "done"

    rebuilt = scheduler.rebuild([late, early, done])

    assert rebuilt == 2
    assert redis_client.lists[scheduler.ready_key] == ["session-a"]
    assert redis_client.lists[scheduler.pending_key("session-a")] == [
        "early|agent_inbound",
        "late|agent_inbound",
    ]


def test_session_scheduler_snapshot_includes_ready_pending_and_busy_details() -> None:
    redis_client = FakeRedisSchedulerClient()
    scheduler = RedisSessionReadyScheduler(redis_client, namespace="gateway:tasks:test")
    scheduler.enqueue(make_task("a1", "session-a"))
    scheduler.enqueue(make_task("a2", "session-a"))
    scheduler.enqueue(make_task("b1", "session-b"))
    claim = scheduler.claim_next(worker_id="worker-1", now=100.0)

    snapshot = scheduler.snapshot(detail=True).to_dict()

    assert claim is not None
    assert snapshot["enabled"] is True
    assert snapshot["namespace"] == "gateway:tasks:test"
    assert "session-b" in snapshot["ready_sessions"]
    pending_by_session = {
        row["session_key"]: row for row in snapshot["pending_buckets"]
    }
    busy_by_session = {
        row["session_key"]: row for row in snapshot["busy_owners"]
    }
    assert pending_by_session["session-a"]["items"] == ["a2|agent_inbound"]
    assert busy_by_session["session-a"]["owner"]["task_id"] == "a1"
