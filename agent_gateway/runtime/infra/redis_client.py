from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class RedisHealth:
    """Redis 健康检查结果。"""

    enabled: bool
    ok: bool
    url: str
    latency_ms: float | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回可被控制面和 Dashboard 序列化的字典。"""

        return {
            "enabled": self.enabled,
            "ok": self.ok,
            "url": self.url,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RedisRateLimitResult:
    """Redis 固定窗口限流结果。"""

    allowed: bool
    key: str
    limit: int
    count: int
    window_seconds: int

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的限流结果。"""

        return {
            "allowed": self.allowed,
            "key": self.key,
            "limit": self.limit,
            "count": self.count,
            "window_seconds": self.window_seconds,
        }


class RedisClient:
    """Redis 客户端薄封装。

    当前阶段只提供连接和健康检查能力，后续会在这里继续增加去重、锁、
    限流和轻量队列能力。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        socket_timeout_seconds: float = 1.0,
    ) -> None:
        self.enabled = enabled
        self.url = url
        self.socket_timeout_seconds = max(0.05, socket_timeout_seconds)
        self._client: Any | None = None

    def health(self) -> RedisHealth:
        """执行一次轻量 ping 检查。"""

        if not self.enabled:
            return RedisHealth(enabled=False, ok=True, url=self.url)
        try:
            start = time.perf_counter()
            self._get_client().ping()
            latency_ms = (time.perf_counter() - start) * 1000.0
            return RedisHealth(
                enabled=True,
                ok=True,
                url=self.url,
                latency_ms=round(latency_ms, 3),
            )
        except Exception as exc:
            return RedisHealth(
                enabled=True,
                ok=False,
                url=self.url,
                error=str(exc),
            )

    def mark_once(self, key: str, *, ttl_seconds: int, value: str = "1") -> bool:
        """基于 `SET NX EX` 标记一次性 key，首次写入返回 True。"""

        if not self.enabled:
            return True
        if not key:
            return True
        return bool(
            self._get_client().set(
                key,
                value,
                nx=True,
                ex=max(1, ttl_seconds),
            )
        )

    def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        """获取带 token 的 Redis 分布式锁。"""

        if not self.enabled:
            return True
        if not key:
            return True
        return bool(
            self._get_client().set(
                key,
                value,
                nx=True,
                ex=max(1, ttl_seconds),
            )
        )

    def release_lock(self, key: str, *, value: str) -> bool:
        """仅在 value 匹配时释放锁，避免误删其他 worker 的锁。"""

        if not self.enabled:
            return True
        if not key:
            return True
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        end
        return 0
        """
        return bool(self._get_client().eval(script, 1, key, value))

    def renew_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
        """仅在 value 匹配时续租锁 TTL，避免误续租其他 worker 的锁。"""

        if not self.enabled:
            return True
        if not key:
            return True
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("EXPIRE", KEYS[1], ARGV[2])
        end
        return 0
        """
        return bool(
            self._get_client().eval(
                script,
                1,
                key,
                value,
                str(max(1, ttl_seconds)),
            )
        )

    def lock_exists(self, key: str) -> bool:
        """检查锁 key 是否存在，用于 reserve 阶段避开热点 session。"""

        if not self.enabled:
            return False
        if not key:
            return False
        return bool(self._get_client().exists(key))

    def check_fixed_window_rate_limit(
        self,
        key_prefix: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RedisRateLimitResult:
        """使用 Redis `INCR + EXPIRE` 执行固定窗口限流。"""

        safe_limit = int(limit)
        safe_window = max(1, int(window_seconds))
        if not self.enabled or safe_limit <= 0:
            return RedisRateLimitResult(
                allowed=True,
                key="",
                limit=safe_limit,
                count=0,
                window_seconds=safe_window,
            )
        current = time.time() if now is None else now
        window_id = int(current // safe_window)
        key = f"{key_prefix.rstrip(':')}:{window_id}"
        client = self._get_client()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, safe_window + 1)
        return RedisRateLimitResult(
            allowed=count <= safe_limit,
            key=key,
            limit=safe_limit,
            count=count,
            window_seconds=safe_window,
        )

    def _get_client(self) -> Any:
        """懒加载 Redis 连接，避免未启用 Redis 时要求安装客户端。"""

        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("redis package is not installed") from exc
        self._client = redis.Redis.from_url(
            self.url,
            socket_timeout=self.socket_timeout_seconds,
            socket_connect_timeout=self.socket_timeout_seconds,
            decode_responses=True,
        )
        return self._client
