from __future__ import annotations

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import InboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.tasks.models import TaskInstance


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
    ) -> None:
        self.dispatcher = dispatcher
        self.channels = channels
        self.delivery_runtime = delivery_runtime

    async def __call__(self, task: TaskInstance) -> str:
        """按原 dispatcher 链路执行入站消息并投递最终回复。"""

        inbound = inbound_from_task(task)
        result = await self.dispatcher.dispatch_inbound(inbound)
        delivery_id = await self.dispatcher.deliver_reply(self.channels, result)
        if inbound.channel == "cli" and self.delivery_runtime is not None:
            await self.delivery_runtime.flush_once()
        return f"agent inbound delivered: {delivery_id}"
