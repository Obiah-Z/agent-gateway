from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.lane import LaneOwnerToken, RedisLaneCoordinator


class FakeLaneRedisClient(RedisClient):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled, url="redis://example.test:6379/0")
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        if key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ttl_seconds
        return True

    def renew_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        if self.values.get(key) != value:
            return False
        self.expirations[key] = ttl_seconds
        return True

    def replace_lock_value(
        self,
        key: str,
        *,
        expected_value: str,
        new_value: str,
        ttl_seconds: int,
    ) -> bool:
        if self.values.get(key) != expected_value:
            return False
        self.values[key] = new_value
        self.expirations[key] = ttl_seconds
        return True

    def release_lock(self, key: str, *, value: str) -> bool:
        if self.values.get(key) != value:
            return False
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return True

    def lock_exists(self, key: str) -> bool:
        return key in self.values

    def get_value(self, key: str) -> str:
        return self.values.get(key, "")


def test_redis_lane_coordinator_allows_only_one_owner_per_session() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(redis_client)
    owner_a = LaneOwnerToken(worker_id="worker-a", task_id="task-a")
    owner_b = LaneOwnerToken(worker_id="worker-b", task_id="task-b")

    ownership = coordinator.acquire("session-1", owner=owner_a, ttl_seconds=60)
    contender = coordinator.acquire("session-1", owner=owner_b, ttl_seconds=60)

    assert ownership is not None
    assert ownership.lane_key == "gateway:lane:agent_inbound:session-1"
    assert contender is None
    assert coordinator.is_owned("session-1") is True
    info = coordinator.inspect("session-1")
    assert info.owned is True
    assert info.worker_id == "worker-a"
    assert info.task_id == "task-a"
    assert info.legacy is False


def test_redis_lane_coordinator_renews_and_releases_matching_owner() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(redis_client)
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")
    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60)

    assert ownership is not None
    renewed = coordinator.renew(ownership, now=200.0)
    assert renewed is not None
    assert redis_client.expirations[ownership.lane_key] == 60
    info = coordinator.inspect("session-1")
    assert info.acquired_at > 0
    assert info.renewed_at == 200.0
    assert coordinator.release(renewed) is True
    assert coordinator.is_owned("session-1") is False


def test_redis_lane_coordinator_rejects_wrong_owner_release_and_renew() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(redis_client)
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")
    wrong_owner = LaneOwnerToken(worker_id="worker-b", task_id="task-b")
    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60)

    assert ownership is not None
    wrong = type(ownership)(
        session_key=ownership.session_key,
        lane_key=ownership.lane_key,
        owner=wrong_owner,
        ttl_seconds=ownership.ttl_seconds,
        owner_value=wrong_owner.value,
    )

    assert coordinator.renew(wrong) is None
    assert coordinator.release(wrong) is False
    assert coordinator.is_owned("session-1") is True
    assert coordinator.release(ownership) is True


def test_redis_lane_coordinator_is_noop_when_redis_disabled() -> None:
    coordinator = RedisLaneCoordinator(FakeLaneRedisClient(enabled=False))
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")

    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60)

    assert ownership is not None
    assert ownership.lane_key == "gateway:lane:agent_inbound:session-1"
    assert coordinator.renew(ownership) == ownership
    assert coordinator.release(ownership) is True
    assert coordinator.is_owned("session-1") is False


def test_redis_lane_coordinator_inspects_legacy_owner_value() -> None:
    redis_client = FakeLaneRedisClient()
    redis_client.values["gateway:lane:agent_inbound:session-1"] = "worker-a:task-a"
    coordinator = RedisLaneCoordinator(redis_client)

    info = coordinator.inspect("session-1")

    assert info.owned is True
    assert info.worker_id == "worker-a"
    assert info.task_id == "task-a"
    assert info.legacy is True
