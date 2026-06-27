from agent_gateway.runtime.infra.redis_client import RedisClient


class FakeRedisClient(RedisClient):
    def __init__(self, *, enabled: bool, should_fail: bool = False) -> None:
        super().__init__(enabled=enabled, url="redis://example.test:6379/0")
        self.should_fail = should_fail

    def _get_client(self):
        if self.should_fail:
            raise RuntimeError("connection refused")
        return type("Client", (), {"ping": lambda self: True})()


class FakeSetRedisClient(RedisClient):
    def __init__(self) -> None:
        super().__init__(enabled=True, url="redis://example.test:6379/0")
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    def _get_client(self):
        values = self.values
        counters = self.counters
        expirations = self.expirations

        class Client:
            def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
                del ex
                if nx and key in values:
                    return False
                values[key] = value
                return True

            def incr(self, key: str) -> int:
                counters[key] = counters.get(key, 0) + 1
                return counters[key]

            def expire(self, key: str, ttl: int) -> None:
                expirations[key] = ttl

        return Client()


def test_redis_health_is_ok_when_disabled() -> None:
    health = FakeRedisClient(enabled=False).health()

    assert health.enabled is False
    assert health.ok is True
    assert health.to_dict()["url"] == "redis://example.test:6379/0"


def test_redis_health_reports_ping_success() -> None:
    health = FakeRedisClient(enabled=True).health()

    assert health.enabled is True
    assert health.ok is True
    assert health.latency_ms is not None
    assert health.error == ""


def test_redis_health_reports_ping_failure() -> None:
    health = FakeRedisClient(enabled=True, should_fail=True).health()

    assert health.enabled is True
    assert health.ok is False
    assert health.error == "connection refused"


def test_redis_mark_once_uses_set_nx_ex() -> None:
    client = FakeSetRedisClient()

    assert client.mark_once("key-1", ttl_seconds=60) is True
    assert client.mark_once("key-1", ttl_seconds=60) is False


def test_redis_fixed_window_rate_limit_counts_per_window() -> None:
    client = FakeSetRedisClient()

    first = client.check_fixed_window_rate_limit(
        "gateway:rate:test",
        limit=2,
        window_seconds=60,
        now=120.0,
    )
    second = client.check_fixed_window_rate_limit(
        "gateway:rate:test",
        limit=2,
        window_seconds=60,
        now=121.0,
    )
    third = client.check_fixed_window_rate_limit(
        "gateway:rate:test",
        limit=2,
        window_seconds=60,
        now=122.0,
    )

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.key == "gateway:rate:test:2"
    assert third.count == 3
    assert client.expirations["gateway:rate:test:2"] == 61
