from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
import json
import time
from typing import Any

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.bootstrap import build_channel_manager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import (
    load_agents,
    load_auth_profiles,
    load_bindings,
    load_channel_accounts,
    read_agents_source,
    read_bindings_source,
    read_channels_source,
    read_profiles_source,
    save_agents,
    save_auth_profiles,
    save_bindings,
    save_channel_accounts,
    write_json_atomic,
)
from agent_gateway.runtime.state.queue import DeliveryQueue, QueuedDelivery
from agent_gateway.runtime.tasks.models import TaskInstance, TaskStatus
from agent_gateway.runtime.tasks.queue import LocalTaskQueue
from agent_gateway.runtime.infra.postgres_client import PostgresClient
from agent_gateway.runtime.state.postgres import PostgresWriteRepository, check_postgres_schema
from agent_gateway.ai.context.diet import DietStore
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.context.personal import PersonalStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.alerts import AlertStore
from agent_gateway.runtime.observability.metrics import MetricsStore
from agent_gateway.runtime.state.adapter import LocalStateReadRepository
from agent_gateway.runtime.state.repository import StateReadRepository
from agent_gateway.runtime.domain.ids import normalize_agent_id
from agent_gateway.runtime.domain.models import AgentConfig, Binding
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.agent_manifest import (
    ALLOWED_TOOL_CAPABILITIES,
    build_agent_template,
    materialize_agent_template,
    validate_agent_config,
)
from agent_gateway.runtime.execution.autonomy import AutonomyRuntime
from agent_gateway.runtime.execution.channel_runtime import ChannelRuntime
from agent_gateway.runtime.execution.resilience import AuthProfile
from agent_gateway.runtime.execution.resilience import ProfileManager
from agent_gateway.ai.tools.registry import ToolRegistry


SUPPORTED_CHANNELS = {"cli", "telegram", "feishu"}


