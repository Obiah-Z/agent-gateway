from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_gateway.agents import AgentManager
from agent_gateway.channels.bootstrap import build_channel_manager
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings, load_env
from agent_gateway.config_loader import (
    ensure_default_project_files,
    load_agents,
    load_auth_profiles,
    load_bindings,
    load_channel_accounts,
)
from agent_gateway.delivery.queue import DeliveryQueue
from agent_gateway.intelligence.bootstrap import PromptAssembler
from agent_gateway.intelligence.memory import MemoryStore, register_memory_tools
from agent_gateway.intelligence.skills import SkillsManager
from agent_gateway.router import BindingTable, normalize_agent_id
from agent_gateway.runtime.autonomy import AutonomyRuntime
from agent_gateway.runtime.channel_runtime import ChannelRuntime
from agent_gateway.runtime.control_plane import GatewayControlPlane
from agent_gateway.runtime.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.dispatcher import GatewayDispatcher
from agent_gateway.runtime.feishu_http import FeishuWebhookServer
from agent_gateway.runtime.gateway_server import GatewayServer
from agent_gateway.runtime.lanes import CommandQueue
from agent_gateway.runtime.loop import AgentLoopRunner
from agent_gateway.runtime.resilience import ProfileManager, ResilienceRunner
from agent_gateway.sessions.store import SessionStore
from agent_gateway.tools.builtin import register_builtin_tools
from agent_gateway.tools.registry import ToolRegistry
from agent_gateway.tools.web_search import register_web_search_tools


@dataclass(slots=True)
class GatewayApplication:
    settings: GatewaySettings
    agents: AgentManager
    bindings: BindingTable
    sessions: SessionStore
    tools: ToolRegistry
    memory_store: MemoryStore
    skills_manager: SkillsManager
    prompt_assembler: PromptAssembler
    runner: AgentLoopRunner
    profile_manager: ProfileManager
    channel_manager: ChannelManager
    dispatcher: GatewayDispatcher
    autonomy_runtime: AutonomyRuntime
    delivery_queue: DeliveryQueue
    delivery_runtime: DeliveryRuntime
    command_queue: CommandQueue
    control_plane: GatewayControlPlane


def build_application(settings: GatewaySettings | None = None) -> GatewayApplication:
    settings = settings or GatewaySettings.from_env()
    settings.ensure_directories()
    ensure_default_project_files(settings)

    agents = AgentManager()
    for config in load_agents(settings):
        agents.register(config)
    if not agents.list():
        raise RuntimeError("No agents loaded from config")

    bindings = BindingTable()
    for binding in load_bindings(settings):
        binding.agent_id = normalize_agent_id(binding.agent_id)
        bindings.add(binding)

    sessions = SessionStore(settings.sessions_dir)
    tools = ToolRegistry()
    memory_store = MemoryStore(settings.workspace_root)
    skills_manager = SkillsManager(settings.workspace_root)
    skills_manager.discover()
    register_builtin_tools(
        tools,
        settings.workspace_root,
        max_output_chars=settings.max_tool_output_chars,
        default_timeout=settings.tool_timeout_seconds,
    )
    register_memory_tools(tools, memory_store)
    register_web_search_tools(tools, settings)
    prompt_assembler = PromptAssembler(
        settings.workspace_root,
        memory_store=memory_store,
        skills_manager=skills_manager,
    )
    profile_manager = ProfileManager(load_auth_profiles(settings))
    resilience_runner = ResilienceRunner(settings, profile_manager, tools)
    runner = AgentLoopRunner(
        settings,
        agents,
        sessions,
        prompt_assembler,
        resilience_runner,
    )
    command_queue = CommandQueue()
    delivery_queue = DeliveryQueue(settings.delivery_queue_dir)
    dispatcher = GatewayDispatcher(agents, bindings, runner, command_queue, delivery_queue)
    channel_manager = build_channel_manager(settings, load_channel_accounts(settings))
    autonomy_runtime = AutonomyRuntime(settings, dispatcher, channel_manager)
    delivery_runtime = DeliveryRuntime(delivery_queue, channel_manager)
    control_plane = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profile_manager,
        channels=channel_manager,
        tools=tools,
        autonomy=autonomy_runtime,
    )

    return GatewayApplication(
        settings=settings,
        agents=agents,
        bindings=bindings,
        sessions=sessions,
        tools=tools,
        memory_store=memory_store,
        skills_manager=skills_manager,
        prompt_assembler=prompt_assembler,
        runner=runner,
        profile_manager=profile_manager,
        channel_manager=channel_manager,
        dispatcher=dispatcher,
        autonomy_runtime=autonomy_runtime,
        delivery_queue=delivery_queue,
        delivery_runtime=delivery_runtime,
        command_queue=command_queue,
        control_plane=control_plane,
    )


async def serve(app: GatewayApplication) -> None:
    channel_runtime = ChannelRuntime(
        app.dispatcher,
        app.channel_manager,
        app.delivery_runtime,
    )
    app.control_plane.channel_runtime = channel_runtime
    server = GatewayServer(
        host=app.settings.host,
        port=app.settings.port,
        dispatcher=app.dispatcher,
        sessions=app.sessions,
        autonomy=app.autonomy_runtime,
        control_plane=app.control_plane,
    )
    feishu_webhook = FeishuWebhookServer(
        host=app.settings.feishu_webhook_host,
        port=app.settings.feishu_webhook_port,
        path=app.settings.feishu_webhook_path,
        channels=app.channel_manager,
        channel_runtime=channel_runtime,
        state_dir=app.settings.feishu_webhook_dir,
        signature_window_seconds=app.settings.feishu_signature_window_seconds,
        dedup_ttl_seconds=app.settings.feishu_event_dedup_ttl_seconds,
    )
    await server.start()
    await app.delivery_runtime.start()
    await channel_runtime.start()
    await app.autonomy_runtime.start()
    await feishu_webhook.start()
    print(f"Gateway running on ws://{app.settings.host}:{app.settings.port}")
    print(
        "Feishu webhook on "
        f"http://{app.settings.feishu_webhook_host}:{app.settings.feishu_webhook_port}"
        f"{app.settings.feishu_webhook_path}"
    )
    print(f"Loaded channels: {', '.join(app.channel_manager.list_channels()) or '(none)'}")
    print(
        "Loaded tools: "
        f"{', '.join(app.tools.names()) or '(none)'} "
        f"(web_search={'on' if app.settings.web_search_enabled else 'off'}, "
        f"provider={app.settings.web_search_provider})"
    )
    try:
        await asyncio.Event().wait()
    finally:
        await app.autonomy_runtime.stop()
        await channel_runtime.stop()
        await app.delivery_runtime.stop()
        await feishu_webhook.stop()
        await server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the modular agent gateway.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--env-file", default="")
    args = parser.parse_args()

    load_env(Path(args.env_file) if args.env_file else None)
    app = build_application()
    asyncio.run(serve(app))


if __name__ == "__main__":
    main()
