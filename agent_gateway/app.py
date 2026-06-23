from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.bootstrap import build_channel_manager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings, load_env
from agent_gateway.config_loader import (
    ensure_default_project_files,
    load_agents,
    load_auth_profiles,
    load_bindings,
    load_channel_accounts,
)
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.ai.context.prompt import PromptAssembler
from agent_gateway.ai.context.memory import MemoryStore, register_memory_tools
from agent_gateway.ai.context.skills import SkillsManager
from agent_gateway.monitoring.static_server import DashboardConfig, DashboardStaticServer
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.gateways.feishu.onboarding import (
    FeishuOnboardingService,
    FeishuOnboardingSessionStore,
)
from agent_gateway.runtime.domain.ids import normalize_agent_id
from agent_gateway.runtime.domain.models import ProactiveTarget
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.autonomy import AutonomyRuntime
from agent_gateway.runtime.execution.channel_runtime import ChannelRuntime
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.execution.loop import AgentLoopRunner
from agent_gateway.runtime.execution.metrics_runtime import MetricsRuntime
from agent_gateway.runtime.execution.alerts_runtime import AlertsRuntime
from agent_gateway.runtime.execution.resilience import ProfileManager, ResilienceRunner
from agent_gateway.gateways.feishu.http import FeishuWebhookServer
from agent_gateway.gateways.feishu.long_connection import FeishuLongConnectionRuntime
from agent_gateway.gateways.control.websocket_server import GatewayServer
from agent_gateway.runtime.state.store import SessionStore
from agent_gateway.ai.tools.builtin import register_builtin_tools
from agent_gateway.ai.tools.registry import ToolRegistry
from agent_gateway.ai.tools.web_search import register_web_search_tools


@dataclass(slots=True)
class GatewayApplication:
    """网关应用装配结果。

    把启动阶段构造出的主要运行对象集中收口，便于 `serve()`、CLI 命令和测试复用。
    """

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
    event_store: RuntimeEventStore
    metrics_store: MetricsStore
    metrics_runtime: MetricsRuntime
    alert_store: AlertStore
    alerts_runtime: AlertsRuntime


def build_dashboard_websocket_url(settings: GatewaySettings) -> str:
    """为本地 Dashboard 生成可连接的 WebSocket 地址。"""

    protocol = "ws"
    host = settings.host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{protocol}://{host}:{settings.port}"


def build_application(settings: GatewaySettings | None = None) -> GatewayApplication:
    """装配完整网关应用。"""

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
    event_store = RuntimeEventStore(
        settings.events_dir,
        retention_days=settings.events_retention_days,
    )
    metrics_store = MetricsStore(
        settings.metrics_dir,
        retention_days=settings.metrics_retention_days,
    )
    alert_store = AlertStore(
        settings.alerts_dir,
        retention_days=settings.alerts_retention_days,
    )
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        command_queue,
        delivery_queue,
        event_store=event_store,
    )
    channel_manager = build_channel_manager(settings, load_channel_accounts(settings))
    autonomy_runtime = AutonomyRuntime(settings, dispatcher, channel_manager, event_store=event_store)
    delivery_runtime = DeliveryRuntime(
        delivery_queue,
        channel_manager,
        on_success=autonomy_runtime.cron.on_delivery_success,
        event_store=event_store,
    )
    metrics_runtime = MetricsRuntime(
        metrics_store=metrics_store,
        delivery_queue=delivery_queue,
        command_queue=command_queue,
        profiles=profile_manager,
        autonomy=autonomy_runtime,
        event_store=event_store,
        interval_seconds=settings.metrics_interval_seconds,
    )
    alerts_runtime = AlertsRuntime(
        metrics_store=metrics_store,
        alert_store=alert_store,
        event_store=event_store,
        dispatcher=dispatcher,
        channels=channel_manager,
        target=(
            ProactiveTarget(
                channel=settings.alert_channel,
                account_id=settings.alert_account_id,
                peer_id=settings.alert_peer_id,
                agent_id=normalize_agent_id(settings.alert_agent_id),
            )
            if settings.alert_channel and settings.alert_account_id and settings.alert_peer_id
            else None
        ),
        interval_seconds=settings.alerts_interval_seconds,
    )
    control_plane = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profile_manager,
        channels=channel_manager,
        tools=tools,
        autonomy=autonomy_runtime,
        delivery_queue=delivery_queue,
        delivery_runtime=delivery_runtime,
        event_store=event_store,
        metrics_store=metrics_store,
        metrics_runtime=metrics_runtime,
        alert_store=alert_store,
        alerts_runtime=alerts_runtime,
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
        event_store=event_store,
        metrics_store=metrics_store,
        metrics_runtime=metrics_runtime,
        alert_store=alert_store,
        alerts_runtime=alerts_runtime,
    )