@dataclass(slots=True)
class GatewayControlPlane:
    """控制面聚合器。

    统一封装配置读写、运行时状态查询和控制操作，供 Dashboard、控制 API 和运维命令使用。
    """

    settings: GatewaySettings
    agents: AgentManager
    bindings: BindingTable
    profiles: ProfileManager
    channels: ChannelManager
    tools: ToolRegistry | None = None
    autonomy: AutonomyRuntime | None = None
    channel_runtime: ChannelRuntime | None = None
    delivery_queue: DeliveryQueue | None = None
    delivery_runtime: Any = None
    feishu_long_connection_runtime: Any = None
    feishu_onboarding: Any = None
    event_store: RuntimeEventStore | None = None
    metrics_store: MetricsStore | None = None
    metrics_runtime: Any = None
    alert_store: AlertStore | None = None
    alerts_runtime: Any = None
    redis_client: Any = None
    postgres_client: PostgresClient | None = None
    state_repository: StateReadRepository | None = None
    state_write_repository: PostgresWriteRepository | None = None
    task_worker: Any = None
    task_queue: LocalTaskQueue | None = None
    personal_store: PersonalStore | None = None

    def _record_config_audit(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        actor: str = "control-plane",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把配置变更写入 PostgreSQL 审计表，失败时不影响主流程。"""

        writer = self.state_write_repository
        if writer is None and isinstance(self.state_repository, PostgresWriteRepository):
            writer = self.state_repository
        if writer is None:
            return
        payload = {
            "id": f"{entity_type}:{entity_id}:{action}:{int(time.time() * 1000)}",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "before": before or {},
            "after": after or {},
            "actor": actor,
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        try:
            writer.append("config_audits", payload)
        except Exception:
            return

    def _record_lane_recovery_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        session_key: str = "",
        metadata: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """记录 session lane 恢复操作事件，失败不影响控制面主流程。"""

        if self.event_store is None:
            return
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="session_lane_recovery",
                message=message,
                session_key=session_key,
                error=error,
                metadata=metadata or {},
            )
        except Exception:
            return

    def _state_repo_list(
        self,
        table: str,
        *,
        limit: int = 500,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.state_repository is None:
            return []
        try:
            return self.state_repository.list(table, limit=limit, filters=filters or {})
        except Exception:
            return []

    def _state_repo_upsert(self, table: str, row: dict[str, Any]) -> bool:
        writer = self.state_write_repository
        if writer is None and isinstance(self.state_repository, PostgresWriteRepository):
            writer = self.state_repository
        if writer is None:
            return False
        try:
            writer.upsert(table, row)
            return True
        except Exception:
            return False

    def _state_repo_delete(self, table: str, key: str) -> bool:
        writer = self.state_write_repository
        if writer is None and isinstance(self.state_repository, PostgresWriteRepository):
            writer = self.state_repository
        if writer is None:
            return False
        try:
            writer.delete(table, key)
            return True
        except Exception:
            return False

    def list_bindings(self) -> list[Binding]:
        """返回当前生效的路由绑定列表。"""

        return self.bindings.list_all()

    def list_agents(self) -> list[AgentConfig]:
        """返回当前已加载的 Agent 配置。"""

        return self.agents.list()

    def list_tool_capabilities(self) -> list[dict[str, Any]]:
        """按 capability tag 汇总工具暴露情况。"""

        if self.tools is None:
            return []
        return [
            {
                "tag": tag,
                "tools": self.tools.names_for_tags([tag]),
            }
            for tag in sorted(ALLOWED_TOOL_CAPABILITIES)
        ]

    def validate_agent(self, agent: AgentConfig) -> list[str]:
        """校验 Agent 配置，主要检查工具能力声明是否合法。"""

        if self.tools is None:
            return []
        return validate_agent_config(agent, self.tools)

    def list_profiles(self) -> list[dict[str, Any]]:
        """返回模型 profile 的运行态快照。"""

        return self.profiles.snapshot()

    def list_channels(self) -> list[dict[str, Any]]:
        """汇总已配置通道和当前激活实例的状态。"""

        active_accounts = {
            (account.channel, account.account_id): account
            for account in self.channels.accounts
        }
        rows = [
            {
                "channel": row.get("channel", ""),
                "account_id": row.get("account_id", ""),
                "label": row.get("label", ""),
                "enabled": bool(row.get("enabled", True)),
                "active": (row.get("channel", ""), row.get("account_id", "")) in active_accounts,
                "has_token": bool(
                    row.get("token")
                    or row.get("token_env")
                    or active_accounts.get((row.get("channel", ""), row.get("account_id", "")), None)
                ),
                "config_keys": sorted(
                    row.get("config", {}).keys() if isinstance(row.get("config"), dict) else []
                ),
            }
            for row in self.get_source("channels").get("channels", [])
            if isinstance(row, dict)
        ]
        seen = {(row["channel"], row["account_id"]) for row in rows}
        for key, account in active_accounts.items():
            if key in seen:
                continue
            rows.append(
                {
                    "channel": account.channel,
                    "account_id": account.account_id,
                    "label": account.label,
                    "enabled": True,
                    "active": True,
                    "has_token": bool(account.token),
                    "config_keys": sorted(account.config.keys()),
                }
            )
        return rows

    def delivery_stats(self) -> dict[str, Any]:
        """返回投递队列的摘要统计。"""

        queue = self._require_delivery_queue()
        pending = queue.pending_entries()
        retrying = queue.retrying_entries()
        failed = queue.failed_entries()
        return {
            "pending": len(pending),
            "retrying": len(retrying),
            "failed": len(failed),
            "retry_ready": sum(1 for entry in retrying if not entry.next_retry_at or entry.next_retry_at <= time.time()),
            "oldest_pending_at": min((entry.enqueued_at for entry in pending), default=None),
            "oldest_retrying_at": min((entry.enqueued_at for entry in retrying), default=None),
            "oldest_failed_at": min((entry.enqueued_at for entry in failed), default=None),
            "broker": queue.broker_stats(),
        }

    def tail_events(
        self,
        *,
        limit: int = 100,
        event_type: str = "",
        component: str = "",
        status: str = "",
        correlation_id: str = "",
        agent_id: str = "",
        channel: str = "",
        job_id: str = "",
        delivery_id: str = "",
    ) -> dict[str, Any]:
        """按条件回看最近运行事件。"""

        if self.event_store is None and self.state_repository is None:
            return {"items": [], "count": 0, "configured": False}
        if self.event_store is not None:
            items = self.event_store.tail(
                limit=limit,
                event_type=event_type,
                component=component,
                status=status,
                correlation_id=correlation_id,
                agent_id=agent_id,
                channel=channel,
                job_id=job_id,
                delivery_id=delivery_id,
            )
        else:
            items = self.state_repository.list(
                "runtime_events",
                limit=limit,
                filters={
                    "event_type": event_type,
                    "component": component,
                    "status": status,
                    "correlation_id": correlation_id,
                    "agent_id": agent_id,
                    "channel": channel,
                    "job_id": job_id,
                    "delivery_id": delivery_id,
                },
            )
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "limit": max(1, min(int(limit), 500)),
        }

    def task_executions(
        self,
        *,
        limit: int = 50,
        task_id: str = "",
        session_key: str = "",
        worker_id: str = "",
        event_type: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        """回看 TaskWorkerRuntime 执行生命周期事件。"""

        safe_limit = max(1, min(int(limit), 200))
        lookup_limit = max(safe_limit, min(safe_limit * 5, 500))
        requested_type = str(event_type or "").strip()
        if requested_type and not requested_type.startswith("task.worker."):
            return {
                "items": [],
                "count": 0,
                "configured": self.event_store is not None or self.state_repository is not None,
                "limit": safe_limit,
                "filters": {
                    "task_id": task_id,
                    "session_key": session_key,
                    "worker_id": worker_id,
                    "event_type": requested_type,
                    "status": status,
                },
            }
        events = self.tail_events(
            limit=lookup_limit,
            event_type=requested_type,
            component="task_worker",
            status=status,
            correlation_id=task_id,
        )
        filtered: list[dict[str, Any]] = []
        for row in list(events.get("items", []) or []):
            row_type = str(row.get("type", ""))
            if not row_type.startswith("task.worker."):
                continue
            if session_key and str(row.get("session_key", "")) != session_key:
                continue
            metadata = row.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            if worker_id and str(metadata.get("worker_id", "")) != worker_id:
                continue
            filtered.append(row)
        items = filtered[-safe_limit:]
        return {
            "items": items,
            "count": len(items),
            "configured": bool(events.get("configured")),
            "limit": safe_limit,
            "filters": {
                "task_id": task_id,
                "session_key": session_key,
                "worker_id": worker_id,
                "event_type": requested_type,
                "status": status,
            },
        }

    def session_scheduler_status(self, *, detail: bool = True, limit: int = 20) -> dict[str, Any]:
        """查看 Redis session ready scheduler 的运行状态。"""

        return self._session_scheduler_status(detail=detail, limit=limit)

    def rebuild_session_scheduler(self, *, limit: int = 5000) -> dict[str, Any]:
        """从 TaskStore / PostgreSQL 事实任务重建 Redis session 调度索引。"""

        queue = self._require_task_queue()
        scheduler = getattr(queue, "session_scheduler", None)
        if scheduler is None or not getattr(scheduler, "enabled", False):
            return {"ok": False, "rebuilt": 0, "configured": scheduler is not None, "enabled": False}
        safe_limit = max(1, min(int(limit), 50_000))
        if self.state_repository is not None:
            rows = self.state_repository.list(
                "tasks",
                limit=safe_limit,
                filters={"status": ["pending", "retrying"]},
            )
            tasks = []
            for row in rows:
                try:
                    tasks.append(TaskInstance.from_dict(row))
                except (KeyError, TypeError, ValueError):
                    continue
        else:
            tasks = queue.store.list(statuses=["pending", "retrying"], limit=safe_limit)
        rebuilt = int(scheduler.rebuild(tasks))
        return {
            "ok": True,
            "rebuilt": rebuilt,
            "configured": True,
            "enabled": True,
            "limit": safe_limit,
            "scheduler": self._session_scheduler_status(detail=True, limit=20),
        }

    def lane_recovery_events(
        self,
        *,
        limit: int = 50,
        session_key: str = "",
        worker_id: str = "",
        event_type: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        """回看 session lane recovery 审计事件。"""

        safe_limit = max(1, min(int(limit), 200))
        lookup_limit = max(safe_limit, min(safe_limit * 5, 500))
        requested_type = str(event_type or "").strip()
        if requested_type and not requested_type.startswith("session_lane.recovery."):
            return {
                "items": [],
                "count": 0,
                "configured": self.event_store is not None or self.state_repository is not None,
                "limit": safe_limit,
                "filters": {
                    "session_key": session_key,
                    "worker_id": worker_id,
                    "event_type": requested_type,
                    "status": status,
                },
            }
        events = self.tail_events(
            limit=lookup_limit,
            event_type=requested_type,
            component="session_lane_recovery",
            status=status,
        )
        filtered: list[dict[str, Any]] = []
        for row in list(events.get("items", []) or []):
            row_type = str(row.get("type", ""))
            if not row_type.startswith("session_lane.recovery."):
                continue
            if session_key and str(row.get("session_key", "")) != session_key:
                continue
            metadata = row.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            if worker_id and str(metadata.get("worker_id", "")) != worker_id:
                continue
            filtered.append(row)
        items = filtered[-safe_limit:]
        return {
            "items": items,
            "count": len(items),
            "configured": bool(events.get("configured")),
            "limit": safe_limit,
            "filters": {
                "session_key": session_key,
                "worker_id": worker_id,
                "event_type": requested_type,
                "status": status,
            },
        }

    def lane_doctor(self, *, limit: int = 20) -> dict[str, Any]:
        """构建分布式 lane 只读诊断报告。

        该方法只读取运行态、健康检查、lane 状态和 worker 事件，不消费队列、
        不释放 lane，也不写入恢复审计事件，适合 CLI、Dashboard 和外部巡检复用。
        """

        safe_limit = max(1, min(int(limit), 200))
        runtime = self.runtime_status()
        health = self.health_check()
        tasks = runtime.get("tasks", {})
        tasks = tasks if isinstance(tasks, dict) else {}
        persisted_lanes = tasks.get("persisted_lanes", {})
        persisted_lanes = persisted_lanes if isinstance(persisted_lanes, dict) else {}
        queue_stats = tasks.get("queue", {})
        queue_stats = queue_stats if isinstance(queue_stats, dict) else {}
        task_broker = tasks.get("broker") or queue_stats.get("broker", {})
        task_broker = task_broker if isinstance(task_broker, dict) else {}
        session_locks = tasks.get("session_locks", {})
        session_locks = session_locks if isinstance(session_locks, dict) else {}
        lanes = self.list_session_lanes(state="owned", limit=safe_limit)
        lane_items = list(lanes.get("items", []) or [])
        stale_lanes = list(persisted_lanes.get("stale_items", []) or [])[:safe_limit]
        if not persisted_lanes.get("configured") and lanes.get("configured"):
            stale_lanes = [row for row in lane_items if self._is_session_lane_stale(row)][:safe_limit]
        recovery_plan = self.plan_session_lane_recovery(limit=safe_limit)
        recovery_events = self.lane_recovery_events(limit=safe_limit)
        task_executions = self.task_executions(limit=safe_limit)
        redis = runtime.get("redis", {})
        redis = redis if isinstance(redis, dict) else {}
        postgres = runtime.get("postgres", {})
        postgres = postgres if isinstance(postgres, dict) else {}
        checks: list[dict[str, Any]] = [
            {
                "name": "health",
                "status": health.get("status", "unknown"),
                "ok": bool(health.get("ok")),
            },
            {
                "name": "redis",
                "status": "ok" if redis.get("ok") else "warning",
                "enabled": bool(redis.get("enabled")),
                "ok": bool(redis.get("ok")),
            },
            {
                "name": "postgres",
                "status": "ok" if postgres.get("ok") else "warning",
                "enabled": bool(postgres.get("enabled")),
                "ok": bool(postgres.get("ok")),
            },
            {
                "name": "inbound_broker",
                "status": "ok"
                if int(task_broker.get("dead_letter_messages", 0) or 0) == 0
                else "warning",
                "enabled": bool(task_broker.get("enabled")),
                "messages": int(task_broker.get("messages", 0) or 0),
                "dead_letter_messages": int(task_broker.get("dead_letter_messages", 0) or 0),
            },
            {
                "name": "session_lanes",
                "status": "ok" if int(persisted_lanes.get("stale_count", len(stale_lanes)) or 0) == 0 else "warning",
                "owned": int(persisted_lanes.get("count", len(lane_items)) or 0),
                "stale": int(persisted_lanes.get("stale_count", len(stale_lanes)) or 0),
            },
            {
                "name": "session_locks",
                "status": "ok"
                if int(session_locks.get("blocked_session_count", 0) or 0) == 0
                else "warning",
                "blocked_session_count": int(session_locks.get("blocked_session_count", 0) or 0),
                "skip_count": int(session_locks.get("skip_count", 0) or 0),
            },
        ]
        warning_count = sum(1 for row in checks if row.get("status") == "warning")
        readiness = self._distributed_lane_readiness(
            runtime=runtime,
            health=health,
            tasks=tasks,
            task_broker=task_broker,
            persisted_lanes=persisted_lanes,
            lanes=lanes,
            redis=redis,
            postgres=postgres,
        )
        ok = bool(health.get("ok")) and warning_count == 0
        return {
            "ok": ok,
            "status": "ok" if ok else "warning",
            "limit": safe_limit,
            "checks": checks,
            "readiness": readiness,
            "summary": {
                "warnings": warning_count,
                "ready": bool(readiness.get("ready")),
                "readiness_passed": int(readiness.get("passed", 0) or 0),
                "readiness_failed": int(readiness.get("failed", 0) or 0),
                "owned_lanes": int(persisted_lanes.get("count", len(lane_items)) or 0),
                "stale_lanes": int(persisted_lanes.get("stale_count", len(stale_lanes)) or 0),
                "recovery_actions": int(recovery_plan.get("action_count", 0) or 0),
                "broker_messages": int(task_broker.get("messages", 0) or 0),
                "broker_dead_letters": int(task_broker.get("dead_letter_messages", 0) or 0),
            },
            "lanes": lanes,
            "stale_lanes": stale_lanes,
            "recovery_plan": recovery_plan,
            "recovery_events": recovery_events,
            "task_executions": task_executions,
            "runtime": {
                "tasks": tasks,
                "redis": redis,
                "postgres": postgres,
            },
            "health": health,
        }

    def _distributed_lane_readiness(
        self,
        *,
        runtime: dict[str, Any],
        health: dict[str, Any],
        tasks: dict[str, Any],
        task_broker: dict[str, Any],
        persisted_lanes: dict[str, Any],
        lanes: dict[str, Any],
        redis: dict[str, Any],
        postgres: dict[str, Any],
    ) -> dict[str, Any]:
        """评估最终分布式 lane 形态的关键开关和依赖是否就绪。"""

        delivery = runtime.get("delivery", {})
        delivery = delivery if isinstance(delivery, dict) else {}
        delivery_broker = delivery.get("broker", {})
        delivery_broker = delivery_broker if isinstance(delivery_broker, dict) else {}
        registered_task_types = tasks.get("registered_task_types", [])
        if not isinstance(registered_task_types, list):
            registered_task_types = []
        checks = [
            self._readiness_check(
                "inbound_task_queue",
                bool(self.settings.inbound_task_queue_enabled),
                "非 CLI 入站消息会先落 agent_inbound 任务队列",
                "需要开启 GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true",
            ),
            self._readiness_check(
                "inbound_broker.rabbitmq",
                self.settings.inbound_broker == "rabbitmq" and bool(task_broker.get("enabled")),
                "入站任务通过 RabbitMQ 分区 broker 分发 task_id 引用",
                "需要设置 GATEWAY_INBOUND_BROKER=rabbitmq 并确认 broker 可用",
                {
                    "configured": self.settings.inbound_broker,
                    "partitions": task_broker.get("partitions", self.settings.inbound_rabbitmq_partitions),
                    "prefetch": task_broker.get("prefetch", self.settings.inbound_rabbitmq_prefetch),
                },
            ),
            self._readiness_check(
                "redis.lane_ownership",
                bool(redis.get("enabled")) and bool(redis.get("ok")),
                "Redis 可用于 session lane ownership、TTL 和续租",
                "需要开启 GATEWAY_REDIS_ENABLED=true 并确认 Redis ping 正常",
            ),
            self._readiness_check(
                "postgres.state",
                bool(postgres.get("enabled")) and bool(postgres.get("ok")),
                "PostgreSQL 可用于任务、事件和 lane 状态外置",
                "需要开启 GATEWAY_POSTGRES_ENABLED=true 并确认 PostgreSQL ping 正常",
            ),
            self._readiness_check(
                "task_worker.agent_inbound",
                bool(tasks.get("configured")) and "agent_inbound" in {str(item) for item in registered_task_types},
                "Worker 池已注册 agent_inbound handler，可消费入站任务",
                "需要启动 worker 角色并注册 agent_inbound handler",
                {
                    "running": bool(tasks.get("running")),
                    "worker_id": tasks.get("worker_id", ""),
                    "concurrency": int(tasks.get("concurrency", 0) or 0),
                },
            ),
            self._readiness_check(
                "session_lanes.persisted",
                bool(persisted_lanes.get("configured") or lanes.get("configured")),
                "PostgreSQL session_lanes 可查询 lane owner 和 stale 状态",
                "需要初始化数据库 schema 并接入 state repository",
            ),
            self._readiness_check(
                "delivery.reliable_outbound",
                bool(delivery.get("configured")),
                "Agent 回复会进入可靠投递队列再发送",
                "需要装配 DeliveryQueue 和 DeliveryRuntime",
                {
                    "delivery_broker": self.settings.delivery_broker,
                    "broker_enabled": bool(delivery_broker.get("enabled")),
                    "pending": int(delivery.get("pending", 0) or 0),
                    "failed": int(delivery.get("failed", 0) or 0),
                },
            ),
            self._readiness_check(
                "health.no_critical",
                int(health.get("summary", {}).get("critical", 0) or 0) == 0,
                "健康检查没有 critical 项",
                "需要先修复 health.check 中的 critical 项",
            ),
        ]
        failed = sum(1 for row in checks if row.get("status") == "fail")
        passed = sum(1 for row in checks if row.get("status") == "pass")
        return {
            "ready": failed == 0,
            "status": "ready" if failed == 0 else "not_ready",
            "passed": passed,
            "failed": failed,
            "checks": checks,
        }

    @staticmethod
    def _readiness_check(
        name: str,
        ok: bool,
        pass_message: str,
        fail_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成一条最终分布式 lane readiness 检查结果。"""

        return {
            "name": name,
            "status": "pass" if ok else "fail",
            "ok": bool(ok),
            "message": pass_message if ok else fail_message,
            "metadata": metadata or {},
        }

    def recent_errors(
        self,
        *,
        limit: int = 50,
        component: str = "",
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """筛出最近错误事件。"""

        if self.event_store is None and self.state_repository is None:
            return {"items": [], "count": 0, "configured": False}
        if self.event_store is not None:
            items = self.event_store.recent_errors(
                limit=limit,
                component=component,
                correlation_id=correlation_id,
            )
        else:
            items = self.state_repository.list(
                "errors",
                limit=limit,
                filters={
                    "component": component,
                    "correlation_id": correlation_id,
                },
            )
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "limit": max(1, min(int(limit), 200)),
        }

    def recent_memories(self, *, limit: int = 20) -> dict[str, Any]:
        """查看最近写入的记忆条目。"""

        if self.state_repository is not None:
            items = self.state_repository.list("memory_entries", limit=limit)
        else:
            store = MemoryStore(self.settings.workspace_root)
            items = store.recent_entries(limit=limit)
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "limit": max(1, min(int(limit), 200)),
        }

    def recent_diet(self, *, limit: int = 20, user_scope: str = "", date: str = "") -> dict[str, Any]:
        """查看最近饮食 Agent 产生的结构化记录。"""

        safe_limit = max(1, min(int(limit), 100))
        filters = {"user_scope": user_scope} if user_scope else {}
        if self.state_repository is None:
            return {
                "meals": [],
                "plans": [],
                "summaries": [],
                "users": [],
                "count": 0,
                "configured": False,
                "limit": safe_limit,
            }
        if not user_scope:
            meals = self.state_repository.list("meal_logs", limit=safe_limit, filters={})
            plans = self.state_repository.list("diet_plans", limit=safe_limit, filters={})
            summaries = self.state_repository.list(
                "daily_nutrition_summaries",
                limit=safe_limit,
                filters={},
            )
            users = self._diet_user_summaries([*meals, *plans, *summaries])
            return {
                "meals": [],
                "plans": [],
                "summaries": [],
                "users": users,
                "count": len(users),
                "configured": True,
                "limit": safe_limit,
                "user_scope": "",
                "requires_user_scope": True,
            }
        meals = self.state_repository.list("meal_logs", limit=safe_limit, filters=filters)
        plans = self.state_repository.list("diet_plans", limit=safe_limit, filters=filters)
        summaries = self.state_repository.list(
            "daily_nutrition_summaries",
            limit=safe_limit,
            filters=filters,
        )
        diet_store = DietStore(self.settings.workspace_root, read_backend=self.state_repository)
        today_status = diet_store.today_status(user_scope, date=date)
        return {
            "meals": meals,
            "plans": plans,
            "summaries": summaries,
            "users": [],
            "today_status": today_status,
            "count": len(meals) + len(plans) + len(summaries),
            "configured": True,
            "limit": safe_limit,
            "user_scope": user_scope,
        }

    def recent_personal(self, *, limit: int = 20, user_scope: str = "") -> dict[str, Any]:
        """查看个人秘书产生的结构化待办和复盘。"""

        safe_limit = max(1, min(int(limit), 100))
        if not user_scope.strip():
            return {
                "todos": [],
                "reviews": [],
                "count": 0,
                "configured": self.personal_store is not None,
                "limit": safe_limit,
                "user_scope": "",
                "requires_user_scope": True,
            }
        store = self.personal_store or PersonalStore(self.settings.workspace_root)
        todos = store.list_todos(status="all", limit=safe_limit, user_scope=user_scope)
        reviews = store.recent_reviews(limit=safe_limit, user_scope=user_scope)
        return {
            "todos": todos,
            "reviews": reviews,
            "count": len(todos) + len(reviews),
            "configured": True,
            "limit": safe_limit,
            "user_scope": user_scope,
        }

    @staticmethod
    def _diet_user_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把跨用户饮食明细折叠成用户级摘要，避免 Dashboard 混合展示私密记录。"""

        users: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_scope = str(row.get("user_scope", "")).strip()
            if not user_scope:
                continue
            item = users.setdefault(
                user_scope,
                {
                    "user_scope": user_scope,
                    "meal_count": 0,
                    "plan_count": 0,
                    "summary_count": 0,
                    "latest_at": 0.0,
                },
            )
            if "meal_date" in row:
                item["meal_count"] += 1
                timestamp = row.get("logged_at", 0)
            elif "plan_date" in row:
                item["plan_count"] += 1
                timestamp = row.get("created_at", 0)
            else:
                item["summary_count"] += 1
                timestamp = row.get("updated_at", row.get("created_at", 0))
            try:
                item["latest_at"] = max(float(item["latest_at"]), float(timestamp or 0))
            except (TypeError, ValueError):
                pass
        return sorted(users.values(), key=lambda item: float(item.get("latest_at", 0)), reverse=True)

    def metrics_snapshot(self) -> dict[str, Any]:
        """返回最近一条指标快照。"""

        if self.metrics_store is None:
            return {"configured": False}
        latest = self.metrics_store.latest()
        if latest is None:
            return {"configured": True, "available": False, "item": None}
        return {
            "configured": True,
            "available": True,
            "item": latest,
        }

    def metrics_tail(self, *, limit: int = 60) -> dict[str, Any]:
        """返回最近一段时间的原始指标序列。"""

        safe_limit = max(1, min(int(limit), 1000))
        if self.metrics_store is None:
            return {"items": [], "count": 0, "configured": False, "limit": safe_limit}
        items = self.metrics_store.tail(limit=safe_limit)
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "limit": safe_limit,
        }

    def metrics_summary(self, *, limit: int = 60) -> dict[str, Any]:
        """把近期指标序列压缩成面板可直接展示的摘要。"""

        safe_limit = max(1, min(int(limit), 1000))
        if self.metrics_store is None:
            return {"configured": False, "count": 0, "limit": safe_limit}
        items = self.metrics_store.tail(limit=safe_limit)
        if not items:
            return {
                "configured": True,
                "count": 0,
                "limit": safe_limit,
                "available": False,
                "latest": None,
                "window": None,
                "delivery": {},
                "lanes": {},
                "events": {},
                "cron": {},
                "profiles": {},
                "tasks": {},
            }

        def pick(section: str, key: str, default: int | float = 0) -> int | float:
            values: list[int | float] = []
            for row in items:
                payload = row.get(section, {})
                if not isinstance(payload, dict):
                    continue
                value = payload.get(key, default)
                if isinstance(value, bool):
                    values.append(int(value))
                elif isinstance(value, (int, float)):
                    values.append(value)
            return max(values) if values else default

        first = items[0]
        last = items[-1]
        return {
            "configured": True,
            "available": True,
            "count": len(items),
            "limit": safe_limit,
            "latest": last,
            "window": {
                "start_time": first.get("time"),
                "end_time": last.get("time"),
                "start_timestamp": first.get("timestamp"),
                "end_timestamp": last.get("timestamp"),
            },
            "delivery": {
                "max_pending": pick("delivery", "pending"),
                "max_failed": pick("delivery", "failed"),
                "max_retry_ready": pick("delivery", "retry_ready"),
                "max_oldest_pending_age_seconds": pick("delivery", "oldest_pending_age_seconds"),
                "max_oldest_failed_age_seconds": pick("delivery", "oldest_failed_age_seconds"),
            },
            "lanes": {
                "max_count": pick("lanes", "count"),
                "max_active": pick("lanes", "active"),
                "max_queued": pick("lanes", "queued"),
                "max_queue_depth": pick("lanes", "max_queue_depth"),
            },
            "events": {
                "max_errors_5m": pick("events", "errors_5m"),
                "max_rejected_5m": pick("events", "rejected_5m"),
                "max_delivery_failed_5m": pick("events", "delivery_failed_5m"),
                "max_tool_failed_5m": pick("events", "tool_failed_5m"),
                "max_cron_failed_5m": pick("events", "cron_failed_5m"),
            },
            "cron": {
                "max_configured": pick("cron", "configured"),
                "max_count": pick("cron", "count"),
                "max_enabled": pick("cron", "enabled"),
                "max_errored": pick("cron", "errored"),
            },
            "profiles": {
                "max_count": pick("profiles", "count"),
                "max_available": pick("profiles", "available"),
                "max_cooling_down": pick("profiles", "cooling_down"),
            },
            "tasks": {
                "max_pending": pick("tasks", "pending"),
                "max_running": pick("tasks", "running"),
                "max_retrying": pick("tasks", "retrying"),
                "max_failed": pick("tasks", "failed"),
                "broker_enabled": pick("tasks", "broker_enabled"),
                "max_broker_messages": pick("tasks", "broker_messages"),
                "max_broker_dead_letter_messages": pick(
                    "tasks",
                    "broker_dead_letter_messages",
                ),
                "max_broker_partitions": pick("tasks", "broker_partitions"),
                "max_broker_prefetch": pick("tasks", "broker_prefetch"),
                "max_broker_partition_messages": pick(
                    "tasks",
                    "broker_max_partition_messages",
                ),
            },
        }

    def active_alerts(self) -> dict[str, Any]:
        """返回当前激活中的告警。"""

        if self.alerts_runtime is None:
            return {"items": [], "count": 0, "configured": False}
        items = self.alerts_runtime.active_alerts()
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "notification_target": {
                "channel": self.settings.alert_channel,
                "account_id": self.settings.alert_account_id,
                "peer_id_configured": bool(self.settings.alert_peer_id),
                "agent_id": self.settings.alert_agent_id,
            },
        }

    def alert_history(self, *, limit: int = 50) -> dict[str, Any]:
        """返回最近告警历史。"""

        safe_limit = max(1, min(int(limit), 200))
        if self.alert_store is None:
            return {"items": [], "count": 0, "configured": False, "limit": safe_limit}
        items = self.alert_store.tail(limit=safe_limit)
        return {
            "items": items,
            "count": len(items),
            "configured": True,
            "limit": safe_limit,
        }

    def list_deliveries(
        self,
        *,
        state: str = "pending",
        limit: int = 50,
        include_text: bool = False,
    ) -> dict[str, Any]:
        """列出投递队列中的记录。"""

        queue = self._require_delivery_queue()
        normalized_state = self._normalize_delivery_state(state, allow_all=True)
        safe_limit = max(1, min(int(limit), 200))
        rows: list[dict[str, Any]] = []
        if normalized_state in {"pending", "all"}:
            rows.extend(
                self._delivery_entry_to_dict(entry, "pending", include_text=include_text)
                for entry in queue.pending_entries()
            )
        if normalized_state in {"retrying", "all"}:
            rows.extend(
                self._delivery_entry_to_dict(entry, "retrying", include_text=include_text)
                for entry in queue.retrying_entries()
            )
        if normalized_state in {"failed", "all"}:
            rows.extend(
                self._delivery_entry_to_dict(entry, "failed", include_text=include_text)
                for entry in queue.failed_entries()
            )
        rows.sort(key=lambda row: (str(row["state"]), float(row["enqueued_at"])))
        return {
            "state": normalized_state,
            "count": len(rows),
            "items": rows[:safe_limit],
            "limit": safe_limit,
        }

    def retry_delivery(self, delivery_id: str) -> bool:
        """立即重试一条失败投递。"""

        if not delivery_id:
            raise ValueError("delivery_id is required")
        return self._require_delivery_queue().retry_now(delivery_id)

    def republish_deliveries(self, *, include_pending: bool = True, include_retrying: bool = True) -> dict[str, Any]:
        """从事实状态重新发布 delivery 引用到 broker。"""

        queue = self._require_delivery_queue()
        published = 0
        states: list[str] = []
        if include_pending:
            published += queue.republish_pending()
            states.append("pending")
        if include_retrying:
            published += queue.publish_due_retries(now=time.time())
            states.append("retrying")
        return {"published": published, "states": states, "broker": queue.broker_stats()}

    def discard_delivery(self, delivery_id: str, *, state: str = "any") -> bool:
        """人工丢弃一条投递记录。"""

        if not delivery_id:
            raise ValueError("delivery_id is required")
        normalized = self._normalize_delivery_state(state, allow_all=False, allow_any=True)
        return self._require_delivery_queue().discard(delivery_id, state=normalized)

    def list_tasks(
        self,
        *,
        status: str = "all",
        limit: int = 50,
        include_payload: bool = False,
    ) -> dict[str, Any]:
        """列出后台任务实例。"""

        statuses = self._normalize_task_statuses(status)
        safe_limit = max(1, min(int(limit), 200))
        if self.state_repository is not None:
            tasks = self.state_repository.list(
                "tasks",
                limit=safe_limit,
                filters={"statuses": statuses},
            )
        else:
            queue = self._require_task_queue()
            tasks = [task.to_dict() for task in queue.store.list(statuses=statuses, limit=safe_limit)]
        return {
            "status": status.strip().lower() if status else "all",
            "count": len(tasks),
            "items": [
                self._task_to_dict(task, include_payload=include_payload)
                if not isinstance(task, dict)
                else (
                    TaskInstance.from_dict(task).to_dict()
                    if include_payload
                    else self._task_to_dict(TaskInstance.from_dict(task), include_payload=False)
                )
                for task in tasks
            ],
            "limit": safe_limit,
        }

    def list_session_lanes(
        self,
        *,
        state: str = "owned",
        limit: int = 50,
        session_key: str = "",
        worker_id: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """列出持久化 session lane owner 状态。"""

        safe_limit = max(1, min(int(limit), 200))
        if self.state_repository is None:
            return {
                "configured": False,
                "state": state or "owned",
                "count": 0,
                "items": [],
                "limit": safe_limit,
            }
        filters = {
            "state": str(state or "owned"),
            "session_key": str(session_key or ""),
            "worker_id": str(worker_id or ""),
            "task_id": str(task_id or ""),
        }
        rows = self._state_repo_list("session_lanes", limit=safe_limit, filters=filters)
        return {
            "configured": True,
            "state": filters["state"],
            "count": len(rows),
            "items": rows[:safe_limit],
            "limit": safe_limit,
            "filters": filters,
        }

    def list_session_lane_history(
        self,
        *,
        limit: int = 50,
        session_key: str = "",
        worker_id: str = "",
        task_id: str = "",
        event: str = "",
    ) -> dict[str, Any]:
        """列出持久化 session lane owner 历史事件。"""

        safe_limit = max(1, min(int(limit), 200))
        filters = {
            "session_key": str(session_key or ""),
            "worker_id": str(worker_id or ""),
            "task_id": str(task_id or ""),
            "event": str(event or ""),
        }
        if self.state_repository is None:
            return {
                "configured": False,
                "count": 0,
                "items": [],
                "limit": safe_limit,
                "filters": filters,
            }
        rows = self._state_repo_list("session_lane_events", limit=safe_limit, filters=filters)
        return {
            "configured": True,
            "count": len(rows),
            "items": rows[:safe_limit],
            "limit": safe_limit,
            "filters": filters,
        }

    def session_lane_recovery_suggestions(self, *, limit: int = 50) -> dict[str, Any]:
        """生成 stale session lane 的人工恢复建议。

        该接口只根据 PostgreSQL 持久状态给出建议，不直接释放 Redis 锁或修改数据库。
        真正释放仍需显式调用 `release_session_lane()`，避免误操作影响活跃 worker。
        """

        safe_limit = max(1, min(int(limit), 200))
        lanes = self.list_session_lanes(state="owned", limit=safe_limit)
        rows = list(lanes.get("items", []) or [])
        now = time.time()
        suggestions: list[dict[str, Any]] = []
        for row in rows:
            if not self._is_session_lane_stale(row, now=now):
                continue
            renewed_at = self._coerce_float(row.get("renewed_at"))
            ttl_seconds = self._coerce_int(row.get("ttl_seconds"))
            expired_seconds = max(0.0, now - (renewed_at + ttl_seconds))
            session_key = str(row.get("session_key", ""))
            owner_token = str(row.get("owner_token", ""))
            suggestions.append(
                {
                    "session_key": session_key,
                    "worker_id": str(row.get("worker_id", "")),
                    "task_id": str(row.get("task_id", "")),
                    "owner_token": owner_token,
                    "ttl_seconds": ttl_seconds,
                    "renewed_at": renewed_at,
                    "expired_seconds": round(expired_seconds, 3),
                    "action": "release_session_lane",
                    "severity": "warning",
                    "message": "持久 Lane 已超过 TTL，可确认 worker 已退出后释放 PostgreSQL owner 状态。",
                    "release_params": {
                        "session_key": session_key,
                        "owner_token": owner_token,
                        "force": False,
                        "reason": "stale lane recovery",
                    },
                    "lane": row,
                }
            )
        return {
            "configured": bool(lanes.get("configured")),
            "count": len(suggestions),
            "items": suggestions[:safe_limit],
            "limit": safe_limit,
        }

    def plan_session_lane_recovery(self, *, limit: int = 50) -> dict[str, Any]:
        """生成 stale session lane 批量恢复预检计划。

        该计划始终是 dry-run，只汇总可人工执行的 release 参数和跳过原因，不修改
        PostgreSQL 或 Redis 状态。后续批量执行必须先通过这个预检结果做人工确认。
        """

        safe_limit = max(1, min(int(limit), 200))
        suggestions = self.session_lane_recovery_suggestions(limit=safe_limit)
        items = list(suggestions.get("items", []) or [])
        actions: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for item in items:
            release_params = dict(item.get("release_params") or {})
            session_key = str(release_params.get("session_key") or item.get("session_key") or "")
            owner_token = str(release_params.get("owner_token") or item.get("owner_token") or "")
            if not session_key:
                skipped.append(
                    {
                        "reason": "missing session_key",
                        "item": item,
                    }
                )
                continue
            if not owner_token:
                skipped.append(
                    {
                        "reason": "missing owner_token",
                        "session_key": session_key,
                        "item": item,
                    }
                )
                continue
            actions.append(
                {
                    "session_key": session_key,
                    "worker_id": str(item.get("worker_id", "")),
                    "task_id": str(item.get("task_id", "")),
                    "owner_token": owner_token,
                    "expired_seconds": item.get("expired_seconds", 0.0),
                    "method": "tasks.lanes.release",
                    "params": {
                        "session_key": session_key,
                        "owner_token": owner_token,
                        "force": False,
                        "reason": "stale lane recovery",
                    },
                    "message": "确认 worker 已退出后，可按该参数释放持久 Lane owner 状态。",
                }
            )
        return {
            "configured": bool(suggestions.get("configured")),
            "dry_run": True,
            "candidate_count": len(items),
            "action_count": len(actions),
            "skipped_count": len(skipped),
            "actions": actions[:safe_limit],
            "skipped": skipped[:safe_limit],
            "limit": safe_limit,
            "warning": "该计划不会自动释放任何 Lane；执行 release 前应确认对应 worker 已停止或 Redis TTL 已过期。",
        }

    def execute_session_lane_recovery(
        self,
        *,
        limit: int = 50,
        execute: bool = False,
        record_events: bool = True,
    ) -> dict[str, Any]:
        """受控执行 stale session lane 批量恢复。

        默认仍是 dry-run。只有显式传入 `execute=True` 时，才会逐条调用
        `release_session_lane()`，并复用其 stale、owner_token 和 writer 配置校验。
        """

        plan = self.plan_session_lane_recovery(limit=limit)
        actions = list(plan.get("actions", []) or [])
        if not execute:
            payload = {
                "configured": bool(plan.get("configured")),
                "dry_run": True,
                "executed": False,
                "released_count": 0,
                "failed_count": 0,
                "results": [],
                "plan": plan,
                "message": "未传入 execute=true，仅返回批量恢复预检计划。",
            }
            if record_events:
                self._record_lane_recovery_event(
                    "session_lane.recovery.dry_run",
                    status="ok",
                    message="session lane 批量恢复 dry-run 已生成",
                    metadata={
                        "candidate_count": plan.get("candidate_count", 0),
                        "action_count": plan.get("action_count", 0),
                        "skipped_count": plan.get("skipped_count", 0),
                        "limit": plan.get("limit", limit),
                    },
                )
            return payload
        results: list[dict[str, Any]] = []
        released_count = 0
        failed_count = 0
        for action in actions:
            params = dict(action.get("params") or {})
            result = self.release_session_lane(
                session_key=str(params.get("session_key", "")),
                owner_token=str(params.get("owner_token", "")),
                force=bool(params.get("force", False)),
                reason=str(params.get("reason", "stale lane recovery")),
            )
            row = {
                "session_key": params.get("session_key", ""),
                "worker_id": action.get("worker_id", ""),
                "task_id": action.get("task_id", ""),
                "owner_token": params.get("owner_token", ""),
                "released": bool(result.get("released")),
                "reason": result.get("reason", ""),
                "result": result,
            }
            if row["released"]:
                released_count += 1
                if record_events:
                    self._record_lane_recovery_event(
                        "session_lane.recovery.released",
                        status="ok",
                        message="stale session lane 已释放",
                        session_key=str(row["session_key"]),
                        metadata={
                            "worker_id": row["worker_id"],
                            "task_id": row["task_id"],
                            "owner_token": row["owner_token"],
                            "reason": row["reason"],
                        },
                    )
            else:
                failed_count += 1
                if record_events:
                    self._record_lane_recovery_event(
                        "session_lane.recovery.release_failed",
                        status="failed",
                        message="stale session lane 释放失败",
                        session_key=str(row["session_key"]),
                        error=str(row["reason"]),
                        metadata={
                            "worker_id": row["worker_id"],
                            "task_id": row["task_id"],
                            "owner_token": row["owner_token"],
                            "reason": row["reason"],
                        },
                    )
            results.append(row)
        payload = {
            "configured": bool(plan.get("configured")),
            "dry_run": False,
            "executed": True,
            "released_count": released_count,
            "failed_count": failed_count,
            "results": results,
            "plan": plan,
        }
        if record_events:
            self._record_lane_recovery_event(
                "session_lane.recovery.completed",
                status="ok" if failed_count == 0 else "warning",
                message="session lane 批量恢复执行完成",
                metadata={
                    "candidate_count": plan.get("candidate_count", 0),
                    "action_count": plan.get("action_count", 0),
                    "released_count": released_count,
                    "failed_count": failed_count,
                    "limit": plan.get("limit", limit),
                },
            )
        return payload

    def release_session_lane(
        self,
        *,
        session_key: str,
        owner_token: str = "",
        force: bool = False,
        reason: str = "manual release",
    ) -> dict[str, Any]:
        """受约束地释放一条持久化 session lane 状态。

        默认只允许释放已 stale 的 lane；如果需要释放仍在 TTL 内的 owner，必须显式
        传入 `force=True`，避免误操作影响活跃 worker。
        """

        normalized_session = str(session_key or "").strip()
        if not normalized_session:
            raise ValueError("session_key is required")
        writer = self.state_write_repository
        if writer is None and isinstance(self.state_repository, PostgresWriteRepository):
            writer = self.state_repository
        if writer is None:
            return {
                "ok": False,
                "configured": False,
                "released": False,
                "reason": "state write repository not configured",
            }
        lanes = self.list_session_lanes(
            state="owned",
            limit=1,
            session_key=normalized_session,
        )
        items = list(lanes.get("items", []) or [])
        lane = items[0] if items else {}
        if not lane:
            return {
                "ok": False,
                "configured": True,
                "released": False,
                "reason": "lane not found or not owned",
                "session_key": normalized_session,
            }
        if owner_token and str(lane.get("owner_token", "")) != owner_token:
            return {
                "ok": False,
                "configured": True,
                "released": False,
                "reason": "owner_token mismatch",
                "session_key": normalized_session,
                "lane": lane,
            }
        stale = self._is_session_lane_stale(lane)
        if not stale and not force:
            return {
                "ok": False,
                "configured": True,
                "released": False,
                "reason": "lane is not stale; pass force=true to release",
                "session_key": normalized_session,
                "lane": lane,
            }
        try:
            released = bool(
                writer.release_session_lane(
                    normalized_session,
                    owner_token=owner_token,
                    reason=reason,
                    now=time.time(),
                )
            )
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "released": False,
                "reason": str(exc),
                "session_key": normalized_session,
                "lane": lane,
            }
        return {
            "ok": released,
            "configured": True,
            "released": released,
            "reason": "released" if released else "release did not match any row",
            "session_key": normalized_session,
            "forced": bool(force),
            "stale": stale,
            "lane": lane,
        }

    def get_task(self, task_id: str, *, include_payload: bool = True) -> dict[str, Any]:
        """读取单条后台任务详情。"""

        if not task_id:
            raise ValueError("task_id is required")
        if self.state_repository is not None:
            task = self.state_repository.get("tasks", task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            task_instance = TaskInstance.from_dict(task)
            return task if include_payload else self._task_to_dict(task_instance, include_payload=False)
        task = self._require_task_queue().store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return self._task_to_dict(task, include_payload=include_payload)

    def cancel_task(self, task_id: str) -> bool:
        """取消一条尚未终态的后台任务。"""

        if not task_id:
            raise ValueError("task_id is required")
        queue = self._require_task_queue()
        task = queue.store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if task.status in {"done", "failed", "cancelled"}:
            return False
        queue.cancel(task_id)
        return True

    def retry_task(self, task_id: str) -> bool:
        """把失败或已取消的任务重新放回可执行队列。"""

        if not task_id:
            raise ValueError("task_id is required")
        queue = self._require_task_queue()
        task = queue.store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if task.status not in {"failed", "cancelled"}:
            return False
        queue.retry(task_id, error="manual retry requested")
        return True

    async def flush_delivery(self, *, rounds: int = 1) -> dict[str, Any]:
        """手动推进投递运行时若干轮。"""

        if self.delivery_runtime is None:
            raise RuntimeError("delivery runtime not configured")
        before = self.delivery_stats()
        for _ in range(max(1, min(int(rounds), 20))):
            await self.delivery_runtime.flush_once()
            if self.delivery_runtime.pending_count() <= 0:
                break
            await asyncio.sleep(0)
        after = self.delivery_stats()
        return {"ok": True, "before": before, "after": after}

    def runtime_status(self) -> dict[str, Any]:
        """聚合各运行部件状态，供面板和健康检查复用。"""

        agents = self.list_agents()
        bindings = self.list_bindings()
        channels = self.list_channels()
        profiles = self.list_profiles()
        cron_jobs = self.autonomy.cron.list_jobs() if self.autonomy is not None else []
        heartbeat = (
            self.autonomy.heartbeat.status()
            if self.autonomy is not None
            else {"enabled": False, "reason": "autonomy runtime not configured"}
        )
        delivery = (
            {"configured": True, **self.delivery_stats()}
            if self.delivery_queue is not None
            else {"configured": False}
        )
        inbound = (
            {"configured": True, **self.channel_runtime.stats()}
            if self.channel_runtime is not None
            else {"configured": False}
        )
        redis_status = (
            self.redis_client.health().to_dict()
            if self.redis_client is not None
            else {"enabled": False, "ok": True, "url": "", "error": ""}
        )
        postgres_status = (
            self.postgres_client.health().to_dict()
            if self.postgres_client is not None
            else {"enabled": False, "ok": True, "url": "", "error": ""}
        )
        if postgres_status.get("enabled") and postgres_status.get("ok"):
            postgres_status["schema"] = self._postgres_schema_status()
        tasks = (
            {"configured": True, **self.task_worker.stats()}
            if self.task_worker is not None
            else {"configured": False}
        )
        if tasks.get("configured"):
            tasks["persisted_lanes"] = self._session_lane_status()
            tasks["session_scheduler"] = self._session_scheduler_status()
        return {
            "agents": {
                "count": len(agents),
                "ids": [agent.id for agent in agents],
            },
            "bindings": {
                "count": len(bindings),
            },
            "channels": {
                "count": len(channels),
                "active": sum(1 for row in channels if row.get("active")),
                "items": channels,
            },
            "profiles": {
                "count": len(profiles),
                "available": sum(
                    1
                    for row in profiles
                    if row.get("has_key") and float(row.get("cooldown_remaining", 0.0)) <= 0.0
                ),
                "items": profiles,
            },
            "inbound": inbound,
            "delivery": delivery,
            "redis": redis_status,
            "postgres": postgres_status,
            "tasks": tasks,
            "heartbeat": heartbeat,
            "cron": {
                "count": len(cron_jobs),
                "enabled": sum(1 for row in cron_jobs if row.get("enabled")),
                "errored": sum(1 for row in cron_jobs if int(row.get("errors", 0) or 0) > 0),
                "items": cron_jobs,
            },
            "paths": {
                "workspace_root": str(self.settings.workspace_root),
                "workspace_exists": self.settings.workspace_root.exists(),
                "data_dir": str(self.settings.data_dir),
                "data_dir_exists": self.settings.data_dir.exists(),
                "config_dir": str(self.settings.config_dir),
                "config_dir_exists": self.settings.config_dir.exists(),
            },
            "features": {
                "web_search_enabled": self.settings.web_search_enabled,
                "web_search_provider": self.settings.web_search_provider,
                "web_search_has_key": bool(self.settings.tavily_api_key)
                if self.settings.web_search_provider == "tavily"
                else True,
                "proactive_target": {
                    "channel": self.settings.proactive_channel,
                    "account_id": self.settings.proactive_account_id,
                    "peer_id_configured": bool(self.settings.proactive_peer_id),
                    "agent_id": self.settings.proactive_agent_id,
                },
            },
        }

    def _session_scheduler_status(
        self,
        *,
        detail: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """读取 Redis session ready scheduler 的轻量状态。"""

        scheduler = getattr(self.task_queue, "session_scheduler", None)
        if scheduler is None:
            return {"configured": False, "enabled": False}
        snapshot = getattr(scheduler, "snapshot", None)
        if snapshot is None:
            return {
                "configured": True,
                "enabled": bool(getattr(scheduler, "enabled", False)),
                "namespace": str(getattr(scheduler, "namespace", "")),
            }
        try:
            data = snapshot(detail=detail, limit=limit).to_dict()
        except TypeError:
            data = snapshot().to_dict()
        except Exception as exc:
            return {
                "configured": True,
                "enabled": bool(getattr(scheduler, "enabled", False)),
                "namespace": str(getattr(scheduler, "namespace", "")),
                "error": str(exc),
            }
        data["configured"] = True
        return data

    def health_check(self) -> dict[str, Any]:
        """执行一轮轻量健康检查。"""

        status = self.runtime_status()
        checks = [
            self._health_check(
                "agents.loaded",
                bool(status["agents"]["count"]),
                "critical",
                f"{status['agents']['count']} agents loaded",
                "no agents loaded",
            ),
            self._health_check(
                "bindings.loaded",
                bool(status["bindings"]["count"]),
                "warning",
                f"{status['bindings']['count']} bindings loaded",
                "no route bindings loaded",
            ),
            self._health_check(
                "profiles.available",
                bool(status["profiles"]["available"]),
                "critical",
                f"{status['profiles']['available']} model profiles available",
                "no model profile is currently available",
            ),
            self._health_check(
                "channels.active",
                bool(status["channels"]["active"]),
                "warning",
                f"{status['channels']['active']} active channel accounts",
                "no active channel account",
            ),
            self._health_check(
                "inbound.backlog",
                int(status["inbound"].get("queued_messages", 0) or 0) < max(
                    1,
                    int(status["inbound"].get("max_concurrent_lanes", 1) or 1) * 2,
                ),
                "warning",
                f"{status['inbound'].get('queued_messages', 0)} inbound messages queued",
                f"{status['inbound'].get('queued_messages', 0)} inbound messages queued",
            ),
            self._health_check(
                "paths.workspace",
                bool(status["paths"]["workspace_exists"]),
                "critical",
                "workspace root exists",
                "workspace root is missing",
            ),
            self._health_check(
                "paths.data",
                bool(status["paths"]["data_dir_exists"]),
                "critical",
                "data directory exists",
                "data directory is missing",
            ),
            self._health_check(
                "paths.config",
                bool(status["paths"]["config_dir_exists"]),
                "critical",
                "config directory exists",
                "config directory is missing",
            ),
        ]
        delivery = status["delivery"]
        if delivery.get("configured"):
            failed = int(delivery.get("failed", 0) or 0)
            pending = int(delivery.get("pending", 0) or 0)
            checks.append(
                self._health_check(
                    "delivery.failed",
                    failed == 0,
                    "warning",
                    "no failed delivery entries",
                    f"{failed} failed delivery entries",
                )
            )
            checks.append(
                self._health_check(
                    "delivery.pending",
                    pending == 0,
                    "warning",
                    "no pending delivery backlog",
                    f"{pending} pending delivery entries",
                )
            )
        else:
            checks.append(
                self._health_check(
                    "delivery.configured",
                    False,
                    "warning",
                    "",
                    "delivery queue not configured",
                )
            )

        redis_status = status["redis"]
        if redis_status.get("enabled"):
            checks.append(
                self._health_check(
                    "redis.ping",
                    bool(redis_status.get("ok")),
                    "warning",
                    f"redis reachable in {redis_status.get('latency_ms')} ms",
                    f"redis unavailable: {redis_status.get('error', '')}",
                )
            )

        postgres_status = status["postgres"]
        if postgres_status.get("enabled"):
            checks.append(
                self._health_check(
                    "postgres.ping",
                    bool(postgres_status.get("ok")),
                    "warning",
                    f"postgres reachable in {postgres_status.get('latency_ms')} ms",
                    f"postgres unavailable: {postgres_status.get('error', '')}",
                )
            )
            schema_status = postgres_status.get("schema")
            if isinstance(schema_status, dict):
                checks.append(
                    self._health_check(
                        "postgres.schema",
                        bool(schema_status.get("ok")),
                        "warning",
                        "postgres schema matches gateway specification",
                        self._format_postgres_schema_error(schema_status),
                    )
                )

        persisted_lanes = status.get("tasks", {}).get("persisted_lanes", {})
        if persisted_lanes.get("configured"):
            stale_count = int(persisted_lanes.get("stale_count", 0) or 0)
            checks.append(
                self._health_check(
                    "tasks.session_lanes.stale",
                    stale_count == 0,
                    "warning",
                    "no stale session lanes",
                    f"{stale_count} stale session lanes require review",
                )
            )

        cron = status["cron"]
        cron_errors = int(cron.get("errored", 0) or 0)
        checks.append(
            self._health_check(
                "cron.errors",
                cron_errors == 0,
                "warning",
                "no cron jobs with consecutive errors",
                f"{cron_errors} cron jobs have consecutive errors",
            )
        )

        features = status["features"]
        if features.get("web_search_enabled"):
            checks.append(
                self._health_check(
                    "web_search.credentials",
                    bool(features.get("web_search_has_key")),
                    "warning",
                    "web search credentials configured",
                    "web search is enabled but credentials are missing",
                )
            )

        proactive = features["proactive_target"]
        proactive_channel_ready = any(
            row.get("channel") == proactive["channel"]
            and row.get("account_id") == proactive["account_id"]
            and row.get("active")
            for row in status["channels"]["items"]
        )
        checks.append(
            self._health_check(
                "proactive.target",
                proactive_channel_ready and bool(proactive.get("peer_id_configured")),
                "warning",
                "proactive target channel and peer are configured",
                "proactive target channel is inactive or peer_id is missing",
            )
        )

        severities = {row["status"] for row in checks}
        overall = "unhealthy" if "critical" in severities else "degraded" if "warning" in severities else "ok"
        return {
            "ok": overall == "ok",
            "status": overall,
            "checks": checks,
            "summary": {
                "critical": sum(1 for row in checks if row["status"] == "critical"),
                "warning": sum(1 for row in checks if row["status"] == "warning"),
                "ok": sum(1 for row in checks if row["status"] == "ok"),
            },
        }

    def _session_lane_status(self) -> dict[str, Any]:
        """读取 PostgreSQL 中最近的 session lane owner 状态。"""

        lanes = self.list_session_lanes(state="owned", limit=50)
        rows = list(lanes.get("items", []) or [])
        stale_rows = [row for row in rows if self._is_session_lane_stale(row)]
        history = self.list_session_lane_history(limit=50)
        history_rows = list(history.get("items", []) or [])
        recovery = self.session_lane_recovery_suggestions(limit=50)
        recovery_rows = list(recovery.get("items", []) or [])
        recovery_plan = self.plan_session_lane_recovery(limit=50)
        recovery_events = self.lane_recovery_events(limit=50)
        recovery_event_rows = list(recovery_events.get("items", []) or [])
        recovery_execute_preview = self.execute_session_lane_recovery(
            limit=50,
            execute=False,
            record_events=False,
        )
        return {
            "configured": bool(lanes.get("configured")),
            "count": len(rows),
            "items": rows[:6],
            "stale_count": len(stale_rows),
            "stale_items": stale_rows[:6],
            "history_count": len(history_rows),
            "history_items": history_rows[:6],
            "recovery_suggestion_count": len(recovery_rows),
            "recovery_suggestions": recovery_rows[:6],
            "recovery_plan": {
                "dry_run": bool(recovery_plan.get("dry_run")),
                "candidate_count": recovery_plan.get("candidate_count", 0),
                "action_count": recovery_plan.get("action_count", 0),
                "skipped_count": recovery_plan.get("skipped_count", 0),
                "actions": list(recovery_plan.get("actions", []) or [])[:6],
            },
            "recovery_execution": {
                "dry_run": bool(recovery_execute_preview.get("dry_run")),
                "executed": bool(recovery_execute_preview.get("executed")),
                "released_count": recovery_execute_preview.get("released_count", 0),
                "failed_count": recovery_execute_preview.get("failed_count", 0),
            },
            "recovery_event_count": len(recovery_event_rows),
            "recovery_events": recovery_event_rows[:6],
        }

    @staticmethod
    def _is_session_lane_stale(lane: dict[str, Any], *, now: float | None = None) -> bool:
        """根据 renewed_at + ttl_seconds 判断持久 lane 状态是否过期。"""

        try:
            renewed_at = float(lane.get("renewed_at", 0.0) or 0.0)
            ttl_seconds = int(lane.get("ttl_seconds", 0) or 0)
        except (TypeError, ValueError):
            return False
        if renewed_at <= 0 or ttl_seconds <= 0:
            return False
        current = time.time() if now is None else float(now)
        return current >= renewed_at + ttl_seconds

    @staticmethod
    def _coerce_float(value: Any) -> float:
        """把持久状态中的数字字段安全转成 float。"""

        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _coerce_int(value: Any) -> int:
        """把持久状态中的数字字段安全转成 int。"""

        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def get_source(self, kind: str) -> dict[str, Any]:
        """读取指定配置源文件的原始内容。"""

        if kind == "agents":
            rows = self._state_repo_list("agents", limit=1000)
            if rows:
                return {"agents": self._normalize_agents_rows(rows)}
        if kind == "bindings":
            rows = self._state_repo_list("bindings", limit=1000)
            if rows:
                return {"bindings": self._normalize_bindings_rows(rows)}
        if kind == "profiles":
            rows = self._state_repo_list("profiles", limit=1000)
            if rows:
                return {"profiles": self._normalize_profiles_rows(rows)}
        if kind == "channels":
            rows = self._state_repo_list("channels", limit=1000)
            if rows:
                return {"channels": self._normalize_channels_rows(rows)}
        readers = {
            "agents": read_agents_source,
            "bindings": read_bindings_source,
            "profiles": read_profiles_source,
            "channels": read_channels_source,
        }
        reader = readers.get(kind)
        if reader is None:
            raise ValueError(f"unknown source kind: {kind}")
        return reader(self.settings)

    def _require_delivery_queue(self) -> DeliveryQueue:
        """确保当前控制面已经接入投递队列。"""

        if self.delivery_queue is None:
            raise RuntimeError("delivery queue not configured")
        return self.delivery_queue

    def _require_task_queue(self) -> LocalTaskQueue:
        """确保当前控制面已经接入后台任务队列。"""

        if self.task_queue is None:
            raise RuntimeError("task queue not configured")
        return self.task_queue

    @staticmethod
    def _normalize_task_statuses(status: str) -> list[TaskStatus] | None:
        """把控制面传入的任务状态过滤条件规范化。"""

        raw = (status or "all").strip().lower()
        if raw in {"", "all", "any"}:
            return None
        allowed: set[TaskStatus] = {
            "pending",
            "running",
            "retrying",
            "done",
            "failed",
            "cancelled",
        }
        statuses = [item.strip().lower() for item in raw.split(",") if item.strip()]
        invalid = [item for item in statuses if item not in allowed]
        if invalid:
            raise ValueError(f"unsupported task status: {', '.join(invalid)}")
        return [item for item in statuses if item in allowed]  # type: ignore[list-item]

    @staticmethod
    def _task_to_dict(task: TaskInstance, *, include_payload: bool = False) -> dict[str, Any]:
        """把任务实例转成控制面响应结构。"""

        payload_preview = GatewayControlPlane._task_payload_preview(task.payload)
        row: dict[str, Any] = {
            "id": task.id,
            "task_type": task.task_type,
            "source": task.source,
            "status": task.status,
            "agent_id": task.agent_id,
            "session_key": task.session_key,
            "priority": task.priority,
            "idempotency_key": task.idempotency_key,
            "payload_preview": payload_preview,
            "result_preview": task.result_preview,
            "error": task.error,
            "retry_count": task.retry_count,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "metadata": task.metadata,
        }
        if include_payload:
            row["payload"] = task.payload
        return row

    @staticmethod
    def _task_payload_preview(payload: dict[str, Any]) -> str:
        """生成适合 Dashboard 展示的任务载荷摘要，避免暴露原始 Python repr。"""

        if not payload:
            return ""
        text = payload.get("text")
        if text:
            return str(text)[:200]
        job_id = payload.get("job_id")
        scheduled_at = payload.get("scheduled_at")
        if job_id and scheduled_at:
            return f"任务 {job_id} · 调度时间 {GatewayControlPlane._format_epoch_time(scheduled_at)}"
        if job_id:
            return f"任务 {job_id}"
        return json.dumps(
            GatewayControlPlane._format_nested_time_values(payload),
            ensure_ascii=False,
            default=str,
        )[:200]

    @staticmethod
    def _format_nested_time_values(value: Any, key: str = "") -> Any:
        """递归格式化任务载荷中的时间字段，供预览文本使用。"""

        if isinstance(value, dict):
            return {
                item_key: GatewayControlPlane._format_nested_time_values(item_value, item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [GatewayControlPlane._format_nested_time_values(item) for item in value]
        if GatewayControlPlane._is_time_field_name(key):
            formatted = GatewayControlPlane._format_epoch_time(value)
            return formatted or value
        return value

    @staticmethod
    def _is_time_field_name(key: str) -> bool:
        normalized = str(key or "").lower()
        return normalized.endswith("_at") or normalized.endswith("_time")

    @staticmethod
    def _format_epoch_time(value: Any) -> str:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return ""
        if timestamp <= 0:
            return ""
        if timestamp > 100000000000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp).strftime("%Y年%m月%d日 %H时%M分")

    @staticmethod
    def _health_check(
        name: str,
        passed: bool,
        failure_status: str,
        ok_message: str,
        failure_message: str,
    ) -> dict[str, str]:
        """构造单条健康检查结果。"""

        return {
            "name": name,
            "status": "ok" if passed else failure_status,
            "message": ok_message if passed else failure_message,
        }

    def _postgres_schema_status(self) -> dict[str, Any]:
        """返回 PostgreSQL schema drift 检查结果。"""

        try:
            result = check_postgres_schema(
                url=self.settings.postgres_url,
                connect_timeout_seconds=self.settings.postgres_connect_timeout_seconds,
            )
            return result.to_dict()
        except Exception as exc:
            return {
                "ok": False,
                "missing_tables": [],
                "missing_columns": {},
                "type_mismatches": {},
                "error": str(exc),
            }

    @staticmethod
    def _format_postgres_schema_error(schema_status: dict[str, Any]) -> str:
        """把 schema drift 结果压缩成健康检查消息。"""

        if schema_status.get("error"):
            return f"postgres schema check failed: {schema_status.get('error')}"
        missing_columns = sum(
            len(columns)
            for columns in dict(schema_status.get("missing_columns", {})).values()
        )
        type_mismatches = sum(
            len(columns)
            for columns in dict(schema_status.get("type_mismatches", {})).values()
        )
        return (
            "postgres schema drift detected: "
            f"missing_tables={len(schema_status.get('missing_tables', []))}, "
            f"missing_columns={missing_columns}, "
            f"type_mismatches={type_mismatches}"
        )

    @staticmethod
    def _normalize_delivery_state(
        state: str,
        *,
        allow_all: bool,
        allow_any: bool = False,
    ) -> str:
        """规范化投递状态过滤条件。"""

        normalized = str(state or "").strip().lower() or "pending"
        allowed = {"pending", "retrying", "failed"}
        if allow_all:
            allowed.add("all")
        if allow_any:
            allowed.add("any")
        if normalized not in allowed:
            raise ValueError(f"state must be one of: {', '.join(sorted(allowed))}")
        return normalized

    @staticmethod
    def _delivery_entry_to_dict(
        entry: QueuedDelivery,
        state: str,
        *,
        include_text: bool,
    ) -> dict[str, Any]:
        """把队列中的投递对象转换成控制面可消费结构。"""

        now = time.time()
        text = entry.text if include_text else ""
        return {
            "id": entry.id,
            "state": state,
            "channel": entry.channel,
            "to": entry.to,
            "text": text,
            "text_preview": " ".join(entry.text.split())[:200],
            "text_length": len(entry.text),
            "retry_count": entry.retry_count,
            "last_error": entry.last_error,
            "enqueued_at": entry.enqueued_at,
            "next_retry_at": entry.next_retry_at,
            "retry_ready": state == "failed" or not entry.next_retry_at or entry.next_retry_at <= now,
            "next_retry_in_seconds": (
                round(max(0.0, entry.next_retry_at - now), 1)
                if entry.next_retry_at
                else 0.0
            ),
            "metadata": entry.metadata,
        }

    def add_binding(self, binding: Binding) -> Binding:
        """新增一条 binding 到当前路由表。"""

        binding.agent_id = normalize_agent_id(binding.agent_id)
        self.bindings.add(binding)
        return binding

    def set_agent(
        self,
        *,
        agent_id: str,
        name: str | None = None,
        personality: str | None = None,
        model: str | None = None,
        dm_scope: str | None = None,
        extra_system: str | None = None,
        tool_policy_mode: str | None = None,
        tool_names: list[str] | None = None,
        memory_enabled: bool | None = None,
        memory_auto_recall: bool | None = None,
        memory_top_k: int | None = None,
        prompt_dir: str | None = None,
        use_global_prompt_files: bool | None = None,
        skills_enabled: bool | None = None,
    ) -> AgentConfig:
        """创建或更新 Agent 配置，并立即重载到运行时。"""

        normalized = normalize_agent_id(agent_id)
        payload = self.get_source("agents")
        rows = [row for row in payload.get("agents", []) if isinstance(row, dict)]
        existing_index, existing = self._find_agent_row(rows, normalized)
        row = dict(existing or {})
        tool_policy = dict(row.get("tool_policy", {}) if isinstance(row.get("tool_policy"), dict) else {})
        memory_policy = dict(row.get("memory_policy", {}) if isinstance(row.get("memory_policy"), dict) else {})
        prompt_policy = dict(row.get("prompt_policy", {}) if isinstance(row.get("prompt_policy"), dict) else {})
        row["id"] = normalized
        row["name"] = name if name is not None else str(row.get("name", normalized)) or normalized
        if personality is not None:
            row["personality"] = personality
        row.setdefault("personality", "")
        if model is not None:
            row["model"] = model
        row.setdefault("model", "")
        if dm_scope is not None:
            row["dm_scope"] = dm_scope
        row.setdefault("dm_scope", "per-peer")
        if extra_system is not None:
            row["extra_system"] = extra_system
        row.setdefault("extra_system", "")
        if tool_policy_mode is not None:
            tool_policy["mode"] = tool_policy_mode
        if tool_names is not None:
            tool_policy["tool_names"] = [str(name) for name in tool_names if str(name).strip()]
        if memory_enabled is not None:
            memory_policy["enabled"] = memory_enabled
        if memory_auto_recall is not None:
            memory_policy["auto_recall"] = memory_auto_recall
        if memory_top_k is not None:
            memory_policy["top_k"] = max(1, int(memory_top_k))
        if prompt_dir is not None:
            prompt_policy["prompt_dir"] = prompt_dir
        if use_global_prompt_files is not None:
            prompt_policy["use_global_files"] = use_global_prompt_files
        if skills_enabled is not None:
            prompt_policy["skills_enabled"] = skills_enabled
        row["tool_policy"] = {
            "mode": str(tool_policy.get("mode", "all") or "all"),
            "tool_names": [str(name) for name in tool_policy.get("tool_names", []) if str(name).strip()],
        }
        row["memory_policy"] = {
            "enabled": bool(memory_policy.get("enabled", True)),
            "auto_recall": bool(memory_policy.get("auto_recall", True)),
            "top_k": max(1, int(memory_policy.get("top_k", 3) or 3)),
        }
        row["prompt_policy"] = {
            "prompt_dir": str(prompt_policy.get("prompt_dir", "")),
            "use_global_files": bool(prompt_policy.get("use_global_files", True)),
            "skills_enabled": bool(prompt_policy.get("skills_enabled", True)),
        }
        candidate = AgentConfig(
            id=normalized,
            name=str(row["name"]),
            personality=str(row["personality"]),
            model=str(row["model"]),
            dm_scope=str(row["dm_scope"]),
            extra_system=str(row["extra_system"]),
            tool_policy_mode=str(row["tool_policy"]["mode"]),
            tool_names=tuple(str(name) for name in row["tool_policy"]["tool_names"]),
            memory_enabled=bool(row["memory_policy"]["enabled"]),
            memory_auto_recall=bool(row["memory_policy"]["auto_recall"]),
            memory_top_k=int(row["memory_policy"]["top_k"]),
            prompt_dir=str(row["prompt_policy"]["prompt_dir"]),
            use_global_prompt_files=bool(row["prompt_policy"]["use_global_files"]),
            skills_enabled=bool(row["prompt_policy"]["skills_enabled"]),
        )
        issues = self.validate_agent(candidate)
        if issues:
            raise ValueError("; ".join(issues))
        before = dict(existing) if existing is not None else None
        self._state_repo_upsert("agents", row)
        self._write_rows(self.settings.agents_config_file, "agents", rows, existing_index, row)
        self.reload_agents()
        agent = self.agents.get(normalized)
        if agent is None:
            raise RuntimeError(f"agent '{normalized}' was not reloaded")
        self._record_config_audit(
            entity_type="agent",
            entity_id=normalized,
            action="set",
            before=before,
            after=row,
            metadata={"source": "control_plane.set_agent"},
        )
        return agent

    def generate_agent_template(
        self,
        *,
        agent_id: str,
        name: str = "",
        capability_tags: list[str] | None = None,
        use_global_prompt_files: bool = True,
        memory_enabled: bool = True,
        skills_enabled: bool = True,
        write_files: bool = True,
    ) -> dict[str, Any]:
        """生成一个新的 Agent 模板，并按需落地提示词文件。"""

        template = build_agent_template(
            agent_id,
            name=name,
            capability_tags=capability_tags or [],
            use_global_prompt_files=use_global_prompt_files,
            memory_enabled=memory_enabled,
            skills_enabled=skills_enabled,
            tools=self.tools,
        )
        written_files = (
            materialize_agent_template(self.settings.workspace_root, template)
            if write_files
            else []
        )
        return {
            "agent": template.agent,
            "prompt_files": template.prompt_files,
            "written_files": written_files,
        }

    def remove_agent(self, agent_id: str) -> bool:
        """删除一个 Agent，同时做必要的引用保护。"""

        normalized = normalize_agent_id(agent_id)
        rows = [row for row in self.get_source("agents").get("agents", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_agent_row(rows, normalized)
        if existing_index < 0:
            return False
        if len(rows) <= 1:
            raise RuntimeError("cannot remove the last agent")
        if any(binding.agent_id == normalized for binding in self.bindings.list_all()):
            raise RuntimeError(f"agent '{normalized}' is still referenced by bindings")
        if normalize_agent_id(self.settings.proactive_agent_id) == normalized:
            raise RuntimeError(f"agent '{normalized}' is configured as proactive agent")
        before = dict(rows[existing_index])
        del rows[existing_index]
        self._state_repo_delete("agents", normalized)
        write_json_atomic(self.settings.agents_config_file, {"agents": rows})
        self.reload_agents()
        self._record_config_audit(
            entity_type="agent",
            entity_id=normalized,
            action="remove",
            before=before,
            after={},
            metadata={"source": "control_plane.remove_agent"},
        )
        return True

    def remove_binding(self, agent_id: str, match_key: str, match_value: str) -> bool:
        """删除一条路由绑定。"""

        return self.bindings.remove(normalize_agent_id(agent_id), match_key, match_value)

    def save_bindings(self) -> int:
        """把当前 bindings 持久化到 PostgreSQL，并把 JSON 文件作为 fallback/audit。"""

        bindings = self.bindings.list_all()
        for binding in bindings:
            self._state_repo_upsert(
                "bindings",
                {
                    "agent_id": binding.agent_id,
                    "tier": binding.tier,
                    "match_key": binding.match_key,
                    "match_value": binding.match_value,
                    "priority": binding.priority,
                },
            )
        save_bindings(self.settings, bindings)
        self._record_config_audit(
            entity_type="bindings",
            entity_id="all",
            action="save",
            after={"count": len(bindings)},
            metadata={"source": "control_plane.save_bindings"},
        )
        return len(bindings)

    def save_agents(self) -> int:
        """把当前 agents 持久化到 PostgreSQL，并把 JSON 文件作为 fallback/audit。"""

        agents = self.agents.list()
        for agent in agents:
            self._state_repo_upsert("agents", agent.manifest_row())
        save_agents(self.settings, agents)
        self._record_config_audit(
            entity_type="agents",
            entity_id="all",
            action="save",
            after={"count": len(agents)},
            metadata={"source": "control_plane.save_agents"},
        )
        return len(agents)

    def save_profiles(self) -> int:
        """把当前 profiles 持久化到 PostgreSQL，并把 JSON 文件作为 fallback/audit。"""

        profiles = list(self.profiles.profiles)
        for profile in profiles:
            self._state_repo_upsert(
                "profiles",
                {
                    "name": profile.name,
                    "provider": profile.provider,
                    "api_key": profile.api_key,
                    "base_url": profile.base_url,
                },
            )
        save_auth_profiles(self.settings, profiles)
        self._record_config_audit(
            entity_type="profiles",
            entity_id="all",
            action="save",
            after={"count": len(profiles)},
            metadata={"source": "control_plane.save_profiles"},
        )
        return len(profiles)

    def save_channels(self) -> int:
        """把当前通道账号配置持久化到 PostgreSQL，并把 JSON 文件作为 fallback/audit。"""

        accounts = list(self.channels.accounts)
        for account in accounts:
            self._state_repo_upsert(
                "channels",
                {
                    "channel": account.channel,
                    "account_id": account.account_id,
                    "enabled": True,
                    "label": account.label,
                    "token": account.token,
                    "config": account.config,
                },
            )
        save_channel_accounts(self.settings, accounts)
        self._record_config_audit(
            entity_type="channels",
            entity_id="all",
            action="save",
            after={"count": len(accounts)},
            metadata={"source": "control_plane.save_channels"},
        )
        return len(accounts)

    def set_profile(
        self,
        *,
        name: str,
        provider: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        base_url: str | None = None,
        base_url_env: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新一个模型认证 profile。"""

        if api_key and api_key_env:
            raise ValueError("api_key and api_key_env are mutually exclusive")
        if base_url and base_url_env:
            raise ValueError("base_url and base_url_env are mutually exclusive")
        rows = [row for row in self.get_source("profiles").get("profiles", []) if isinstance(row, dict)]
        existing_index, existing = self._find_profile_row(rows, name)
        row = dict(existing or {})
        row["name"] = name
        if provider is not None:
            row["provider"] = provider
        row.setdefault("provider", "anthropic")
        self._apply_secret_field(row, "api_key", api_key, api_key_env)
        self._apply_secret_field(row, "base_url", base_url, base_url_env)
        self._state_repo_upsert(
            "profiles",
            {
                "name": row["name"],
                "provider": row["provider"],
                "api_key": row.get("api_key", ""),
                "api_key_env": row.get("api_key_env", ""),
                "base_url": row.get("base_url", ""),
                "base_url_env": row.get("base_url_env", ""),
            },
        )
        self._write_rows(self.settings.profiles_config_file, "profiles", rows, existing_index, row)
        snapshot = self.reload_profiles()
        return self._find_profile_snapshot(snapshot, name)

    def remove_profile(self, name: str) -> bool:
        """删除一个 profile，并刷新运行态。"""

        rows = [row for row in self.get_source("profiles").get("profiles", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_profile_row(rows, name)
        if existing_index < 0:
            return False
        if len(rows) <= 1:
            raise RuntimeError("cannot remove the last profile")
        del rows[existing_index]
        self._state_repo_delete("profiles", name)
        write_json_atomic(self.settings.profiles_config_file, {"profiles": rows})
        self.reload_profiles()
        return True

    def reload_bindings(self) -> int:
        """从磁盘重载全部 bindings。"""

        bindings = load_bindings(self.settings)
        if self.state_repository is not None:
            repo_bindings = self.get_source("bindings").get("bindings", [])
            if repo_bindings:
                bindings = [
                    Binding(
                        agent_id=str(row.get("agent_id", "")),
                        tier=int(row.get("tier", 0)),
                        match_key=str(row.get("match_key", "")),
                        match_value=str(row.get("match_value", "")),
                        priority=int(row.get("priority", 0)),
                    )
                    for row in repo_bindings
                    if isinstance(row, dict) and row.get("agent_id") and row.get("match_key")
                ]
        for binding in bindings:
            binding.agent_id = normalize_agent_id(binding.agent_id)
        self.bindings.replace_all(bindings)
        return len(bindings)

    def reload_agents(self) -> list[AgentConfig]:
        """从磁盘重载全部 agents。"""

        agents = load_agents(self.settings)
        if self.state_repository is not None:
            repo_agents = self.get_source("agents").get("agents", [])
            if repo_agents:
                agents = [
                    AgentConfig(
                        id=str(row.get("id", "")),
                        name=str(row.get("name", "")),
                        personality=str(row.get("personality", "")),
                        model=str(row.get("model", "")),
                        dm_scope=str(row.get("dm_scope", "per-peer")),
                        extra_system=str(row.get("extra_system", "")),
                        tool_policy_mode=str(
                            row.get("tool_policy", {}).get("mode", "all")
                            if isinstance(row.get("tool_policy"), dict)
                            else "all"
                        ),
                        tool_names=tuple(
                            str(name)
                            for name in (
                                row.get("tool_policy", {}).get("tool_names", [])
                                if isinstance(row.get("tool_policy"), dict)
                                else []
                            )
                        ),
                        memory_enabled=bool(
                            row.get("memory_policy", {}).get("enabled", True)
                            if isinstance(row.get("memory_policy"), dict)
                            else True
                        ),
                        memory_auto_recall=bool(
                            row.get("memory_policy", {}).get("auto_recall", True)
                            if isinstance(row.get("memory_policy"), dict)
                            else True
                        ),
                        memory_top_k=max(
                            1,
                            int(
                                row.get("memory_policy", {}).get("top_k", 3)
                                if isinstance(row.get("memory_policy"), dict)
                                else 3
                            ),
                        ),
                        prompt_dir=str(
                            row.get("prompt_policy", {}).get("prompt_dir", "")
                            if isinstance(row.get("prompt_policy"), dict)
                            else ""
                        ),
                        use_global_prompt_files=bool(
                            row.get("prompt_policy", {}).get("use_global_files", True)
                            if isinstance(row.get("prompt_policy"), dict)
                            else True
                        ),
                        skills_enabled=bool(
                            row.get("prompt_policy", {}).get("skills_enabled", True)
                            if isinstance(row.get("prompt_policy"), dict)
                            else True
                        ),
                    )
                    for row in repo_agents
                    if isinstance(row, dict) and row.get("id") and row.get("name")
                ]
        if not agents:
            raise RuntimeError("No agents loaded from config")
        self.agents.replace_all(agents)
        return self.agents.list()

    def reload_profiles(self) -> list[dict[str, Any]]:
        """从磁盘重载全部 profiles，并保留旧冷却状态。"""

        profiles = load_auth_profiles(self.settings)
        if self.state_repository is not None:
            repo_profiles = self.get_source("profiles").get("profiles", [])
            if repo_profiles:
                profiles = [
                    AuthProfile(
                        name=str(row.get("name", "primary")),
                        provider=str(row.get("provider", "anthropic")),
                        api_key=self._resolve_secret_value(row, "api_key"),
                        base_url=self._resolve_secret_value(row, "base_url"),
                    )
                    for row in repo_profiles
                    if isinstance(row, dict) and row.get("name")
                ]
        self.profiles.replace_profiles(profiles)
        return self.profiles.snapshot()

    async def set_channel(
        self,
        *,
        channel: str,
        account_id: str,
        enabled: bool | None = None,
        label: str | None = None,
        token: str | None = None,
        token_env: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建或更新一个通道账号，并重建通道实例。"""

        if token and token_env:
            raise ValueError("token and token_env are mutually exclusive")
        normalized_channel = channel.strip().lower()
        if normalized_channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"unsupported channel: {normalized_channel}")
        if not account_id.strip():
            raise ValueError("account_id is required")
        rows = [row for row in self.get_source("channels").get("channels", []) if isinstance(row, dict)]
        existing_index, existing = self._find_channel_row(rows, normalized_channel, account_id)
        row = dict(existing or {})
        row["channel"] = normalized_channel
        row["account_id"] = account_id
        if enabled is not None:
            row["enabled"] = enabled
        row.setdefault("enabled", True)
        if label is not None:
            row["label"] = label
        row.setdefault("label", "")
        self._apply_secret_field(row, "token", token, token_env)
        merged_config = self._merge_channel_config(
            row.get("config", {}) if isinstance(row.get("config"), dict) else {},
            config or {},
        )
        row["config"] = merged_config
        self._state_repo_upsert(
            "channels",
            {
                "channel": row["channel"],
                "account_id": row["account_id"],
                "enabled": row.get("enabled", True),
                "label": row.get("label", ""),
                "token": row.get("token", ""),
                "token_env": row.get("token_env", ""),
                "config": row.get("config", {}),
            },
        )
        self._write_rows(self.settings.channels_config_file, "channels", rows, existing_index, row)
        await self.reload_channels()
        return self._find_channel_descriptor(normalized_channel, account_id)

    async def remove_channel(self, channel: str, account_id: str) -> bool:
        """删除一个通道账号，并刷新通道管理器。"""

        normalized_channel = channel.strip().lower()
        rows = [row for row in self.get_source("channels").get("channels", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_channel_row(rows, normalized_channel, account_id)
        if existing_index < 0:
            return False
        if (
            normalized_channel == self.settings.proactive_channel.strip().lower()
            and account_id == self.settings.proactive_account_id
        ):
            raise RuntimeError("cannot remove the configured proactive channel account")
        del rows[existing_index]
        self._state_repo_delete("channels", f"{normalized_channel}\x1f{account_id}")
        write_json_atomic(self.settings.channels_config_file, {"channels": rows})
        await self.reload_channels()
        return True

    async def reload_channels(self) -> list[str]:
        """从磁盘重载全部通道，并同步替换依赖它们的运行时。"""

        next_manager = build_channel_manager(
            self.settings,
            load_channel_accounts(self.settings),
            state_read_repository=self.state_repository,
            state_write_repository=self.state_write_repository,
        )
        if self.state_repository is not None:
            repo_channels = self.get_source("channels").get("channels", [])
            if repo_channels:
                next_manager = build_channel_manager(
                    self.settings,
                    [
                        ChannelAccount(
                            channel=str(row.get("channel", "")).strip().lower(),
                            account_id=str(row.get("account_id", "")),
                            label=str(row.get("label", "")),
                            token=self._resolve_secret_value(row, "token"),
                            config=self._resolve_channel_config(row),
                        )
                        for row in repo_channels
                        if isinstance(row, dict)
                        and row.get("channel")
                        and row.get("account_id")
                        and bool(row.get("enabled", True))
                    ],
                    state_read_repository=self.state_repository,
                    state_write_repository=self.state_write_repository,
                )
        if self.channel_runtime is not None:
            await self.channel_runtime.restart(next_manager)
            self.channels.replace_from(next_manager)
            if hasattr(self.channel_runtime, "channels"):
                self.channel_runtime.channels = self.channels
            delivery_runtime = getattr(self.channel_runtime, "delivery_runtime", None)
            if delivery_runtime is not None:
                delivery_runtime.channels = self.channels
        else:
            self.channels.close_all()
            self.channels.replace_from(next_manager)
        if self.autonomy is not None:
            self.autonomy.set_channels(self.channels)
        if self.feishu_long_connection_runtime is not None:
            await self.feishu_long_connection_runtime.restart(self.channels)
        return self.channels.list_channels()

    @staticmethod
    def _find_agent_row(rows: list[dict[str, Any]], agent_id: str) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if normalize_agent_id(str(row.get("id", ""))) == agent_id:
                return index, row
        return -1, None

    @staticmethod
    def _find_profile_row(rows: list[dict[str, Any]], name: str) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if str(row.get("name", "")) == name:
                return index, row
        return -1, None

    @staticmethod
    def _find_channel_row(
        rows: list[dict[str, Any]],
        channel: str,
        account_id: str,
    ) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if str(row.get("channel", "")).strip().lower() == channel and str(
                row.get("account_id", "")
            ) == account_id:
                return index, row
        return -1, None

    @staticmethod
    def _write_rows(
        path,
        root_key: str,
        rows: list[dict[str, Any]],
        existing_index: int,
        row: dict[str, Any],
    ) -> None:
        if existing_index >= 0:
            rows[existing_index] = row
        else:
            rows.append(row)
        write_json_atomic(path, {root_key: rows})

    @staticmethod
    def _apply_secret_field(
        row: dict[str, Any],
        field: str,
        literal_value: str | None,
        env_value: str | None,
    ) -> None:
        env_key = f"{field}_env"
        if env_value is not None:
            row.pop(field, None)
            if env_value:
                row[env_key] = env_value
            else:
                row.pop(env_key, None)
        if literal_value is not None:
            row.pop(env_key, None)
            if literal_value:
                row[field] = literal_value
            else:
                row.pop(field, None)

    @staticmethod
    def _merge_channel_config(
        current: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        for key, value in patch.items():
            if key.endswith("_env"):
                base_key = key[:-4]
                merged.pop(base_key, None)
                if value in ("", None):
                    merged.pop(key, None)
                else:
                    merged[key] = value
                continue
            env_key = f"{key}_env"
            merged.pop(env_key, None)
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _find_profile_snapshot(snapshot: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for row in snapshot:
            if row.get("name") == name:
                return row
        raise RuntimeError(f"profile '{name}' was not reloaded")

    def _find_channel_descriptor(self, channel: str, account_id: str) -> dict[str, Any]:
        for row in self.list_channels():
            if row.get("channel") == channel and row.get("account_id") == account_id:
                return row
        raise RuntimeError(f"channel '{channel}/{account_id}' was not reloaded")

    @staticmethod
    def _normalize_agents_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "id": row.get("id", ""),
                    "name": row.get("name", ""),
                    "personality": row.get("personality", ""),
                    "model": row.get("model", ""),
                    "dm_scope": row.get("dm_scope", "per-peer"),
                    "extra_system": row.get("extra_system", ""),
                    "tool_policy": row.get("tool_policy", {"mode": "all", "tool_names": []}),
                    "memory_policy": row.get("memory_policy", {"enabled": True, "auto_recall": True, "top_k": 3}),
                    "prompt_policy": row.get("prompt_policy", {"prompt_dir": "", "use_global_files": True, "skills_enabled": True}),
                }
            )
        return normalized

    @staticmethod
    def _normalize_bindings_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            payload = dict(row)
            payload.pop("key", None)
            normalized.append(payload)
        return normalized

    @staticmethod
    def _normalize_profiles_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append(dict(row))
        return normalized

    @staticmethod
    def _normalize_channels_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            payload = dict(row)
            payload.pop("key", None)
            normalized.append(payload)
        return normalized

    @staticmethod
    def _resolve_secret_value(row: dict[str, Any], field: str) -> str:
        env_name = str(row.get(f"{field}_env", "") or "")
        if env_name:
            return os.getenv(env_name, "")
        return str(row.get(field, "") or "")

    @classmethod
    def _resolve_channel_config(cls, row: dict[str, Any]) -> dict[str, Any]:
        config = dict(row.get("config", {}) if isinstance(row.get("config"), dict) else {})
        for key, value in list(config.items()):
            if key.endswith("_env") and isinstance(value, str):
                config[key[:-4]] = os.getenv(value, "")
        return config
