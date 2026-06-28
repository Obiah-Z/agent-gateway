from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.lane import LaneOwnerToken, RedisLaneCoordinator


class FakeLaneRedisClient(RedisClient):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled, url="redis://example.test:6379/0")
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._purge_expired()

    def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        self._purge_expired()
        if key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ttl_seconds
        self.expires_at[key] = self.now + ttl_seconds
        return True

    def renew_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        self._purge_expired()
        if self.values.get(key) != value:
            return False
        self.expirations[key] = ttl_seconds
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
        if self.values.get(key) != expected_value:
            return False
        self.values[key] = new_value
        self.expirations[key] = ttl_seconds
        self.expires_at[key] = self.now + ttl_seconds
        return True

    def release_lock(self, key: str, *, value: str) -> bool:
        self._purge_expired()
        if self.values.get(key) != value:
            return False
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        self.expires_at.pop(key, None)
        return True

    def lock_exists(self, key: str) -> bool:
        self._purge_expired()
        return key in self.values

    def get_value(self, key: str) -> str:
        self._purge_expired()
        return self.values.get(key, "")

    def _purge_expired(self) -> None:
        expired = [key for key, expires_at in self.expires_at.items() if expires_at <= self.now]
        for key in expired:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
            self.expires_at.pop(key, None)


class FakeLaneStateRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[dict] = []
        self.releases: list[dict] = []

    def write_session_lane(self, row: dict) -> dict:
        if self.fail:
            raise RuntimeError("state unavailable")
        self.writes.append(dict(row))
        return row

    def release_session_lane(
        self,
        session_key: str,
        *,
        owner_token: str = "",
        reason: str = "manual release",
        now: float = 0.0,
    ) -> bool:
        if self.fail:
            raise RuntimeError("state unavailable")
        self.releases.append(
            {
                "session_key": session_key,
                "owner_token": owner_token,
                "reason": reason,
                "now": now,
            }
        )
        return True


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


def test_redis_lane_coordinator_allows_takeover_after_ttl_expiry() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(redis_client)
    owner_a = LaneOwnerToken(worker_id="worker-a", task_id="task-a")
    owner_b = LaneOwnerToken(worker_id="worker-b", task_id="task-b")

    first = coordinator.acquire("session-1", owner=owner_a, ttl_seconds=10, now=100.0)
    blocked = coordinator.acquire("session-1", owner=owner_b, ttl_seconds=10, now=101.0)
    redis_client.advance(10.0)
    takeover = coordinator.acquire("session-1", owner=owner_b, ttl_seconds=10, now=111.0)

    assert first is not None
    assert blocked is None
    assert takeover is not None
    info = coordinator.inspect("session-1", now=112.0)
    assert info.worker_id == "worker-b"
    assert info.task_id == "task-b"


def test_redis_lane_coordinator_marks_stale_owner_for_observability() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(redis_client)
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")

    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60, now=100.0)

    assert ownership is not None
    fresh = coordinator.inspect("session-1", now=120.0, stale_after_seconds=30)
    stale = coordinator.inspect("session-1", now=140.0, stale_after_seconds=30)
    assert fresh.stale is False
    assert fresh.age_seconds == 20.0
    assert stale.stale is True
    assert stale.age_seconds == 40.0
    assert stale.ttl_seconds == 30


def test_redis_lane_coordinator_mirrors_owner_state_to_repository() -> None:
    redis_client = FakeLaneRedisClient()
    state = FakeLaneStateRepository()
    coordinator = RedisLaneCoordinator(redis_client, state_repository=state)
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")

    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60, now=100.0)

    assert ownership is not None
    assert state.writes[0]["session_key"] == "session-1"
    assert state.writes[0]["worker_id"] == "worker-a"
    assert state.writes[0]["task_id"] == "task-a"
    assert state.writes[0]["owner_token"] == "worker-a:task-a"
    assert state.writes[0]["state"] == "owned"
    assert state.writes[0]["ttl_seconds"] == 60
    assert state.writes[0]["acquired_at"] == 100.0
    renewed = coordinator.renew(ownership, now=120.0)
    assert renewed is not None
    assert state.writes[1]["renewed_at"] == 120.0
    assert state.writes[1]["state"] == "owned"
    assert coordinator.release(renewed) is True
    assert state.releases[0]["session_key"] == "session-1"
    assert state.releases[0]["owner_token"] == "worker-a:task-a"


def test_redis_lane_coordinator_ignores_state_repository_failures() -> None:
    redis_client = FakeLaneRedisClient()
    coordinator = RedisLaneCoordinator(
        redis_client,
        state_repository=FakeLaneStateRepository(fail=True),
    )
    owner = LaneOwnerToken(worker_id="worker-a", task_id="task-a")

    ownership = coordinator.acquire("session-1", owner=owner, ttl_seconds=60)

    assert ownership is not None
    renewed = coordinator.renew(ownership)
    assert renewed is not None
    assert coordinator.release(renewed) is True
    assert coordinator.is_owned("session-1") is False
