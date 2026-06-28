from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any

from agent_gateway.runtime.infra.redis_client import RedisClient


@dataclass(frozen=True, slots=True)
class LaneOwnerToken:
    """分布式 lane owner token。

    `value` 保持 `worker_id:task_id` 字符串格式，用于错误消息和旧 value 兼容；
    Redis lane value 默认写入 JSON metadata，便于后续 heartbeat、接管和 Dashboard inspect。
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
    owner_value: str = ""


@dataclass(frozen=True, slots=True)
class LaneInfo:
    """当前 Redis 中可观测的 session lane owner 信息。"""

    session_key: str
    lane_key: str
    owned: bool
    worker_id: str = ""
    task_id: str = ""
    owner_value: str = ""
    acquired_at: float = 0.0
    renewed_at: float = 0.0
    age_seconds: float = 0.0
    stale: bool = False
    ttl_seconds: int = 0
    legacy: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回可用于 runtime.status / Dashboard 的字典。"""

        return {
            "session_key": self.session_key,
            "lane_key": self.lane_key,
            "owned": self.owned,
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "owner_value": self.owner_value,
            "acquired_at": self.acquired_at,
            "renewed_at": self.renewed_at,
            "age_seconds": self.age_seconds,
            "stale": self.stale,
            "ttl_seconds": self.ttl_seconds,
            "legacy": self.legacy,
        }


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
        state_repository: Any | None = None,
    ) -> None:
        self.redis_client = redis_client
        self.namespace = namespace.strip(":") or "gateway:lane:agent_inbound"
        self.state_repository = state_repository

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
        now: float | None = None,
    ) -> LaneOwnership | None:
        """尝试获取 session lane ownership，失败返回 None。"""

        lane_key = self.lane_key(session_key)
        current = time.time() if now is None else float(now)
        owner_value = self._encode_owner(
            session_key=session_key,
            lane_key=lane_key,
            owner=owner,
            acquired_at=current,
            renewed_at=current,
        )
        if not self.enabled or not lane_key:
            ownership = LaneOwnership(
                session_key=session_key,
                lane_key=lane_key,
                owner=owner,
                ttl_seconds=max(1, int(ttl_seconds)),
                owner_value=owner_value,
            )
            self._write_state(ownership, acquired_at=current, renewed_at=current, state="owned")
            return ownership
        acquired = self.redis_client.acquire_lock(
            lane_key,
            value=owner_value,
            ttl_seconds=max(1, int(ttl_seconds)),
        )
        if not acquired:
            return None
        ownership = LaneOwnership(
            session_key=session_key,
            lane_key=lane_key,
            owner=owner,
            ttl_seconds=max(1, int(ttl_seconds)),
            owner_value=owner_value,
        )
        self._write_state(ownership, acquired_at=current, renewed_at=current, state="owned")
        return ownership

    def renew(self, ownership: LaneOwnership, *, now: float | None = None) -> LaneOwnership | None:
        """续租当前 owner 持有的 lane。"""

        if not self.enabled or not ownership.lane_key:
            return ownership
        current = time.time() if now is None else float(now)
        acquired_at = self._decode_owner(ownership.owner_value).acquired_at or current
        new_value = self._encode_owner(
            session_key=ownership.session_key,
            lane_key=ownership.lane_key,
            owner=ownership.owner,
            acquired_at=acquired_at,
            renewed_at=current,
        )
        replaced = False
        replace_method = getattr(self.redis_client, "replace_lock_value", None)
        if replace_method is not None:
            replaced = bool(
                replace_method(
                    ownership.lane_key,
                    expected_value=ownership.owner_value,
                    new_value=new_value,
                    ttl_seconds=ownership.ttl_seconds,
                )
            )
        if not replaced and ownership.owner_value == ownership.owner.value:
            replaced = self.redis_client.renew_lock(
                ownership.lane_key,
                value=ownership.owner.value,
                ttl_seconds=ownership.ttl_seconds,
            )
            new_value = ownership.owner_value
        if not replaced:
            return None
        renewed = LaneOwnership(
            session_key=ownership.session_key,
            lane_key=ownership.lane_key,
            owner=ownership.owner,
            ttl_seconds=ownership.ttl_seconds,
            owner_value=new_value,
        )
        self._write_state(
            renewed,
            acquired_at=acquired_at,
            renewed_at=current,
            state="owned",
        )
        return renewed

    def release(self, ownership: LaneOwnership) -> bool:
        """释放当前 owner 持有的 lane。"""

        if not self.enabled or not ownership.lane_key:
            self._release_state(ownership)
            return True
        released = self.redis_client.release_lock(
            ownership.lane_key,
            value=ownership.owner_value,
        )
        if released:
            self._release_state(ownership)
        return released

    def is_owned(self, session_key: str) -> bool:
        """检查 session lane 当前是否已有 owner。"""

        lane_key = self.lane_key(session_key)
        if not self.enabled or not lane_key:
            return False
        return self.redis_client.lock_exists(lane_key)

    def inspect(
        self,
        session_key: str,
        *,
        now: float | None = None,
        stale_after_seconds: int = 0,
    ) -> LaneInfo:
        """读取当前 session lane owner metadata。"""

        lane_key = self.lane_key(session_key)
        if not self.enabled or not lane_key:
            return LaneInfo(session_key=session_key, lane_key=lane_key, owned=False)
        raw = self.redis_client.get_value(lane_key)
        if not raw:
            return LaneInfo(session_key=session_key, lane_key=lane_key, owned=False)
        info = self._decode_owner(raw, session_key=session_key, lane_key=lane_key)
        return self._with_staleness(
            info,
            now=time.time() if now is None else float(now),
            stale_after_seconds=max(0, int(stale_after_seconds)),
        )

    def _encode_owner(
        self,
        *,
        session_key: str,
        lane_key: str,
        owner: LaneOwnerToken,
        acquired_at: float,
        renewed_at: float,
    ) -> str:
        """编码 lane owner metadata。"""

        return json.dumps(
            {
                "version": 1,
                "session_key": session_key,
                "lane_key": lane_key,
                "worker_id": owner.worker_id,
                "task_id": owner.task_id,
                "owner_token": owner.value,
                "acquired_at": acquired_at,
                "renewed_at": renewed_at,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _write_state(
        self,
        ownership: LaneOwnership,
        *,
        acquired_at: float,
        renewed_at: float,
        state: str,
    ) -> None:
        """把 lane owner 状态镜像到持久仓储；失败不影响 Redis 主路径。"""

        writer = getattr(self.state_repository, "write_session_lane", None)
        if writer is None:
            return
        try:
            writer(
                {
                    "session_key": ownership.session_key,
                    "lane_key": ownership.lane_key,
                    "worker_id": ownership.owner.worker_id,
                    "task_id": ownership.owner.task_id,
                    "owner_token": ownership.owner.value,
                    "state": state,
                    "ttl_seconds": ownership.ttl_seconds,
                    "acquired_at": acquired_at,
                    "renewed_at": renewed_at,
                    "updated_at": renewed_at,
                    "metadata": {
                        "owner_value": ownership.owner_value,
                        "source": "redis_lane_coordinator",
                    },
                }
            )
        except Exception:
            return

    def _release_state(self, ownership: LaneOwnership) -> None:
        """把 lane 释放状态镜像到持久仓储；失败不影响释放结果。"""

        releaser = getattr(self.state_repository, "release_session_lane", None)
        if releaser is None:
            return
        try:
            releaser(
                ownership.session_key,
                owner_token=ownership.owner.value,
                now=time.time(),
            )
        except Exception:
            return

    def _decode_owner(
        self,
        raw_value: str,
        *,
        session_key: str = "",
        lane_key: str = "",
    ) -> LaneInfo:
        """解析 Redis 中的 lane owner value，兼容旧字符串 token。"""

        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            worker_id, _, task_id = raw_value.partition(":")
            return LaneInfo(
                session_key=session_key,
                lane_key=lane_key,
                owned=bool(raw_value),
                worker_id=worker_id,
                task_id=task_id,
                owner_value=raw_value,
                legacy=True,
            )
        if not isinstance(payload, dict):
            return LaneInfo(
                session_key=session_key,
                lane_key=lane_key,
                owned=bool(raw_value),
                owner_value=raw_value,
                legacy=True,
            )
        return LaneInfo(
            session_key=str(payload.get("session_key") or session_key),
            lane_key=str(payload.get("lane_key") or lane_key),
            owned=True,
            worker_id=str(payload.get("worker_id", "")),
            task_id=str(payload.get("task_id", "")),
            owner_value=raw_value,
            acquired_at=float(payload.get("acquired_at", 0.0) or 0.0),
            renewed_at=float(payload.get("renewed_at", 0.0) or 0.0),
            legacy=False,
        )

    def _with_staleness(
        self,
        info: LaneInfo,
        *,
        now: float,
        stale_after_seconds: int,
    ) -> LaneInfo:
        """根据 renewed_at 计算 lane owner 年龄和 stale 状态。"""

        renewed_at = info.renewed_at or info.acquired_at
        age_seconds = max(0.0, now - renewed_at) if renewed_at > 0 else 0.0
        stale = bool(stale_after_seconds > 0 and age_seconds >= stale_after_seconds)
        return LaneInfo(
            session_key=info.session_key,
            lane_key=info.lane_key,
            owned=info.owned,
            worker_id=info.worker_id,
            task_id=info.task_id,
            owner_value=info.owner_value,
            acquired_at=info.acquired_at,
            renewed_at=info.renewed_at,
            age_seconds=age_seconds,
            stale=stale,
            ttl_seconds=stale_after_seconds,
            legacy=info.legacy,
        )