async def serve(app: GatewayApplication) -> None:
    """启动网关所有常驻服务，并在退出时统一回收。"""

    onboarding_store = FeishuOnboardingSessionStore(app.settings.data_dir / "onboarding" / "feishu")
    onboarding_service = FeishuOnboardingService(
        store=onboarding_store,
        control_plane=app.control_plane,
        dispatcher=app.dispatcher,
        public_base_url=(
            f"http://{app.settings.dashboard_host}:{app.settings.dashboard_port}"
            if app.settings.dashboard_enabled
            else ""
        ),
        bot_link=app.settings.feishu_onboarding_bot_link,
        auto_bind_first_message=app.settings.feishu_onboarding_auto_bind_first_message,
        auto_bind_bot_added=app.settings.feishu_onboarding_auto_bind_bot_added,
    )
    app.control_plane.feishu_onboarding = onboarding_service
    channel_runtime = ChannelRuntime(
        app.dispatcher,
        app.channel_manager,
        app.delivery_runtime,
        inbound_interceptors=[onboarding_service],
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
        event_store=app.event_store,
    )
    feishu_long_connection = FeishuLongConnectionRuntime(
        channels=app.channel_manager,
        channel_runtime=channel_runtime,
        event_interceptors=[onboarding_service],
    )
    app.control_plane.feishu_long_connection_runtime = feishu_long_connection
    dashboard_server = (
        DashboardStaticServer(
            host=app.settings.dashboard_host,
            port=app.settings.dashboard_port,
            config=DashboardConfig(
                websocket_url=build_dashboard_websocket_url(app.settings),
                refresh_interval_seconds=app.settings.dashboard_refresh_interval_seconds,
            ),
            onboarding=onboarding_service,
        )
        if app.settings.dashboard_enabled
        else None
    )
    started: list[object] = []
    try:
        await server.start()
        started.append(server)
        await app.delivery_runtime.start()
        started.append(app.delivery_runtime)
        await channel_runtime.start()
        started.append(channel_runtime)
        await app.autonomy_runtime.start()
        started.append(app.autonomy_runtime)
        await app.metrics_runtime.start()
        started.append(app.metrics_runtime)
        await app.alerts_runtime.start()
        started.append(app.alerts_runtime)
        await feishu_webhook.start()
        started.append(feishu_webhook)
        await feishu_long_connection.start()
        started.append(feishu_long_connection)
        if dashboard_server is not None:
            await dashboard_server.start()
            started.append(dashboard_server)
        print(f"Gateway running on ws://{app.settings.host}:{app.settings.port}")
        if dashboard_server is not None:
            print(
                "Dashboard running on "
                f"http://{app.settings.dashboard_host}:{app.settings.dashboard_port}"
            )
        else:
            print("Dashboard disabled")
    except Exception:
        for component in reversed(started):
            stop = getattr(component, "stop", None)
            if stop is not None:
                await stop()
        raise
    webhook_paths = feishu_webhook.list_webhook_paths()
    if webhook_paths:
        for account_id, path in webhook_paths:
            print(
                "Feishu webhook on "
                f"http://{app.settings.feishu_webhook_host}:{app.settings.feishu_webhook_port}"
                f"{path} account={account_id}"
            )
    else:
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
        if dashboard_server is not None:
            await dashboard_server.stop()
        await feishu_long_connection.stop()
        await app.metrics_runtime.stop()
        await app.alerts_runtime.stop()
        await app.autonomy_runtime.stop()
        await channel_runtime.stop()
        await app.delivery_runtime.stop()
        await feishu_webhook.stop()
        await server.stop()


