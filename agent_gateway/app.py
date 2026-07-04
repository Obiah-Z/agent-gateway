from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, replace
import json
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings, load_env
from agent_gateway.config_loader import (
    ensure_default_project_files,
)
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.ai.context.prompt import PromptAssembler
from agent_gateway.ai.context.memory import MemoryStore, register_memory_tools
from agent_gateway.ai.context.skills import SkillsManager
from agent_gateway.monitoring.static_server import DashboardConfig, DashboardStaticServer
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.alerts import AlertRule, AlertState, AlertStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.factory import StateRepositoryBundle, build_state_repository
from agent_gateway.gateways.feishu.onboarding import (
    FeishuOnboardingService,
    FeishuOnboardingSessionStore,
)
from agent_gateway.gateways.feishu.state import FeishuCardState, FeishuCardStateStore
from agent_gateway.ai.news.models import NewsItem, NewsSourceConfig
from agent_gateway.ai.news.store import NewsDigestStore
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
from agent_gateway.runtime.diagnostics import render_doctor_text, run_doctor
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager, ResilienceRunner
from agent_gateway.runtime.execution.roles import build_runtime_role_plan
from agent_gateway.runtime.infra.rabbitmq import RabbitMQDeliveryBroker, RabbitMQInboundTaskBroker
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.infra.postgres_client import PostgresClient
from agent_gateway.runtime.tasks import (
    LocalTaskQueue,
    LocalTaskStore,
    RedisSessionReadyScheduler,
    TaskWorkerRuntime,
)
from agent_gateway.runtime.tasks.handlers import AgentInboundTaskHandler
from agent_gateway.gateways.feishu.http import FeishuWebhookServer
from agent_gateway.gateways.feishu.long_connection import FeishuLongConnectionRuntime
from agent_gateway.gateways.control.websocket_server import GatewayServer
from agent_gateway.runtime.state.postgres import (
    PostgresReadRepository,
    PostgresWriteRepository,
    build_postgres_schema_sql,
    check_postgres_schema,
    initialize_postgres_schema,
)
from agent_gateway.runtime.state.migration import backfill_local_state_to_repository
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
    state_repository: StateRepositoryBundle  # 统一状态仓储装配结果，包含读仓储、写仓储和本地备份。
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


def build_lane_doctor_report(app: GatewayApplication, *, limit: int = 20) -> dict[str, object]:
    """构建分布式 lane 只读诊断报告。"""

    return app.control_plane.lane_doctor(limit=limit)


def render_lane_doctor_text(report: dict[str, object]) -> str:
    """把 lane-doctor 诊断报告渲染为中文摘要。"""

    summary = report.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    lines = [
        f"分布式 Lane 诊断：{report.get('status', 'unknown')}",
        (
            "摘要："
            f"ready={summary.get('ready', False)} "
            f"owned={summary.get('owned_lanes', 0)} "
            f"stale={summary.get('stale_lanes', 0)} "
            f"recovery_actions={summary.get('recovery_actions', 0)} "
            f"broker_messages={summary.get('broker_messages', 0)} "
            f"broker_dlq={summary.get('broker_dead_letters', 0)}"
        ),
        "",
        "检查项：",
    ]
    for row in list(report.get("checks", []) or []):
        if not isinstance(row, dict):
            continue
        details = ", ".join(
            f"{key}={value}"
            for key, value in row.items()
            if key not in {"name", "status"}
        )
        lines.append(f"- {row.get('status', 'unknown').upper()} {row.get('name', '--')} {details}".rstrip())
    readiness = report.get("readiness", {})
    readiness = readiness if isinstance(readiness, dict) else {}
    readiness_checks = list(readiness.get("checks", []) or [])
    if readiness_checks:
        lines.extend(
            [
                "",
                (
                    "最终形态就绪："
                    f"{readiness.get('status', 'unknown')} "
                    f"passed={readiness.get('passed', 0)} "
                    f"failed={readiness.get('failed', 0)}"
                ),
            ]
        )
        for row in readiness_checks:
            if not isinstance(row, dict):
                continue
            if row.get("ok"):
                continue
            lines.append(f"- FAIL {row.get('name', '--')} {row.get('message', '')}".rstrip())
    recovery_plan = report.get("recovery_plan", {})
    recovery_plan = recovery_plan if isinstance(recovery_plan, dict) else {}
    if int(recovery_plan.get("action_count", 0) or 0) > 0:
        lines.extend(
            [
                "",
                "恢复建议：",
                "- 存在 stale lane 可执行动作，请先查看 tasks.lanes.recovery.plan。",
                "- 确认 worker 已退出后，再显式调用 tasks.lanes.recovery.execute execute=true。",
            ]
        )
    return "\n".join(lines)


