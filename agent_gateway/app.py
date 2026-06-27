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
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.factory import build_state_repository
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
from agent_gateway.runtime.execution.roles import build_runtime_role_plan
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.infra.postgres_client import PostgresClient
from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore, TaskWorkerRuntime
from agent_gateway.runtime.tasks.handlers import AgentInboundTaskHandler
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

    settings: GatewaySettings  # 全局运行配置，来自环境变量和默认配置。
    agents: AgentManager  # Agent 配置注册表，负责保存可用智能体。
    bindings: BindingTable  # 消息路由绑定表，决定入站消息分配给哪个 Agent。
    sessions: SessionStore  # 会话持久化存储，负责读写多轮对话历史。
    tools: ToolRegistry  # 工具注册表，向模型暴露可调用工具及 schema。
    memory_store: MemoryStore  # 长期记忆存储，负责记忆写入、检索和召回。
    skills_manager: SkillsManager  # Skill 发现与注入管理器。
    prompt_assembler: PromptAssembler  # System prompt 组装器。
    runner: AgentLoopRunner  # Agent Loop 执行器，负责模型调用和工具闭环。
    profile_manager: ProfileManager  # 模型 Profile 管理器，负责主备模型和冷却状态。
    channel_manager: ChannelManager  # 多通道管理器，保存 CLI、飞书、Telegram 等通道实例。
    dispatcher: GatewayDispatcher  # 入站/后台任务调度器，连接路由、Agent Loop 和投递队列。
    autonomy_runtime: AutonomyRuntime  # 主动任务运行时，包含 Heartbeat 和 Cron。
    delivery_queue: DeliveryQueue  # 可靠投递磁盘队列。
    delivery_runtime: DeliveryRuntime  # 出站投递运行时，负责重试、失败归档和成功回调。
    command_queue: CommandQueue  # 命名并发车道队列，控制同类任务串行执行。
    control_plane: GatewayControlPlane  # 控制面服务，提供配置、状态和运维操作。
    redis_client: RedisClient  # Redis 基础设施客户端，用于后续去重、锁和限流。
    postgres_client: PostgresClient  # PostgreSQL 基础设施客户端，用于状态外置健康检查与接入。
    task_store: LocalTaskStore  # 后台任务本地状态存储。
    task_queue: LocalTaskQueue  # 后台任务队列抽象。
    task_worker: TaskWorkerRuntime  # 后台任务 worker 运行时。
    event_store: RuntimeEventStore  # 运行事件 JSONL 存储。
    metrics_store: MetricsStore  # 指标快照存储。
    metrics_runtime: MetricsRuntime  # 指标采集运行时。
    alert_store: AlertStore  # 告警事件存储。
    alerts_runtime: AlertsRuntime  # 告警规则运行时，负责检测和通知。


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
    redis_client = RedisClient(
        enabled=settings.redis_enabled,
        url=settings.redis_url,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
    )
    postgres_client = PostgresClient(
        enabled=settings.postgres_enabled,
        url=settings.postgres_url,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    task_store = LocalTaskStore(settings.tasks_dir)
    task_queue = LocalTaskQueue(task_store)
    state_bundle = build_state_repository(
        settings,
        sessions=sessions,
        tasks=task_store,
        events=event_store,
        metrics=metrics_store,
        alerts=alert_store,
        memory=memory_store,
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
    autonomy_runtime = AutonomyRuntime(
        settings,
        dispatcher,
        channel_manager,
        event_store=event_store,
        redis_client=redis_client,
        task_queue=task_queue,
    )
    task_worker = TaskWorkerRuntime(task_queue, worker_id="local-worker")
    task_worker.register_handler("cron", autonomy_runtime.cron.run_task_instance)
    task_worker.register_handler("heartbeat", autonomy_runtime.heartbeat.run_task_instance)
    task_worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(dispatcher, channel_manager, delivery_runtime=None),
    )
    delivery_runtime = DeliveryRuntime(
        delivery_queue,
        channel_manager,
        on_success=autonomy_runtime.cron.on_delivery_success,
        event_store=event_store,
    )
    agent_inbound_handler = task_worker.handlers.get("agent_inbound")
    if isinstance(agent_inbound_handler, AgentInboundTaskHandler):
        agent_inbound_handler.delivery_runtime = delivery_runtime
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
        redis_client=redis_client,
        postgres_client=postgres_client,
        state_repository=state_bundle.read,
        task_queue=task_queue,
        task_worker=task_worker,
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
        redis_client=redis_client,
        postgres_client=postgres_client,
        task_store=task_store,
        task_queue=task_queue,
        task_worker=task_worker,
        event_store=event_store,
        metrics_store=metrics_store,
        metrics_runtime=metrics_runtime,
        alert_store=alert_store,
        alerts_runtime=alerts_runtime,
    )


async def serve(app: GatewayApplication) -> None:
    """启动网关所有常驻服务，并在退出时统一回收。"""

    role_plan = build_runtime_role_plan(app.settings.runtime_roles)
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
        max_concurrent_lanes=app.settings.inbound_max_concurrent_lanes,
        max_queue_size=app.settings.inbound_max_queue_size,
        max_lane_queue_size=app.settings.inbound_max_lane_queue_size,
        long_task_notice_seconds=app.settings.inbound_long_task_notice_seconds,
        task_queue=app.task_queue,
        background_inbound_commands=app.settings.background_inbound_commands,
    )
    app.control_plane.channel_runtime = channel_runtime
    server = GatewayServer(
        host=app.settings.host,
        port=app.settings.port,
        dispatcher=app.dispatcher,
        sessions=app.sessions,
        autonomy=app.autonomy_runtime,
        control_plane=app.control_plane,
        state_repository=app.control_plane.state_repository,
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
        redis_client=app.redis_client,
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
        if app.settings.dashboard_enabled and role_plan.dashboard
        else None
    )
    started: list[object] = []
    try:
        if role_plan.control:
            await server.start()
            started.append(server)
        if role_plan.delivery:
            await app.delivery_runtime.start()
            started.append(app.delivery_runtime)
        if role_plan.inbound:
            await channel_runtime.start()
            started.append(channel_runtime)
        if role_plan.scheduler:
            await app.autonomy_runtime.start()
            started.append(app.autonomy_runtime)
        if role_plan.worker:
            await app.task_worker.start()
            started.append(app.task_worker)
        if role_plan.observability:
            await app.metrics_runtime.start()
            started.append(app.metrics_runtime)
            await app.alerts_runtime.start()
            started.append(app.alerts_runtime)
        if role_plan.inbound:
            await feishu_webhook.start()
            started.append(feishu_webhook)
            await feishu_long_connection.start()
            started.append(feishu_long_connection)
        if dashboard_server is not None:
            await dashboard_server.start()
            started.append(dashboard_server)
        print(f"Gateway runtime roles: {role_plan.role_label}")
        if role_plan.control:
            print(f"Gateway control running on ws://{app.settings.host}:{app.settings.port}")
        else:
            print("Gateway control disabled for this runtime role")
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
    if role_plan.inbound:
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
    else:
        print("Inbound channels disabled for this runtime role")
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
        for component in reversed(started):
            stop = getattr(component, "stop", None)
            if stop is not None:
                await stop()


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
