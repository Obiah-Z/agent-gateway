from __future__ import annotations

from dataclasses import dataclass

from agent_gateway.runtime.infra.redis_client import RedisClient


@dataclass(frozen=True, slots=True)
class LaneOwnerToken:
    """分布式 lane owner token。

    MVP 阶段保持 `worker_id:task_id` 字符串格式，兼容现有 Redis session lock；
    后续会升级为带 heartbeat、acquired_at、renewed_at 的 JSON metadata。
    """

    worker_id: str
    task_id: str

    @property
    def value(self) -> str:
        """返回写入 Redis 的 owner token。"""

        return f"{self.worker_id}:{self.task_id}"


@dataclass(frozen=True, slots=True)
class LaneOwnership:
    """一次 session lane ownership 持有记录。"""

    session_key: str
    lane_key: str
    owner: LaneOwnerToken
    ttl_seconds: int


class RedisLaneCoordinator:
    """基于 Redis 的 session lane ownership 协调器。

    这一层把“分布式锁”语义提升为“session lane owner”语义：
    同一个 session 同一时间只能有一个 owner，owner 需要续租，释放和续租都必须校验 token。
    """

    def __init__(
        self,
        redis_client: RedisClient | None,
        *,
        namespace: str = "gateway:lane:agent_inbound",
    ) -> None:
        self.redis_client = redis_client
        self.namespace = namespace.strip(":") or "gateway:lane:agent_inbound"

    @property
    def enabled(self) -> bool:
        """Redis 可用配置是否开启。"""

        return bool(self.redis_client is not None and self.redis_client.enabled)

    def lane_key(self, session_key: str) -> str:
        """生成 session lane key。"""

        safe_session = session_key.strip()
        if not safe_session:
            return ""
        return f"{self.namespace}:{safe_session}"

    def acquire(
        self,
        session_key: str,
        *,
        owner: LaneOwnerToken,
        ttl_seconds: int,
    ) -> LaneOwnership | None:
        """尝试获取 session lane ownership，失败返回 None。"""

        lane_key = self.lane_key(session_key)
        if not self.enabled or not lane_key:
            return LaneOwnership(
                session_key=session_key,
                lane_key=lane_key,
                owner=owner,
                ttl_seconds=max(1, int(ttl_seconds)),
            )
        acquired = self.redis_client.acquire_lock(
            lane_key,
            value=owner.value,
            ttl_seconds=max(1, int(ttl_seconds)),
        )
        if not acquired:
            return None
        return LaneOwnership(
            session_key=session_key,
            lane_key=lane_key,
            owner=owner,
            ttl_seconds=max(1, int(ttl_seconds)),
        )

    def renew(self, ownership: LaneOwnership) -> bool:
        """续租当前 owner 持有的 lane。"""

        if not self.enabled or not ownership.lane_key:
            return True
        return self.redis_client.renew_lock(
            ownership.lane_key,
            value=ownership.owner.value,
            ttl_seconds=ownership.ttl_seconds,
        )

    def release(self, ownership: LaneOwnership) -> bool:
        """释放当前 owner 持有的 lane。"""

        if not self.enabled or not ownership.lane_key:
            return True
        return self.redis_client.release_lock(
            ownership.lane_key,
            value=ownership.owner.value,
        )

    def is_owned(self, session_key: str) -> bool:
        """检查 session lane 当前是否已有 owner。"""

        lane_key = self.lane_key(session_key)
        if not self.enabled or not lane_key:
            return False
        return self.redis_client.lock_exists(lane_key)