def build_application(settings: GatewaySettings | None = None) -> GatewayApplication:
    """装配完整网关应用。"""

    settings = settings or GatewaySettings.from_env()
    settings.ensure_directories()
    ensure_default_project_files(settings)

    sessions = SessionStore(settings.sessions_dir)
    memory_store = MemoryStore(settings.workspace_root)
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
    state_bundle = build_state_repository(
        settings,
        sessions=sessions,
        tasks=task_store,
        events=event_store,
        metrics=metrics_store,
        alerts=alert_store,
        memory=memory_store,
    )
    sessions.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    task_store.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    event_store.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    memory_store.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    metrics_store.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    alert_store.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None  # type: ignore[assignment]
    sessions.backup_sink = state_bundle.backup
    task_store.backup_sink = state_bundle.backup
    event_store.backup_sink = state_bundle.backup
    memory_store.backup_sink = state_bundle.backup
    primary_write = state_bundle.write if state_bundle.write is not None and state_bundle.write.enabled else None
    sessions.write_backend = primary_write
    task_store.write_backend = primary_write
    event_store.write_backend = primary_write
    memory_store.write_backend = primary_write
    metrics_store.write_backend = primary_write
    alert_store.write_backend = primary_write

    agents = AgentManager()
    bindings = BindingTable()
    profile_manager = ProfileManager(
        [
            AuthProfile(
                name="bootstrap",
                provider="anthropic",
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
            )
        ]
    )
    channel_manager = ChannelManager()
    config_loader = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=profile_manager,
        channels=channel_manager,
        state_repository=state_bundle.read,
        state_write_repository=state_bundle.config_write,
    )
    config_loader.reload_agents()
    config_loader.reload_bindings()
    config_loader.reload_profiles()
    asyncio.run(config_loader.reload_channels())

    tools = ToolRegistry()
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
    delivery_queue.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None
    delivery_queue.write_backend = primary_write
    if settings.delivery_broker == "rabbitmq":
        delivery_queue.broker = RabbitMQDeliveryBroker(
            url=settings.rabbitmq_url,
            exchange=settings.rabbitmq_exchange,
            queue=settings.rabbitmq_queue,
            dead_letter_exchange=settings.rabbitmq_dead_letter_exchange,
            dead_letter_queue=settings.rabbitmq_dead_letter_queue,
            connect_timeout_seconds=settings.rabbitmq_connect_timeout_seconds,
            enabled=True,
        )
    task_broker = None
    if settings.inbound_broker == "rabbitmq":
        task_broker = RabbitMQInboundTaskBroker(
            url=settings.inbound_rabbitmq_url,
            exchange=settings.inbound_rabbitmq_exchange,
            queue_prefix=settings.inbound_rabbitmq_queue_prefix,
            dead_letter_exchange=settings.inbound_rabbitmq_dead_letter_exchange,
            dead_letter_queue=settings.inbound_rabbitmq_dead_letter_queue,
            partitions=settings.inbound_rabbitmq_partitions,
            prefetch=settings.inbound_rabbitmq_prefetch,
            connect_timeout_seconds=settings.inbound_rabbitmq_connect_timeout_seconds,
            enabled=True,
        )
    session_scheduler = None
    if settings.session_ready_scheduler_enabled and redis_client.enabled:
        session_scheduler = RedisSessionReadyScheduler(
            redis_client,
            namespace=settings.session_ready_scheduler_namespace,
            default_ttl_seconds=settings.inbound_session_lock_ttl_seconds,
        )
    task_queue = LocalTaskQueue(
        task_store,
        broker=task_broker,
        session_scheduler=session_scheduler,
    )
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        command_queue,
        delivery_queue,
        event_store=event_store,
    )
    autonomy_runtime = AutonomyRuntime(
        settings,
        dispatcher,
        channel_manager,
        event_store=event_store,
        redis_client=redis_client,
        task_queue=task_queue,
        state_read_repository=state_bundle.read,
        state_write_repository=primary_write,
    )
    task_worker = TaskWorkerRuntime(
        task_queue,
        worker_id=settings.task_worker_id,
        concurrency=settings.task_worker_concurrency,
        event_store=event_store,
    )
    task_worker.register_handler("cron", autonomy_runtime.cron.run_task_instance)
    task_worker.register_handler("heartbeat", autonomy_runtime.heartbeat.run_task_instance)
    task_worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(
            dispatcher,
            channel_manager,
            delivery_runtime=None,
            redis_client=redis_client,
            lock_ttl_seconds=settings.inbound_session_lock_ttl_seconds,
            lock_renew_interval_seconds=(
                settings.inbound_session_lock_renew_interval_seconds or None
            ),
            worker_id=task_worker.worker_id,
            state_repository=primary_write,
            use_lane_lock=session_scheduler is None,
            feishu_progress_notice_enabled=settings.feishu_progress_notice_enabled,
            feishu_progress_notice_text=settings.feishu_progress_notice_text,
        ),
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
        task_worker=task_worker,
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
        state_write_repository=state_bundle.config_write,
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
        state_repository=state_bundle,
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
    onboarding_store = FeishuOnboardingSessionStore(
        app.settings.data_dir / "onboarding" / "feishu",
        read_backend=app.state_repository.read,
        write_backend=app.state_repository.write,
    )
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
        inbound_task_queue_enabled=app.settings.inbound_task_queue_enabled,
        background_inbound_commands=app.settings.background_inbound_commands,
        feishu_progress_notice_enabled=app.settings.feishu_progress_notice_enabled,
        feishu_progress_notice_text=app.settings.feishu_progress_notice_text,
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
        state_write_repository=app.state_repository.write,
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
            control_plane=app.control_plane,
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


