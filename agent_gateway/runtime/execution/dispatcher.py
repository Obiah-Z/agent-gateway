"""消息分发应用服务。

dispatcher 是入站消息进入 Agent Loop 前的核心编排点：负责路由、会话 lane 串行化、
以及把回复写入可靠投递队列。它不直接调用任何通道的 send，避免模型执行和外部投递
失败互相耦合。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.domain.models import (
    AgentReply,
    DispatchResult,
    InboundMessage,
    ProactiveTarget,
)
from agent_gateway.runtime.domain.router import BindingTable, resolve_route
from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.execution.loop import AgentLoopRunner
from agent_gateway.runtime.observability.events import RuntimeEventStore, ensure_correlation_id, new_correlation_id


class GatewayDispatcher:
    """统一调度用户消息和主动任务。"""

    def __init__(
        self,
        agents: AgentManager,
        bindings: BindingTable,
        runner: AgentLoopRunner,
        command_queue: CommandQueue,
        delivery_queue: DeliveryQueue,
        event_store: RuntimeEventStore | None = None,
        task_queue: Any | None = None,
    ) -> None:
        self.agents = agents
        self.bindings = bindings
        self.runner = runner
        self.command_queue = command_queue
        self.delivery_queue = delivery_queue
        self.event_store = event_store
        self.task_queue = task_queue
        self.runner.event_store = event_store

    async def dispatch_inbound(
        self,
        inbound: InboundMessage,
        *,
        forced_agent_id: str = "",
    ) -> DispatchResult:
        """处理用户触发的入站消息。

        同一个 session_key 会进入同一条命名 lane，确保同一会话内的多轮上下文按顺序
        写入，避免并发消息互相覆盖会话历史。
        """

        correlation_id = ensure_correlation_id(inbound.metadata, prefix=inbound.channel or "inbound")
        self._record(
            "inbound.received",
            status="ok",
            component="dispatcher",
            message="Inbound message received",
            correlation_id=correlation_id,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            metadata={"sender_id": inbound.sender_id, "text_length": len(inbound.text)},
        )
        route = resolve_route(
            self.bindings,
            self.agents,
            inbound,
            forced_agent_id=forced_agent_id,
        )
        self._record(
            "route.resolved",
            status="ok",
            component="dispatcher",
            message=f"Route resolved to agent '{route.agent_id}'",
            correlation_id=correlation_id,
            agent_id=route.agent_id,
            session_key=route.session_key,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            metadata={
                "forced_agent_id": forced_agent_id,
                "matched_binding": route.matched_binding.display()
                if route.matched_binding
                else "",
            },
        )
        orchestration_reply = self._maybe_enqueue_orchestration(
            inbound,
            route_agent_id=route.agent_id,
            route_session_key=route.session_key,
            correlation_id=correlation_id,
        )
        if orchestration_reply is not None:
            return DispatchResult(inbound=inbound, route=route, reply=orchestration_reply)
        final_route = route
        try:
            reply = await self._execute_lane_task(
                lane_name=route.session_key,
                coroutine_factory=lambda: self.runner.run_turn(
                    route.agent_id,
                    route.session_key,
                    inbound.text,
                    channel=inbound.channel,
                    correlation_id=correlation_id,
                ),
            )
            if reply.handoff_request is not None:
                reply, final_route = await self._execute_handoff(
                    inbound,
                    source_reply=reply,
                    correlation_id=correlation_id,
                )
        except Exception as exc:
            self._record(
                "agent.turn.failed",
                status="error",
                component="dispatcher",
                message="Agent turn failed",
                correlation_id=correlation_id,
                agent_id=route.agent_id,
                session_key=route.session_key,
                channel=inbound.channel,
                account_id=inbound.account_id,
                peer_id=inbound.peer_id,
                error=exc,
            )
            raise
        return DispatchResult(inbound=inbound, route=final_route, reply=reply)

    def _maybe_enqueue_orchestration(
        self,
        inbound: InboundMessage,
        *,
        route_agent_id: str,
        route_session_key: str,
        correlation_id: str,
    ) -> AgentReply | None:
        """对高置信复杂任务直接启动主控协作，避免依赖入口 Agent 自觉调用工具。"""

        if self.task_queue is None:
            return None
        if not self._should_auto_orchestrate(inbound.text, route_agent_id):
            return None
        run_id = self._orchestration_run_id(route_agent_id, inbound.text)
        task = self.task_queue.enqueue(
            task_type="agent_collaboration",
            source="auto_orchestration",
            agent_id="main",
            session_key=f"orchestration:{run_id}:controller:main",
            priority=80,
            idempotency_key=f"auto_orchestration:{route_agent_id}:{run_id}",
            payload={
                "user_goal": inbound.text,
                "controller_agent_id": "main",
                "run_id": run_id,
                "channel": inbound.channel,
                "mode": "minimal",
                "max_iterations": 8,
                "disabled_tools": ["memory_write"],
                "correlation_id": correlation_id,
                "response_target": {
                    "channel": inbound.channel,
                    "account_id": inbound.account_id,
                    "peer_id": inbound.peer_id,
                    "source_session_key": route_session_key,
                    "source_agent_id": route_agent_id,
                },
            },
            metadata={
                "origin": "dispatcher_auto_orchestration",
                "source_agent_id": route_agent_id,
                "source_session_key": route_session_key,
                "correlation_id": correlation_id,
            },
        )
        self._record(
            "agent.orchestration.enqueued",
            status="ok",
            component="dispatcher",
            message="Auto orchestration task enqueued",
            correlation_id=correlation_id,
            agent_id=route_agent_id,
            session_key=route_session_key,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            metadata={"task_id": task.id, "run_id": run_id, "controller_agent_id": "main"},
        )
        return AgentReply(
            agent_id=route_agent_id,
            session_key=route_session_key,
            text=(
                "已启动主控协作任务。主 Agent 会持续规划下一步、委托专家 Agent 执行，"
                "完成后会把最终结果继续推送到当前会话。"
            ),
            stop_reason="orchestration_enqueued",
            tool_calls=["start_agent_orchestration"],
        )

    def _should_auto_orchestrate(self, text: str, route_agent_id: str) -> bool:
        """判断是否应跳过入口 Agent，直接进入主控协作。"""

        if route_agent_id not in {"main", "feishu-entry", "wework-entry"}:
            return False
        normalized = text.lower()
        has_repo = "github.com/" in normalized or "仓库" in text or "repo" in normalized
        if not has_repo:
            return False
        adoption_signals = ("引入 gateway", "适合引入", "采纳计划", "风险审查", "正式报告")
        if any(signal in normalized for signal in adoption_signals):
            return True
        chinese_signals = ("引入", "风险", "采纳", "报告")
        return sum(1 for signal in chinese_signals if signal in text) >= 3

    @staticmethod
    def _orchestration_run_id(agent_id: str, user_goal: str) -> str:
        """生成稳定 run_id，用于自动编排任务幂等。"""

        seed = f"{agent_id}:{user_goal}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:12]

    async def _execute_handoff(
        self,
        inbound: InboundMessage,
        *,
        source_reply: AgentReply,
        correlation_id: str,
    ):
        """执行 Agent 请求的 one-shot 专家转交。"""

        request = source_reply.handoff_request
        if request is None:
            route = resolve_route(self.bindings, self.agents, inbound)
            return source_reply, route
        target_agent_id = request.target_agent_id.strip()
        if not target_agent_id:
            raise ValueError("handoff target_agent_id is empty")
        if target_agent_id == source_reply.agent_id:
            raise ValueError("handoff target_agent_id must differ from source agent_id")
        if self.agents.get(target_agent_id) is None:
            raise ValueError(f"handoff target agent not found: {target_agent_id}")

        target_route = resolve_route(
            self.bindings,
            self.agents,
            inbound,
            forced_agent_id=target_agent_id,
        )
        self._record(
            "agent.handoff.requested",
            status="ok",
            component="dispatcher",
            message=(
                f"Agent handoff requested: {source_reply.agent_id} -> "
                f"{target_route.agent_id}"
            ),
            correlation_id=correlation_id,
            agent_id=source_reply.agent_id,
            session_key=source_reply.session_key,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            metadata={
                "source_agent_id": source_reply.agent_id,
                "target_agent_id": target_route.agent_id,
                "target_session_key": target_route.session_key,
                "scope": request.scope,
                "reason": request.reason,
                "user_goal": request.user_goal,
            },
        )
        try:
            target_reply = await self._execute_lane_task(
                lane_name=target_route.session_key,
                coroutine_factory=lambda: self.runner.run_turn(
                    target_route.agent_id,
                    target_route.session_key,
                    request.handoff_prompt,
                    channel=inbound.channel,
                    correlation_id=correlation_id,
                ),
            )
        except Exception as exc:
            self._record(
                "agent.handoff.failed",
                status="error",
                component="dispatcher",
                message=(
                    f"Agent handoff failed: {source_reply.agent_id} -> "
                    f"{target_route.agent_id}"
                ),
                correlation_id=correlation_id,
                agent_id=target_route.agent_id,
                session_key=target_route.session_key,
                channel=inbound.channel,
                account_id=inbound.account_id,
                peer_id=inbound.peer_id,
                error=exc,
                metadata={
                    "source_agent_id": source_reply.agent_id,
                    "target_agent_id": target_route.agent_id,
                    "scope": request.scope,
                },
            )
            raise
        self._record(
            "agent.handoff.completed",
            status="ok",
            component="dispatcher",
            message=(
                f"Agent handoff completed: {source_reply.agent_id} -> "
                f"{target_reply.agent_id}"
            ),
            correlation_id=correlation_id,
            agent_id=target_reply.agent_id,
            session_key=target_reply.session_key,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            metadata={
                "source_agent_id": source_reply.agent_id,
                "source_session_key": source_reply.session_key,
                "target_agent_id": target_reply.agent_id,
                "scope": request.scope,
                "reason": request.reason,
            },
        )
        return target_reply, target_route

    async def dispatch_background(
        self,
        *,
        agent_id: str,
        session_key: str,
        prompt: str,
        channel: str,
        mode: str = "minimal",
        lane_name: str = "",
        correlation_id: str = "",
        disabled_tools: list[str] | None = None,
    ) -> AgentReply:
        """处理 heartbeat、cron 等系统主动任务。"""

        correlation_id = correlation_id or new_correlation_id(channel or "task")
        self._record(
            "agent.task.started",
            status="ok",
            component="dispatcher",
            message="Background agent task queued",
            correlation_id=correlation_id,
            agent_id=agent_id,
            session_key=session_key,
            channel=channel,
            metadata={"mode": mode, "lane_name": lane_name or session_key},
        )
        return await self._execute_lane_task(
            lane_name=lane_name or session_key,
            coroutine_factory=lambda: self.runner.run_task_turn(
                agent_id=agent_id,
                session_key=session_key,
                user_text=prompt,
                channel=channel,
                mode=mode,
                correlation_id=correlation_id,
                disabled_tools=disabled_tools,
            ),
        )

    async def _execute_lane_task(
        self,
        *,
        lane_name: str,
        coroutine_factory,
    ) -> AgentReply:
        """把协程封装成 lane 任务执行。

        CommandQueue 是线程侧的命名并发车道；这里用 `asyncio.to_thread` 等待结果，
        让事件循环不被具体 Agent 执行阻塞。
        """

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
        """把普通回复写入可靠投递队列。

        参数中保留 `channels` 是为了兼容旧调用形态；真实发送由 DeliveryRuntime 后台完成。
        """

        del channels
        metadata = dict(result.inbound.metadata)
        correlation_id = ensure_correlation_id(metadata, prefix=result.inbound.channel or "reply")
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
        self._record(
            "delivery.enqueued",
            status="ok",
            component="dispatcher",
            message="Reply enqueued for delivery",
            correlation_id=correlation_id,
            agent_id=result.reply.agent_id,
            session_key=result.reply.session_key,
            channel=result.inbound.channel,
            account_id=result.inbound.account_id,
            peer_id=result.inbound.peer_id,
            delivery_id=delivery_id,
            metadata={"kind": metadata.get("kind", "reply")},
        )
        print(
            "[dispatcher] reply queued:"
            f" delivery_id={delivery_id}"
            f" channel={result.inbound.channel}"
            f" to={result.inbound.peer_id}"
            f" session={result.reply.session_key}"
        )
        return delivery_id

    async def deliver_progress(
        self,
        channels: ChannelManager,
        inbound: InboundMessage,
        text: str,
        *,
        stage: str = "started",
    ) -> str:
        """把准流式进度提示写入可靠投递队列。

        进度提示不绕过 DeliveryRuntime，避免飞书 API 短暂失败时丢失用户反馈。
        """

        metadata = dict(inbound.metadata)
        metadata.update(
            {
                "account_id": inbound.account_id,
                "sender_id": inbound.sender_id,
                "kind": "progress",
                "progress_stage": stage,
            }
        )
        target = ProactiveTarget(
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            agent_id=str(inbound.metadata.get("agent_id", "main")) or "main",
        )
        return await self.deliver_text(channels, target, text, metadata=metadata)

    async def deliver_text(
        self,
        channels: ChannelManager,
        target: ProactiveTarget,
        text: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> str:
        """把主动任务输出写入可靠投递队列。"""

        del channels
        payload = dict(metadata or {})
        correlation_id = ensure_correlation_id(
            payload,
            prefix=f"{target.channel}-{target.agent_id}" if target.channel else "proactive",
        )
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
        self._record(
            "delivery.enqueued",
            status="ok",
            component="dispatcher",
            message="Proactive message enqueued for delivery",
            correlation_id=correlation_id,
            agent_id=target.agent_id,
            channel=target.channel,
            account_id=target.account_id,
            peer_id=target.peer_id,
            delivery_id=delivery_id,
            metadata={"kind": payload.get("kind", "proactive")},
        )
        print(
            "[dispatcher] proactive queued:"
            f" delivery_id={delivery_id}"
            f" channel={target.channel}"
            f" to={target.peer_id}"
        )
        return delivery_id

    def _record(self, event_type: str, **kwargs) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.record(event_type, **kwargs)
        except Exception:
            pass
