from __future__ import annotations

import asyncio

from agent_gateway.agents import AgentManager
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.models import (
    AgentReply,
    DispatchResult,
    InboundMessage,
    ProactiveTarget,
)
from agent_gateway.router import BindingTable, resolve_route
from agent_gateway.runtime.lanes import CommandQueue
from agent_gateway.runtime.loop import AgentLoopRunner


class GatewayDispatcher:
    def __init__(
        self,
        agents: AgentManager,
        bindings: BindingTable,
        runner: AgentLoopRunner,
        command_queue: CommandQueue,
        delivery_queue: DeliveryQueue,
    ) -> None:
        self.agents = agents
        self.bindings = bindings
        self.runner = runner
        self.command_queue = command_queue
        self.delivery_queue = delivery_queue

    async def dispatch_inbound(
        self,
        inbound: InboundMessage,
        *,
        forced_agent_id: str = "",
    ) -> DispatchResult:
        route = resolve_route(
            self.bindings,
            self.agents,
            inbound,
            forced_agent_id=forced_agent_id,
        )
        reply = await self._execute_lane_task(
            lane_name=route.session_key,
            coroutine_factory=lambda: self.runner.run_turn(
                route.agent_id,
                route.session_key,
                inbound.text,
                channel=inbound.channel,
            ),
        )
        return DispatchResult(inbound=inbound, route=route, reply=reply)

    async def dispatch_background(
        self,
        *,
        agent_id: str,
        session_key: str,
        prompt: str,
        channel: str,
        mode: str = "minimal",
        lane_name: str = "",
    ) -> AgentReply:
        return await self._execute_lane_task(
            lane_name=lane_name or session_key,
            coroutine_factory=lambda: self.runner.run_task_turn(
                agent_id=agent_id,
                session_key=session_key,
                user_text=prompt,
                channel=channel,
                mode=mode,
            ),
        )

    async def _execute_lane_task(
        self,
        *,
        lane_name: str,
        coroutine_factory,
    ) -> AgentReply:
        def _run() -> object:
            return asyncio.run(coroutine_factory())

        future = self.command_queue.enqueue(lane_name, _run, max_concurrency=1)
        reply = await asyncio.to_thread(future.result)
        return reply  # type: ignore[return-value]

    async def deliver_reply(
        self,
        channels: ChannelManager,
        result: DispatchResult,
    ) -> str:
        del channels
        metadata = dict(result.inbound.metadata)
        metadata.update(
            {
                "account_id": result.inbound.account_id,
                "sender_id": result.inbound.sender_id,
                "session_key": result.reply.session_key,
                "agent_id": result.reply.agent_id,
                "stop_reason": result.reply.stop_reason,
                "kind": metadata.get("kind", "reply"),
            }
        )
        delivery_id = await asyncio.to_thread(
            self.delivery_queue.enqueue,
            result.inbound.channel,
            result.inbound.peer_id,
            result.reply.text,
            metadata,
        )
        print(
            "[dispatcher] reply queued:"
            f" delivery_id={delivery_id}"
            f" channel={result.inbound.channel}"
            f" to={result.inbound.peer_id}"
            f" session={result.reply.session_key}"
        )
        return delivery_id

    async def deliver_text(
        self,
        channels: ChannelManager,
        target: ProactiveTarget,
        text: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> str:
        del channels
        payload = dict(metadata or {})
        payload.update(
            {
                "account_id": target.account_id,
                "agent_id": target.agent_id,
                "kind": payload.get("kind", "proactive"),
            }
        )
        delivery_id = await asyncio.to_thread(
            self.delivery_queue.enqueue,
            target.channel,
            target.peer_id,
            text,
            payload,
        )
        print(
            "[dispatcher] proactive queued:"
            f" delivery_id={delivery_id}"
            f" channel={target.channel}"
            f" to={target.peer_id}"
        )
        return delivery_id