def run_postgres_smoke(settings: GatewaySettings) -> dict[str, object]:
    """执行 PostgreSQL 主存储最小端到端验证。

    该验证只触发状态层写入，不调用模型、不发送外部消息。成功标准是核心运行状态
    能从 PostgreSQL 读回，同时本地 fallback 文件也已经生成。
    """

    with TemporaryDirectory(prefix="agent-gateway-pg-smoke-") as temp_dir:
        root = Path(temp_dir)
        smoke_settings = replace(
            settings,
            postgres_enabled=True,
            data_dir=root / "data",
            config_dir=root / "config",
            workspace_root=root / "workspace",
        )
        return _run_postgres_smoke_with_settings(smoke_settings)


def _run_postgres_smoke_with_settings(settings: GatewaySettings) -> dict[str, object]:
    """在隔离 settings 下执行 PostgreSQL smoke 主逻辑。"""

    settings.ensure_directories()
    initialize_postgres_schema(
        url=settings.postgres_url,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    app = build_application(settings)
    marker = f"pg-smoke-{uuid.uuid4().hex[:10]}"
    session_key = f"smoke:{marker}"
    now = time.time()
    updated_at = now

    if app.state_repository.write is None:
        raise RuntimeError("PostgreSQL write repository is not configured for smoke test")

    app.state_repository.write.upsert(
        "agents",
        {
            "id": f"{marker}-agent",
            "name": f"PostgreSQL Smoke {marker}",
            "personality": "smoke",
            "model": "",
            "dm_scope": "all",
            "extra_system": "",
            "tool_policy": {},
            "memory_policy": {},
            "prompt_policy": {},
            "updated_at": updated_at,
        },
    )
    binding_key = f"{marker}-agent\x1fchannel\x1fpostgres-smoke"
    app.state_repository.write.upsert(
        "bindings",
        {
            "key": binding_key,
            "agent_id": f"{marker}-agent",
            "tier": 1,
            "match_key": "channel",
            "match_value": "postgres-smoke",
            "priority": 100,
            "updated_at": updated_at,
        },
    )
    app.state_repository.write.upsert(
        "profiles",
        {
            "name": f"{marker}-profile",
            "provider": "anthropic",
            "api_key": "",
            "api_key_env": "ANTHROPIC_API_KEY",
            "base_url": "",
            "base_url_env": "ANTHROPIC_BASE_URL",
            "updated_at": updated_at,
        },
    )
    channel_key = f"postgres-smoke\x1f{marker}"
    app.state_repository.write.upsert(
        "channels",
        {
            "key": channel_key,
            "channel": "postgres-smoke",
            "account_id": marker,
            "enabled": False,
            "label": "PostgreSQL smoke",
            "token": "",
            "token_env": "",
            "config": {"marker": marker},
            "updated_at": updated_at,
        },
    )

    app.sessions.rewrite_messages(
        "main",
        session_key,
        [{"role": "user", "content": f"{marker}: session"}],
    )
    task = app.task_queue.enqueue(
        task_type="postgres_smoke",
        source="postgres-smoke",
        agent_id="main",
        session_key=session_key,
        priority=1,
        idempotency_key=marker,
        payload={"marker": marker},
    )
    reserved = app.task_queue.reserve(
        worker_id="postgres-smoke",
        task_types=["postgres_smoke"],
        now=now,
    )
    if reserved is not None:
        app.task_queue.ack(reserved.id, result_preview=marker, now=now + 1)
    event = app.event_store.record(
        "postgres.smoke",
        status="ok",
        component="postgres_smoke",
        message=marker,
        correlation_id=marker,
        agent_id="main",
        session_key=session_key,
        metadata={"marker": marker},
    )
    app.memory_store.write_memory(f"{marker}: memory", category="postgres_smoke")
    app.metrics_store.record(
        runtime={"marker": marker},
        metadata={"marker": marker},
        timestamp=now,
    )
    alert_rule = AlertRule(
        id=f"{marker}-rule",
        title="PostgreSQL smoke",
        severity="info",
        description="PostgreSQL smoke verification",
        threshold=1.0,
    )
    alert_state = AlertState(rule_id=alert_rule.id, status="inactive", metadata={"marker": marker})
    app.alert_store.append(
        rule=alert_rule,
        state=alert_state,
        event="postgres_smoke",
        message=marker,
        value=0.0,
        metadata={"marker": marker},
        timestamp=now,
    )
    alert_id = f"alert_{int(now * 1000)}"
    delivery_id = app.delivery_queue.enqueue(
        "cli",
        "postgres-smoke",
        marker,
        {"kind": "postgres_smoke", "marker": marker},
    )
    telegram_account_id = "postgres-smoke"
    telegram_offset = int(now)
    app.state_repository.write.write_channel_offset("telegram", telegram_account_id, telegram_offset)
    telegram_offset_path = settings.data_dir / "channel-state" / "telegram" / f"offset-{telegram_account_id}.txt"
    telegram_offset_path.parent.mkdir(parents=True, exist_ok=True)
    telegram_offset_path.write_text(str(telegram_offset), encoding="utf-8")

    cron_run_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    app.autonomy_runtime.cron._write_run_log(  # type: ignore[attr-defined]
        {
            "job_id": "postgres-smoke",
            "config_id": "postgres-smoke",
            "agent_id": "main",
            "scope": "system",
            "run_at": cron_run_at,
            "status": "ok",
            "output_preview": marker,
            "metadata": {"marker": marker},
        }
    )

    news_source = NewsSourceConfig(
        id="postgres-smoke",
        type="postgres_smoke",
        tags=("postgres_smoke",),
    )
    news_item = NewsItem.build(
        source=news_source,
        title=f"{marker}: news",
        url=f"https://example.com/{marker}",
        published_at=cron_run_at,
        summary=marker,
        metadata={"marker": marker},
    )
    news_store = NewsDigestStore(
        settings.data_dir / "postgres-smoke-news",
        read_backend=app.state_repository.read,
        write_backend=app.state_repository.write,
    )
    news_store.append_collected([news_item])
    news_store.mark_seen([news_item])

    card_id = f"card-{marker}"
    card_store = FeishuCardStateStore(
        settings.data_dir / "channel-state" / "feishu" / "postgres-smoke",
        read_backend=app.state_repository.read,
        write_backend=app.state_repository.write,
    )
    card_store.save(
        FeishuCardState(
            card_id=card_id,
            owner_channel="feishu",
            owner_account_id="postgres-smoke",
            peer_id="postgres-smoke-peer",
            message_id=f"om_{marker}",
            title=marker,
            summary=marker,
            template="blue",
            card_link="",
            blocks=[marker],
            structured_blocks=[],
            actions=[],
            page_size=1,
            page_index=0,
            expanded=False,
        )
    )

    reader = PostgresReadRepository(
        url=settings.postgres_url,
        enabled=True,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    checks = {
        "agent_config": bool(reader.get("agents", f"{marker}-agent")),
        "binding_config": bool(reader.get("bindings", binding_key)),
        "profile_config": bool(reader.get("profiles", f"{marker}-profile")),
        "channel_config": bool(reader.get("channels", channel_key)),
        "session": bool(reader.read_session_messages("main", session_key)),
        "task": bool(reader.get("tasks", task.id)),
        "event": bool(reader.get("runtime_events", str(event.get("event_id", "")))),
        "memory": _postgres_smoke_has_marker(reader, "memory_entries", marker, "metadata"),
        "metric": _postgres_smoke_has_marker(reader, "metrics", marker, "metadata"),
        "alert": bool(reader.get("errors", alert_id)),
        "delivery": bool(reader.get("delivery_entries", delivery_id)),
        "telegram_offset": bool(reader.get("channel_offsets", f"telegram\x1f{telegram_account_id}")),
        "cron_run": bool(reader.get("cron_runs", f"postgres-smoke:{cron_run_at}")),
        "news_items": _postgres_smoke_has_marker(reader, "news_items", marker, "metadata"),
        "feishu_card_state": bool(reader.get("feishu_card_states", card_id)),
    }
    local_checks = {
        "session_file": app.sessions.session_path("main", session_key).exists(),
        "task_file": (settings.tasks_dir / f"{task.id}.json").exists(),
        "event_file": any(settings.events_dir.glob("runtime-events-*.jsonl")),
        "memory_file": (settings.workspace_root / "memory" / "daily" / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl").exists(),
        "metric_file": any(settings.metrics_dir.glob("metrics-*.jsonl")),
        "alert_file": any(settings.alerts_dir.glob("alerts-*.jsonl")),
        "delivery_file": (settings.delivery_queue_dir / f"{delivery_id}.json").exists(),
        "telegram_offset_file": telegram_offset_path.exists(),
        "cron_run_file": (settings.workspace_root / "cron" / "cron-runs.jsonl").exists(),
        "news_seen_file": (settings.data_dir / "postgres-smoke-news" / "seen-items.jsonl").exists(),
        "news_collected_file": (settings.data_dir / "postgres-smoke-news" / "collected-items.jsonl").exists(),
        "feishu_card_state_file": (
            settings.data_dir / "channel-state" / "feishu" / "postgres-smoke" / "cards" / f"{card_id}.json"
        ).exists(),
    }
    ok = all(checks.values()) and all(local_checks.values())
    app.delivery_queue.discard(delivery_id)
    write_backend = app.task_store.write_backend
    if write_backend is not None and hasattr(write_backend, "delete"):
        try:
            write_backend.delete("tasks", task.id)
        except Exception:
            pass
    return {
        "result": "ok" if ok else "failed",
        "marker": marker,
        "postgres_checks": checks,
        "local_fallback_checks": local_checks,
    }


def _postgres_smoke_has_marker(
    reader: PostgresReadRepository,
    table: str,
    marker: str,
    metadata_key: str,
) -> bool:
    """检查最近若干行是否包含 smoke marker。"""

    try:
        rows = reader.list(table, limit=50)
    except Exception:
        return False
    for row in rows:
        metadata = row.get(metadata_key, {})
        if isinstance(metadata, dict) and metadata.get("marker") == marker:
            return True
        if marker in str(row):
            return True
    return False


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
    postgres_init = subparsers.add_parser(
        "postgres-init",
        help="Initialize PostgreSQL tables used by the gateway.",
    )
    postgres_init.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the generated schema SQL instead of executing it.",
    )
    subparsers.add_parser(
        "postgres-check-schema",
        help="Check PostgreSQL tables against the gateway schema specification.",
    )
    postgres_migrate = subparsers.add_parser(
        "postgres-migrate-local",
        help="Backfill local JSON/JSONL state files into PostgreSQL.",
    )
    postgres_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan local files and print counts without writing to PostgreSQL.",
    )
    subparsers.add_parser(
        "postgres-smoke",
        help="Verify PostgreSQL primary storage and local fallback writes.",
    )
    doctor = subparsers.add_parser(
        "doctor",
        help="Run startup diagnostics without serving traffic.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    delivery_republish = subparsers.add_parser(
        "delivery-republish",
        help="Republish pending/retrying delivery references to the configured broker.",
    )
    delivery_republish.add_argument(
        "--no-pending",
        action="store_true",
        help="Do not republish pending delivery records.",
    )
    delivery_republish.add_argument(
        "--no-retrying",
        action="store_true",
        help="Do not republish due retrying delivery records.",
    )
    lane_doctor = subparsers.add_parser(
        "lane-doctor",
        help="Run read-only distributed lane diagnostics.",
    )
    lane_doctor.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    lane_doctor.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum rows to include for lane, recovery and task event views.",
    )

    parser.add_argument("--env-file", default="")
    args = parser.parse_args()

    env_path = Path(args.env_file) if args.env_file else None
    load_env(env_path)
    if args.command == "doctor":
        settings = GatewaySettings.from_env()
        report = run_doctor(settings, env_file=env_path)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_doctor_text(report))
        if not report.get("ok", False):
            raise SystemExit(1)
        return
    if args.command == "postgres-init":
        settings = GatewaySettings.from_env()
        sql = build_postgres_schema_sql()
        if args.print_sql:
            print(sql)
            return
        initialize_postgres_schema(
            url=settings.postgres_url,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
        )
        print({"result": "ok", "postgres_url": settings.postgres_url})
        return
    if args.command == "postgres-check-schema":
        settings = GatewaySettings.from_env()
        result = check_postgres_schema(
            url=settings.postgres_url,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
        )
        print(result.to_dict())
        return
    if args.command == "postgres-migrate-local":
        settings = GatewaySettings.from_env()
        settings.ensure_directories()
        writer = PostgresWriteRepository(
            url=settings.postgres_url,
            enabled=True,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
        )
        report = backfill_local_state_to_repository(
            settings,
            writer,
            dry_run=bool(args.dry_run),
        )
        print(report.to_dict())
        return
    if args.command == "postgres-smoke":
        settings = GatewaySettings.from_env()
        result = run_postgres_smoke(settings)
        print(result)
        return

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
    if args.command == "delivery-republish":
        result = app.control_plane.republish_deliveries(
            include_pending=not args.no_pending,
            include_retrying=not args.no_retrying,
        )
        print(result)
        return
    if args.command == "lane-doctor":
        report = build_lane_doctor_report(app, limit=args.limit)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_lane_doctor_text(report))
        return
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