async def trigger_cron_once(
    app: GatewayApplication,
    job_id: str,
    *,
    flush_delivery: bool = True,
    flush_rounds: int = 3,
) -> dict[str, object]:
    """手动触发一次 Cron 任务，并按需执行若干轮投递刷新。"""

    result = await app.autonomy_runtime.cron.trigger_job(job_id)
    pending_before_flush = app.delivery_runtime.pending_count()
    if flush_delivery:
        for _ in range(max(1, flush_rounds)):
            if app.delivery_runtime.pending_count() <= 0:
                break
            await app.delivery_runtime.flush_once()
            await asyncio.sleep(0)
        if app.delivery_runtime.pending_count() > 0:
            await asyncio.sleep(0.25)
            await app.delivery_runtime.flush_once()
    pending_entries = app.delivery_queue.pending_entries()
    return {
        "job_id": job_id,
        "result": result,
        "pending_before_flush": pending_before_flush,
        "pending_after_flush": len(pending_entries),
        "pending_ids": [entry.id for entry in pending_entries],
        "pending_errors": {
            entry.id: entry.last_error for entry in pending_entries if entry.last_error
        },
    }


async def trigger_cron_once_with_timeout(
    app: GatewayApplication,
    job_id: str,
    *,
    flush_delivery: bool = True,
    flush_rounds: int = 3,
    timeout_seconds: float = 180.0,
) -> dict[str, object]:
    """在超时保护下触发一次 Cron 任务。"""

    try:
        return await asyncio.wait_for(
            trigger_cron_once(
                app,
                job_id,
                flush_delivery=flush_delivery,
                flush_rounds=flush_rounds,
            ),
            timeout=max(0.001, timeout_seconds),
        )
    except asyncio.TimeoutError:
        return {
            "job_id": job_id,
            "result": "timeout",
            "timeout_seconds": timeout_seconds,
            "pending_after_timeout": app.delivery_runtime.pending_count(),
        }


def main() -> None:
    """CLI 入口。"""

    parser = argparse.ArgumentParser(description="Run the modular agent gateway.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run the gateway service.")

    cron_trigger = subparsers.add_parser(
        "cron-trigger",
        help="Trigger one cron job immediately, then flush delivery queue by default.",
    )
    cron_trigger.add_argument("job_id")
    cron_trigger.add_argument(
        "--no-flush",
        action="store_true",
        help="Only enqueue the cron output; do not flush the delivery queue.",
    )
    cron_trigger.add_argument(
        "--flush-rounds",
        type=int,
        default=3,
        help="Maximum delivery flush rounds after triggering the job.",
    )
    cron_trigger.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for the cron job and delivery flush.",
    )

    parser.add_argument("--env-file", default="")
    args = parser.parse_args()

    load_env(Path(args.env_file) if args.env_file else None)
    app = build_application()
    if args.command in {None, "serve"}:
        asyncio.run(serve(app))
        return
    if args.command == "cron-trigger":
        result = asyncio.run(
            trigger_cron_once_with_timeout(
                app,
                args.job_id,
                flush_delivery=not args.no_flush,
                flush_rounds=args.flush_rounds,
                timeout_seconds=args.timeout_seconds,
            )
        )
        print(result)
        return
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
