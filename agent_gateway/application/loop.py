"""Agent 单轮执行闭环。

本模块负责把会话历史、workspace prompt、记忆和工具策略组合成一次模型调用，并将模型
返回后的完整消息历史写回 SessionStore。模型容灾、工具调用循环等细节由 ResilienceRunner
承接。
"""

from __future__ import annotations

import asyncio

from agent_gateway.core.agents import AgentManager
from agent_gateway.config import GatewaySettings
from agent_gateway.intelligence.bootstrap import PromptAssembler
from agent_gateway.core.models import AgentReply
from agent_gateway.application.resilience import ResilienceRunner
from agent_gateway.sessions.context import ContextGuard
from agent_gateway.sessions.store import SessionStore


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
    ) -> AgentReply:
        """用户对话入口，默认使用完整 prompt 和记忆上下文。"""

        return await self.run_task_turn(
            agent_id=agent_id,
            session_key=session_key,
            user_text=user_text,
            channel=channel,
            mode="full",
        )

    async def run_task_turn(
        self,
        *,
        agent_id: str,
        session_key: str,
        user_text: str,
        channel: str,
        mode: str,
    ) -> AgentReply:
        """执行一次 Agent 轮次。

        流程：加载会话历史 -> 追加本轮用户输入 -> 组装系统 prompt -> 调用模型执行器 ->
        用模型返回的完整 messages 重写 JSONL 会话。
        """

        agent = self.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"agent '{agent_id}' not found")

        messages = self.sessions.load_messages(agent_id, session_key)
        messages.append({"role": "user", "content": user_text})
        allowed_tools = agent.allowed_tool_names(self.resilience_runner.tools.names())

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
            },
        )

        result = await asyncio.to_thread(
            self.resilience_runner.run,
            system_prompt,
            messages,
            model=agent.effective_model(self.settings.model_id),
            allowed_tools=allowed_tools,
        )
        # ResilienceRunner 返回的是经过工具调用闭环后的完整历史，用它覆盖会话文件。
        self.sessions.rewrite_messages(agent_id, session_key, result.messages)

        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=result.text,
            stop_reason=result.stop_reason,
            tool_calls=result.tool_calls,
        )
