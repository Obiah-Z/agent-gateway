"""Agent 单轮执行闭环。

本模块负责把会话历史、workspace prompt、记忆和工具策略组合成一次模型调用，并将模型
返回后的完整消息历史写回 SessionStore。模型容灾、工具调用循环等细节由 ResilienceRunner
承接。
"""

from __future__ import annotations

import asyncio
import time

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.config import GatewaySettings
from agent_gateway.ai.context.prompt import PromptAssembler
from agent_gateway.runtime.domain.models import AgentReply
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.execution.resilience import ResilienceRunner
from agent_gateway.runtime.state.context import ContextGuard
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.runtime.user_scope import user_scope_from_session_key


def memory_scope_from_session_key(session_key: str) -> str:
    """从 session_key 推导用户记忆作用域，避免不同用户共享个人记忆。"""

    return user_scope_from_session_key(session_key)


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
        persist_history: bool = True,
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
        messages = self.sessions.load_messages(agent_id, session_key) if persist_history else []
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
            reply_text = result.text
            result_messages = result.messages
            if "start_agent_orchestration" in result.tool_calls:
                reply_text = "已收到，正在处理。本轮结果生成后会继续推送。"
                result_messages = self._replace_last_assistant_text(result.messages, reply_text)
            # ResilienceRunner 返回的是经过工具调用闭环后的完整历史。协作中间轮次
            # 只写 run artifact，避免把 step 会话散落到各 Agent 的普通会话目录。
            if persist_history:
                self.sessions.rewrite_messages(agent_id, session_key, result_messages)
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
                    "profile": getattr(result, "profile_name", ""),
                    "model": getattr(result, "model", ""),
                },
            )
            return AgentReply(
                agent_id=agent_id,
                session_key=session_key,
                text=reply_text,
                stop_reason=result.stop_reason,
                tool_calls=result.tool_calls,
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

    @staticmethod
    def _replace_last_assistant_text(
        messages: list[dict[str, object]],
        text: str,
    ) -> list[dict[str, object]]:
        """把后台协作启动后的模型续写替换为受控确认语。"""

        updated = [dict(message) for message in messages]
        for index in range(len(updated) - 1, -1, -1):
            if updated[index].get("role") == "assistant":
                updated[index]["content"] = [{"type": "text", "text": text}]
                return updated
        updated.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
        return updated

    def _record(self, event_type: str, **kwargs) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.record(event_type, **kwargs)
        except Exception:
            pass
