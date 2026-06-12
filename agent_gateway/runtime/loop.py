from __future__ import annotations

import asyncio

from agent_gateway.agents import AgentManager
from agent_gateway.config import GatewaySettings
from agent_gateway.intelligence.bootstrap import PromptAssembler
from agent_gateway.models import AgentReply
from agent_gateway.runtime.resilience import ResilienceRunner
from agent_gateway.sessions.context import ContextGuard
from agent_gateway.sessions.store import SessionStore


class AgentLoopRunner:
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
        agent = self.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"agent '{agent_id}' not found")

        messages = self.sessions.load_messages(agent_id, session_key)
        messages.append({"role": "user", "content": user_text})
        allowed_tools = agent.allowed_tool_names(self.resilience_runner.tools.names())

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
        self.sessions.rewrite_messages(agent_id, session_key, result.messages)

        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=result.text,
            stop_reason=result.stop_reason,
            tool_calls=result.tool_calls,
        )
