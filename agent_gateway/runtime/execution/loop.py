"""Agent 单轮执行闭环。

本模块负责把会话历史、workspace prompt、记忆和工具策略组合成一次模型调用，并将模型
返回后的完整消息历史写回 SessionStore。模型容灾、工具调用循环等细节由 ResilienceRunner
承接。
"""

from __future__ import annotations

import asyncio
import json
import time

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.config import GatewaySettings
from agent_gateway.ai.context.prompt import PromptAssembler
from agent_gateway.runtime.domain.models import AgentHandoffRequest, AgentReply
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.execution.resilience import ResilienceRunner
from agent_gateway.runtime.state.context import ContextGuard
from agent_gateway.runtime.state.store import SessionStore


def memory_scope_from_session_key(session_key: str) -> str:
    """从 session_key 推导用户记忆作用域，避免不同用户共享个人记忆。"""

    raw = str(session_key or "").strip()
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) >= 3 and parts[0] == "agent":
        # 去掉 agent_id，让同一用户在多个 Agent 间可以共享个人记忆。
        return "user:" + ":".join(parts[2:])
    return raw


class AgentLoopRunner:
    """执行一个 Agent 的对话轮次或后台任务轮次。"""

    def __init__(
        self,
        settings: GatewaySettings,
        agents: AgentManager,
        sessions: SessionStore,
        prompt_assembler: PromptAssembler,
        resilience_runner: ResilienceRunner,
        context_guard: ContextGuard | None = None,
    ) -> None:
        self.settings = settings
        self.agents = agents
        self.sessions = sessions
        self.prompt_assembler = prompt_assembler
        self.resilience_runner = resilience_runner
        self.event_store: RuntimeEventStore | None = None
        self.context_guard = context_guard or ContextGuard(
            safe_limit=settings.context_safe_limit,
            max_tool_chars=settings.max_tool_output_chars,
        )

    async def run_turn(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        *,
        channel: str = "gateway",
        correlation_id: str = "",
    ) -> AgentReply:
        """用户对话入口，默认使用完整 prompt 和记忆上下文。"""

        return await self.run_task_turn(
            agent_id=agent_id,
            session_key=session_key,
            user_text=user_text,
            channel=channel,
            mode="full",
            correlation_id=correlation_id,
        )

    async def run_task_turn(
        self,
        *,
        agent_id: str,
        session_key: str,
        user_text: str,
        channel: str,
        mode: str,
        correlation_id: str = "",
        disabled_tools: list[str] | None = None,
    ) -> AgentReply:
        """执行一次 Agent 轮次。

        流程：加载会话历史 -> 追加本轮用户输入 -> 组装系统 prompt -> 调用模型执行器 ->
        用模型返回的完整 messages 重写 JSONL 会话。
        """

        agent = self.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"agent '{agent_id}' not found")

        started_at = time.time()
        self._record(
            "agent.turn.started",
            status="ok",
            component="agent_loop",
            message=f"Agent turn started: {agent_id}",
            correlation_id=correlation_id,
            agent_id=agent_id,
            session_key=session_key,
            channel=channel,
            metadata={"mode": mode, "input_length": len(user_text)},
        )
        messages = self.sessions.load_messages(agent_id, session_key)
        previous_message_count = len(messages)
        messages.append({"role": "user", "content": user_text})
        allowed_tools = agent.allowed_tool_names(self.resilience_runner.tools.names())
        disabled = {name for name in (disabled_tools or []) if name}
        if disabled:
            allowed_tools = [name for name in allowed_tools if name not in disabled]
        memory_user_scope = memory_scope_from_session_key(session_key)

        # PromptAssembler 会按 Agent 策略合并 workspace 文件、记忆召回和技能说明。
        system_prompt = self.prompt_assembler.build(
            agent,
            mode=mode,
            channel=channel,
            user_text=user_text,
            runtime_context={
                "agent_id": agent.id,
                "channel": channel,
                "model": agent.effective_model(self.settings.model_id),
                "disabled_tools": ", ".join(sorted(disabled)) if disabled else "",
                "memory_user_scope": memory_user_scope,
            },
        )

        self.resilience_runner.event_store = self.event_store
        runtime_context = {
            "agent_id": agent_id,
            "session_key": session_key,
            "channel": channel,
            "correlation_id": correlation_id,
            "disabled_tools": sorted(disabled),
            "memory_user_scope": memory_user_scope,
        }
        try:
            result = await asyncio.to_thread(
                self.resilience_runner.run,
                system_prompt,
                messages,
                model=agent.effective_model(self.settings.model_id),
                allowed_tools=allowed_tools,
                runtime_context=runtime_context,
            )
            # Handoff 只能由本轮新产生的工具结果触发。旧会话历史里可能残留
            # agent_handoff_request，不能让历史请求污染当前用户消息的路由。
            current_turn_messages = result.messages[previous_message_count:]
            handoff_request = self._extract_handoff_request(
                current_turn_messages,
                source_agent_id=agent_id,
            )
            # ResilienceRunner 返回的是经过工具调用闭环后的完整历史，用它覆盖会话文件。
            self.sessions.rewrite_messages(agent_id, session_key, result.messages)
            self._record(
                "agent.turn.completed",
                status="ok",
                component="agent_loop",
                message=f"Agent turn completed: {agent_id}",
                correlation_id=correlation_id,
                agent_id=agent_id,
                session_key=session_key,
                channel=channel,
                metadata={
                    "mode": mode,
                    "duration_ms": round((time.time() - started_at) * 1000, 1),
                    "stop_reason": result.stop_reason,
                    "tool_calls": list(result.tool_calls),
                    "handoff_target_agent_id": handoff_request.target_agent_id
                    if handoff_request
                    else "",
                    "profile": getattr(result, "profile_name", ""),
                    "model": getattr(result, "model", ""),
                },
            )
            return AgentReply(
                agent_id=agent_id,
                session_key=session_key,
                text=result.text,
                stop_reason=result.stop_reason,
                tool_calls=result.tool_calls,
                handoff_request=handoff_request,
            )
        except Exception as exc:
            self._record(
                "agent.turn.failed",
                status="error",
                component="agent_loop",
                message=f"Agent turn failed: {agent_id}",
                correlation_id=correlation_id,
                agent_id=agent_id,
                session_key=session_key,
                channel=channel,
                error=exc,
                metadata={
                    "mode": mode,
                    "duration_ms": round((time.time() - started_at) * 1000, 1),
                },
            )
            raise

    def _extract_handoff_request(
        self,
        messages: list[dict[str, object]],
        *,
        source_agent_id: str,
    ) -> AgentHandoffRequest | None:
        """从工具结果中提取最新的运行时 handoff 请求。"""

        for message in reversed(messages):
            for text in self._iter_content_text(message.get("content")):
                try:
                    payload = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") != "agent_handoff_request":
                    continue
                target_agent_id = str(payload.get("target_agent_id") or "").strip()
                handoff_prompt = str(payload.get("handoff_prompt") or "").strip()
                if not target_agent_id or not handoff_prompt:
                    continue
                return AgentHandoffRequest(
                    target_agent_id=target_agent_id,
                    handoff_prompt=handoff_prompt,
                    reason=str(payload.get("reason") or "").strip(),
                    scope=str(payload.get("scope") or "one-shot").strip() or "one-shot",
                    source_agent_id=source_agent_id,
                    user_goal=str(payload.get("user_goal") or "").strip(),
                )
        return None

    def _iter_content_text(self, content: object) -> list[str]:
        """兼容纯文本、content block 和 tool_result 嵌套内容。"""

        if isinstance(content, str):
            return [content]
        if not isinstance(content, list):
            return []
        texts: list[str] = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            block_text = block.get("text") if block.get("type") == "text" else block.get("content")
            if isinstance(block_text, str):
                texts.append(block_text)
            elif isinstance(block_text, list):
                texts.extend(self._iter_content_text(block_text))
        return texts

    def _record(self, event_type: str, **kwargs) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.record(event_type, **kwargs)
        except Exception:
            pass
