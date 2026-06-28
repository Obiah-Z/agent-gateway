from __future__ import annotations

import asyncio

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.domain.models import InboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.tasks.lane import LaneOwnership, LaneOwnerToken, RedisLaneCoordinator
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.worker import RetryableTaskError


def inbound_from_task(task: TaskInstance) -> InboundMessage:
    """从后台任务 payload 恢复标准入站消息。"""

    payload = task.payload
    metadata = dict(payload.get("metadata", {}) or {})
    metadata["background_task_id"] = task.id
    metadata["kind"] = metadata.get("kind", "background_inbound")
    return InboundMessage(
        text=str(payload.get("text", "")),
        sender_id=str(payload.get("sender_id", "")),
        channel=str(payload.get("channel", "")),
        account_id=str(payload.get("account_id", "")),
        peer_id=str(payload.get("peer_id", "")),
        guild_id=str(payload.get("guild_id", "")),
        is_group=bool(payload.get("is_group", False)),
        media=list(payload.get("media", []) or []),
        raw=dict(payload.get("raw", {}) or {}),
        metadata=metadata,
    )


def inbound_to_task_payload(inbound: InboundMessage) -> dict[str, object]:
    """把入站消息序列化为可持久化的后台任务 payload。"""

    return {
        "text": inbound.text,
        "sender_id": inbound.sender_id,
        "channel": inbound.channel,
        "account_id": inbound.account_id,
        "peer_id": inbound.peer_id,
        "guild_id": inbound.guild_id,
        "is_group": inbound.is_group,
        "media": list(inbound.media),
        "raw": dict(inbound.raw),
        "metadata": dict(inbound.metadata),
    }


class AgentInboundTaskHandler:
    """后台执行用户主动触发的长任务入站消息。"""

    def __init__(
        self,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        delivery_runtime: DeliveryRuntime | None = None,
        redis_client: RedisClient | None = None,
        lock_ttl_seconds: int = 300,
        lock_renew_interval_seconds: float | None = None,
        worker_id: str = "local-worker",
        lane_coordinator: RedisLaneCoordinator | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.channels = channels
        self.delivery_runtime = delivery_runtime
        self.redis_client = redis_client
        self.lane_coordinator = lane_coordinator or RedisLaneCoordinator(
            redis_client,
            namespace="gateway:lock:agent_inbound",
        )
        self.lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self.lock_renew_interval_seconds = self._resolve_renew_interval(
            lock_renew_interval_seconds,
            self.lock_ttl_seconds,
        )
        self.worker_id = worker_id

    async def __call__(self, task: TaskInstance) -> str:
        """按原 dispatcher 链路执行入站消息并投递最终回复。"""

        owner = LaneOwnerToken(worker_id=self.worker_id, task_id=task.id)
        ownership: LaneOwnership | None = None
        ownership_ref: dict[str, LaneOwnership] = {}
        renew_task: asyncio.Task[None] | None = None
        if self.lane_coordinator.enabled and task.session_key.strip():
            try:
                ownership = self.lane_coordinator.acquire(
                    task.session_key,
                    owner=owner,
                    ttl_seconds=self.lock_ttl_seconds,
                )
            except Exception as exc:
                raise RetryableTaskError(f"agent inbound session lock unavailable: {exc}") from exc
            if ownership is None:
                raise RetryableTaskError(f"agent inbound session locked: {task.session_key}")
            ownership_ref["current"] = ownership
            renew_task = asyncio.create_task(
                self._renew_lock_until_cancelled(ownership_ref),
                name=f"agent-inbound-lock-renew:{task.id}",
            )
        try:
            inbound = inbound_from_task(task)
            result = await self.dispatcher.dispatch_inbound(inbound)
            delivery_id = await self.dispatcher.deliver_reply(self.channels, result)
            if inbound.channel == "cli" and self.delivery_runtime is not None:
                await self.delivery_runtime.flush_once()
            return f"agent inbound delivered: {delivery_id}"
        finally:
            if renew_task is not None:
                renew_task.cancel()
                try:
                    await renew_task
                except asyncio.CancelledError:
                    pass
            if ownership is not None:
                try:
                    self.lane_coordinator.release(ownership_ref.get("current", ownership))
                except Exception:
                    pass

    def is_session_locked(self, task: TaskInstance) -> bool:
        """检查任务 session 当前是否已被其他 worker 持锁。"""

        if not self.lane_coordinator.enabled:
            return False
        try:
            return self.lane_coordinator.is_owned(task.session_key)
        except Exception:
            # Redis 不可用时不在 reserve 阶段跳过，执行阶段会进入 retrying。
            return False

    def inspect_session_lane(self, task: TaskInstance) -> dict[str, object]:
        """返回任务 session lane 的当前 owner 信息。"""

        if not self.lane_coordinator.enabled:
            return {}
        try:
            return self.lane_coordinator.inspect(task.session_key).to_dict()
        except Exception:
            return {}

    async def _renew_lock_until_cancelled(
        self,
        ownership_ref: dict[str, LaneOwnership],
    ) -> None:
        """定期续租当前任务持有的 session 锁，覆盖长模型调用场景。"""

        while True:
            await asyncio.sleep(self.lock_renew_interval_seconds)
            try:
                renewed = await asyncio.to_thread(
                    self.lane_coordinator.renew,
                    ownership_ref["current"],
                )
            except Exception:
                # 续租失败时不直接中断已在执行的 Agent 回合，避免重复副作用；
                # Redis 锁后续会自然过期，观测和重试治理在后续阶段补齐。
                continue
            if not renewed:
                return
            ownership_ref["current"] = renewed

    @staticmethod
    def _lock_key(task: TaskInstance) -> str:
        """生成 agent_inbound session 互斥锁 key。"""

        session_key = task.session_key.strip()
        if not session_key:
            return ""
        return f"gateway:lock:agent_inbound:{session_key}"

    @staticmethod
    def _resolve_renew_interval(raw_value: float | None, ttl_seconds: int) -> float:
        """计算续租间隔，默认使用 TTL 的三分之一并保留最小 1 秒。"""

        if raw_value is not None:
            return max(0.1, float(raw_value))
        return max(1.0, min(60.0, ttl_seconds / 3.0))
