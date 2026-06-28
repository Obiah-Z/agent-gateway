from __future__ import annotations

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.domain.models import InboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
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
        worker_id: str = "local-worker",
    ) -> None:
        self.dispatcher = dispatcher
        self.channels = channels
        self.delivery_runtime = delivery_runtime
        self.redis_client = redis_client
        self.lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self.worker_id = worker_id

    async def __call__(self, task: TaskInstance) -> str:
        """按原 dispatcher 链路执行入站消息并投递最终回复。"""

        lock_key = self._lock_key(task)
        lock_value = f"{self.worker_id}:{task.id}"
        acquired = False
        if self.redis_client is not None and self.redis_client.enabled and lock_key:
            try:
                acquired = self.redis_client.acquire_lock(
                    lock_key,
                    value=lock_value,
                    ttl_seconds=self.lock_ttl_seconds,
                )
            except Exception as exc:
                raise RetryableTaskError(f"agent inbound session lock unavailable: {exc}") from exc
            if not acquired:
                raise RetryableTaskError(f"agent inbound session locked: {task.session_key}")
        try:
            inbound = inbound_from_task(task)
            result = await self.dispatcher.dispatch_inbound(inbound)
            delivery_id = await self.dispatcher.deliver_reply(self.channels, result)
            if inbound.channel == "cli" and self.delivery_runtime is not None:
                await self.delivery_runtime.flush_once()
            return f"agent inbound delivered: {delivery_id}"
        finally:
            if acquired and self.redis_client is not None and lock_key:
                try:
                    self.redis_client.release_lock(lock_key, value=lock_value)
                except Exception:
                    pass

    @staticmethod
    def _lock_key(task: TaskInstance) -> str:
        """生成 agent_inbound session 互斥锁 key。"""

        session_key = task.session_key.strip()
        if not session_key:
            return ""
        return f"gateway:lock:agent_inbound:{session_key}"
