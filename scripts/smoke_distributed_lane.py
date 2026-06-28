#!/usr/bin/env python3
"""Smoke test for the distributed inbound lane path.

This script intentionally avoids real model and Feishu calls. It verifies the
infrastructure contract behind the final distributed lane design:

RabbitMQ partitioned inbound refs -> TaskWorkerRuntime pool -> Redis lane
ownership -> same-session serialization -> broker backlog drained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.infra.postgres_client import PostgresClient
from agent_gateway.runtime.infra.rabbitmq import RabbitMQInboundTaskBroker
from agent_gateway.config import GatewaySettings
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.runtime.domain.models import AgentConfig, Binding
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.runtime.execution.resilience import AuthProfile, ProfileManager
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.state.postgres import (
    PostgresReadRepository,
    PostgresWriteRepository,
    initialize_postgres_schema,
)
from agent_gateway.runtime.tasks import (
    LaneOwnerToken,
    LocalTaskQueue,
    LocalTaskStore,
    RedisLaneCoordinator,
    TaskWorkerRuntime,
)
from scripts.load_test_gateway import (
    DEFAULT_REPORT_DIR,
    build_result,
    run_inbound_rabbitmq,
    write_reports,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run distributed lane smoke checks.")
    parser.add_argument(
        "--scenario",
        choices=(
            "inbound",
            "ttl-takeover",
            "worker-crash",
            "broker-unavailable",
            "primary-unavailable",
            "postgres-lane",
            "readiness",
        ),
        default="inbound",
        help="smoke scenario to run",
    )
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--agent-delay-ms", type=float, default=5.0)
    parser.add_argument("--session-count", type=int, default=4)
    parser.add_argument("--rabbitmq-url", default="amqp://admin:admin123@127.0.0.1:5672/")
    parser.add_argument("--rabbitmq-exchange", default="agent_gateway.inbound.smoke")
    parser.add_argument("--rabbitmq-queue-prefix", default="agent_gateway.inbound.smoke.partition")
    parser.add_argument("--rabbitmq-dead-letter-exchange", default="agent_gateway.inbound.smoke.dlx")
    parser.add_argument("--rabbitmq-dead-letter-queue", default="agent_gateway.inbound.smoke.dead")
    parser.add_argument("--rabbitmq-partitions", type=int, default=4)
    parser.add_argument("--rabbitmq-prefetch", type=int, default=1)
    parser.add_argument("--rabbitmq-connect-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--redis-socket-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--postgres-url", default="postgresql://postgres:postgres@127.0.0.1:5432/postgres")
    parser.add_argument("--postgres-connect-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--lane-ttl-seconds", type=int, default=30)
    parser.add_argument("--lane-namespace", default="gateway:smoke:lane")
    parser.add_argument("--takeover-session-key", default="smoke-session-ttl-takeover")
    parser.add_argument("--takeover-wait-seconds", type=float, default=0.0)
    parser.add_argument("--work-dir", type=Path, default=Path(".load-test-tmp"))
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--basename", default="")
    return parser.parse_args(argv)


class FailingInboundBroker:
    """Minimal broker that simulates RabbitMQ publish/consume unavailability."""

    enabled = True
    partitions = 1

    def __init__(self) -> None:
        self.publish_attempts = 0
        self.consume_attempts = 0

    def publish(self, task) -> None:
        del task
        self.publish_attempts += 1
        raise RuntimeError("simulated rabbitmq publish failure")

    def consume_once(self, partition: int, handler) -> bool:
        del partition, handler
        self.consume_attempts += 1
        return False

    def stats(self) -> dict:
        return {
            "backend": "failing-inbound-broker",
            "enabled": True,
            "messages": 0,
            "dead_letter_messages": 0,
            "publish_attempts": self.publish_attempts,
            "consume_attempts": self.consume_attempts,
        }


class SinglePayloadBroker:
    """Broker that delivers one task reference, then becomes empty."""

    enabled = True
    partitions = 1

    def __init__(self, payload: dict) -> None:
        self.payloads = [payload]
        self.acked = 0
        self.nacked = 0

    def publish(self, task) -> None:
        del task
        return None

    def consume_once(self, partition: int, handler) -> bool:
        del partition
        if not self.payloads:
            return False
        payload = self.payloads.pop(0)
        if handler(payload):
            self.acked += 1
        else:
            self.nacked += 1
            self.payloads.append(payload)
        return True

    def stats(self) -> dict:
        return {
            "backend": "single-payload-broker",
            "enabled": True,
            "messages": len(self.payloads),
            "dead_letter_messages": 0,
            "acked": self.acked,
            "nacked": self.nacked,
        }


class SmokeAutonomy:
    """Minimal autonomy facade used by readiness smoke."""

    class _Heartbeat:
        def status(self) -> dict:
            return {"enabled": False, "reason": "smoke readiness"}

    class _Cron:
        def list_jobs(self) -> list[dict]:
            return []

    def __init__(self) -> None:
        self.heartbeat = self._Heartbeat()
        self.cron = self._Cron()


class SmokeChannelRuntime:
    """Minimal inbound runtime stats used by readiness smoke."""

    def stats(self) -> dict:
        return {
            "running": True,
            "global_queue_depth": 0,
            "global_queue_limit": 200,
            "lane_queue_limit": 20,
            "max_concurrent_lanes": 4,
            "active_lanes": 0,
            "running_tasks": 0,
            "lane_count": 0,
            "queued_messages": 0,
            "oldest_wait_seconds": 0.0,
            "lanes": [],
        }


class SmokeTaskWorker:
    """Minimal task worker stats used by readiness smoke."""

    def __init__(self, *, broker: dict) -> None:
        self.broker = broker

    def stats(self) -> dict:
        return {
            "running": True,
            "worker_id": "smoke-readiness-worker",
            "concurrency": 2,
            "registered_task_types": ["agent_inbound"],
            "queue": {"pending": 0, "running": 0, "retrying": 0, "failed": 0},
            "broker": self.broker,
            "session_locks": {
                "blocked_session_count": 0,
                "skip_count": 0,
                "last_blocked_sessions": [],
            },
        }


class FailingPrimaryBackend:
    """Write backend that simulates PostgreSQL reserve_task_id failure."""

    enabled = True

    def __init__(self) -> None:
        self.reserve_task_id_attempts = 0

    def reserve_task_id(
        self,
        *,
        task_id: str,
        worker_id: str,
        task_types: list[str],
        blocked_session_keys: list[str],
        now: float | None = None,
    ):
        del task_id, worker_id, task_types, blocked_session_keys, now
        self.reserve_task_id_attempts += 1
        raise RuntimeError("simulated postgres reserve_task_id failure")


def assert_broker_unavailable_result(result: dict) -> list[str]:
    """Validate broker-unavailable fallback smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    if result.get("publish_failed") is not True:
        failures.append("broker publish failure was not exercised")
    if result.get("worker_handled") is not True:
        failures.append("worker did not handle fallback task")
    if result.get("task_status_after_enqueue") != "pending":
        failures.append(
            "task was not kept pending after broker publish failure: "
            f"{result.get('task_status_after_enqueue')}"
        )
    if result.get("task_status_after_worker") != "done":
        failures.append(
            "task was not completed by polling fallback: "
            f"{result.get('task_status_after_worker')}"
        )
    if result.get("handler_calls") != 1:
        failures.append(f"handler call count mismatch: {result.get('handler_calls')}")
    if result.get("broker_consume_attempts", 0) < 1:
        failures.append("worker did not attempt broker consume before polling fallback")
    return failures


def assert_primary_unavailable_result(result: dict) -> list[str]:
    """Validate primary-store reserve_task_id fallback smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    if result.get("primary_reserve_failed") is not True:
        failures.append("primary reserve_task_id failure was not exercised")
    if result.get("worker_handled") is not True:
        failures.append("worker did not handle task after primary failure")
    if result.get("task_status_after_worker") != "done":
        failures.append(
            "task was not completed after primary failure: "
            f"{result.get('task_status_after_worker')}"
        )
    if result.get("handler_calls") != 1:
        failures.append(f"handler call count mismatch: {result.get('handler_calls')}")
    broker_stats = dict(result.get("broker_stats", {}) or {})
    if int(broker_stats.get("acked", 0) or 0) != 1:
        failures.append(f"broker payload was not acked: {broker_stats}")
    return failures


def assert_postgres_lane_result(result: dict) -> list[str]:
    """Validate PostgreSQL session_lanes real-store smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    if result.get("write_ok") is not True:
        failures.append("session lane write did not return the expected row")
    if result.get("listed_owned") is not True:
        failures.append("owned session lane was not readable from PostgreSQL")
    if result.get("stale_before_release") is not True:
        failures.append("owned session lane was not detected as stale")
    if result.get("mismatch_release") is not False:
        failures.append("owner_token mismatch unexpectedly released the lane")
    if result.get("matched_release") is not True:
        failures.append("owner_token matched release did not update the lane")
    if int(result.get("history_count", 0) or 0) < 2:
        failures.append(f"lane history events were not persisted: {result.get('history_count')}")
    released = dict(result.get("released_row", {}) or {})
    if released.get("state") != "released":
        failures.append(f"released row state mismatch: {released.get('state')}")
    metadata = dict(released.get("metadata", {}) or {})
    if metadata.get("release_reason") != result.get("release_reason"):
        failures.append(f"release metadata missing reason: {metadata}")
    if "released_at" not in metadata:
        failures.append(f"release metadata missing released_at: {metadata}")
    return failures


def assert_ttl_takeover_result(result: dict) -> list[str]:
    """Validate Redis lane TTL takeover smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    if result.get("first_owner_acquired") is not True:
        failures.append("first owner did not acquire lane")
    if result.get("second_owner_blocked_before_ttl") is not True:
        failures.append("second owner was not blocked before TTL expiry")
    if result.get("second_owner_acquired_after_ttl") is not True:
        failures.append("second owner did not acquire lane after TTL expiry")
    after = dict(result.get("after_takeover", {}) or {})
    if after.get("worker_id") != result.get("second_worker_id"):
        failures.append(
            "lane owner did not switch to second worker: "
            f"worker_id={after.get('worker_id')}"
        )
    before = dict(result.get("before_takeover", {}) or {})
    if before.get("worker_id") != result.get("first_worker_id"):
        failures.append(
            "initial lane owner was not first worker: "
            f"worker_id={before.get('worker_id')}"
        )
    if after.get("task_id") != result.get("second_task_id"):
        failures.append(f"lane owner task mismatch: task_id={after.get('task_id')}")
    if after.get("owned") is not True:
        failures.append("lane is not owned after takeover")
    return failures


def assert_worker_crash_result(result: dict) -> list[str]:
    """Validate worker-crash lane takeover smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    if result.get("old_owner_acquired") is not True:
        failures.append("old worker did not acquire lane")
    if result.get("blocked_before_ttl") is not True:
        failures.append("new worker was not blocked before TTL expiry")
    if result.get("handled_after_ttl") is not True:
        failures.append("new worker did not handle task after TTL expiry")
    if result.get("task_status_after_blocked") != "pending":
        failures.append(
            "task status changed before TTL expiry: "
            f"{result.get('task_status_after_blocked')}"
        )
    if result.get("task_status_after_takeover") != "done":
        failures.append(
            "task was not completed after takeover: "
            f"{result.get('task_status_after_takeover')}"
        )
    if result.get("handler_calls") != 1:
        failures.append(f"handler call count mismatch: {result.get('handler_calls')}")
    before = dict(result.get("before_ttl_owner", {}) or {})
    if before.get("worker_id") != result.get("old_worker_id"):
        failures.append(
            "old lane owner metadata mismatch before TTL: "
            f"worker_id={before.get('worker_id')}"
        )
    during = dict(result.get("during_handler_owner", {}) or {})
    if during.get("worker_id") != result.get("new_worker_id"):
        failures.append(
            "new lane owner metadata mismatch during handler: "
            f"worker_id={during.get('worker_id')}"
        )
    after = dict(result.get("after_completion_owner", {}) or {})
    if after.get("owned") is True:
        failures.append(f"lane was not released after completion: {after}")
    return failures


def assert_smoke_result(result: dict) -> list[str]:
    """Return a list of smoke failures; empty list means pass."""

    failures: list[str] = []
    scenario = result.get("scenario", {})
    summary = result.get("summary", {})
    context = result.get("context", {})
    requests = int(scenario.get("requests", 0) or 0)
    success = int(summary.get("success", 0) or 0)
    broker_after_consume = dict(context.get("broker_after_consume", {}) or {})
    redis_health = dict(context.get("redis_health", {}) or {})

    if success != requests:
        failures.append(f"success mismatch: success={success}, requests={requests}")
    if int(summary.get("failed", 0) or 0) != 0:
        failures.append(f"unexpected failed requests: {summary.get('failed')}")
    if context.get("lane_mode") != "redis":
        failures.append(f"lane_mode is not redis: {context.get('lane_mode')}")
    if int(context.get("max_same_session_concurrency", 0) or 0) != 1:
        failures.append(
            "same-session serialization failed: "
            f"max_same_session_concurrency={context.get('max_same_session_concurrency')}"
        )
    if int(broker_after_consume.get("messages", 0) or 0) != 0:
        failures.append(
            f"broker backlog not drained: messages={broker_after_consume.get('messages')}"
        )
    if int(broker_after_consume.get("dead_letter_messages", 0) or 0) != 0:
        failures.append(
            "broker DLQ not empty: "
            f"dead_letter_messages={broker_after_consume.get('dead_letter_messages')}"
        )
    if not bool(redis_health.get("enabled", False)):
        failures.append(f"redis is not enabled: {redis_health}")
    if redis_health.get("ok") is False:
        failures.append(f"redis health failed: {redis_health}")
    return failures


def assert_readiness_result(result: dict) -> list[str]:
    """Validate distributed lane readiness smoke result."""

    failures: list[str] = []
    if result.get("status") != "ok":
        failures.append(f"unexpected status: {result.get('status')}")
    readiness = dict(result.get("readiness", {}) or {})
    if readiness.get("ready") is not True:
        failures.append(f"readiness is not ready: {readiness.get('status')}")
    if int(readiness.get("failed", 0) or 0) != 0:
        failures.append(f"readiness failed checks: {readiness.get('failed')}")
    failed_checks = [
        str(row.get("name", ""))
        for row in list(readiness.get("checks", []) or [])
        if not bool(row.get("ok"))
    ]
    if failed_checks:
        failures.append(f"failed readiness checks: {', '.join(failed_checks)}")
    if result.get("redis_ok") is not True:
        failures.append(f"redis health failed: {result.get('redis_health')}")
    if result.get("postgres_ok") is not True:
        failures.append(f"postgres health failed: {result.get('postgres_health')}")
    rabbitmq_stats = dict(result.get("rabbitmq_stats", {}) or {})
    if result.get("rabbitmq_ok") is not True:
        failures.append(f"rabbitmq health failed: {rabbitmq_stats}")
    return failures


async def run_smoke(args: argparse.Namespace) -> tuple[dict, Path, Path]:
    samples, wall_seconds, context = await run_inbound_rabbitmq(
        requests=max(1, args.requests),
        concurrency=max(1, args.concurrency),
        agent_delay_ms=max(0.0, args.agent_delay_ms),
        work_dir=args.work_dir,
        rabbitmq_url=args.rabbitmq_url,
        rabbitmq_exchange=args.rabbitmq_exchange,
        rabbitmq_queue_prefix=args.rabbitmq_queue_prefix,
        rabbitmq_dead_letter_exchange=args.rabbitmq_dead_letter_exchange,
        rabbitmq_dead_letter_queue=args.rabbitmq_dead_letter_queue,
        rabbitmq_partitions=max(1, args.rabbitmq_partitions),
        rabbitmq_prefetch=max(1, args.rabbitmq_prefetch),
        session_count=max(1, args.session_count),
        lane_mode="redis",
        redis_url=args.redis_url,
        redis_socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
        lane_ttl_seconds=max(1, args.lane_ttl_seconds),
        lane_namespace=args.lane_namespace,
        connect_timeout_seconds=max(0.2, args.rabbitmq_connect_timeout_seconds),
    )
    result = build_result(
        scenario="distributed-lane-smoke",
        requests=max(1, args.requests),
        concurrency=max(1, args.concurrency),
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=max(0.0, args.agent_delay_ms),
        delivery_delay_ms=0.0,
        context={
            **context,
            "smoke": True,
            "uses_real_redis": True,
            "redis_url": args.redis_url,
        },
    )
    basename = args.basename or f"smoke-distributed-lane-{int(time.time())}"
    json_path, md_path = write_reports(result, args.report_dir, basename)
    return result, json_path, md_path


async def run_readiness_smoke(args: argparse.Namespace) -> dict:
    """Run a full distributed lane readiness gate against local middleware."""

    settings = GatewaySettings(
        redis_enabled=True,
        redis_url=args.redis_url,
        redis_socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
        postgres_enabled=True,
        postgres_url=args.postgres_url,
        postgres_connect_timeout_seconds=max(0.2, args.postgres_connect_timeout_seconds),
        inbound_task_queue_enabled=True,
        inbound_broker="rabbitmq",
        inbound_rabbitmq_partitions=max(1, args.rabbitmq_partitions),
        inbound_rabbitmq_prefetch=max(1, args.rabbitmq_prefetch),
        delivery_broker="rabbitmq",
        config_dir=args.work_dir / "readiness-config",
        data_dir=args.work_dir / "readiness-data",
        workspace_root=args.work_dir / "readiness-workspace",
        proactive_channel="cli",
        proactive_account_id="cli-local",
        proactive_peer_id="cli-user",
    )
    settings.ensure_directories()
    initialize_postgres_schema(
        url=settings.postgres_url,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    redis = RedisClient(
        enabled=True,
        url=settings.redis_url,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
    )
    postgres = PostgresClient(
        enabled=True,
        url=settings.postgres_url,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    redis_health = redis.health().to_dict()
    postgres_health = postgres.health().to_dict()
    inbound_broker = RabbitMQInboundTaskBroker(
        enabled=True,
        url=args.rabbitmq_url,
        exchange=args.rabbitmq_exchange,
        queue_prefix=args.rabbitmq_queue_prefix,
        dead_letter_exchange=args.rabbitmq_dead_letter_exchange,
        dead_letter_queue=args.rabbitmq_dead_letter_queue,
        partitions=max(1, args.rabbitmq_partitions),
        prefetch=max(1, args.rabbitmq_prefetch),
        connect_timeout_seconds=max(0.2, args.rabbitmq_connect_timeout_seconds),
    )
    rabbitmq_stats = inbound_broker.stats()
    rabbitmq_ok = bool(rabbitmq_stats.get("enabled")) and not rabbitmq_stats.get("error")
    reader = PostgresReadRepository(
        url=settings.postgres_url,
        enabled=True,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    writer = PostgresWriteRepository(
        url=settings.postgres_url,
        enabled=True,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    )
    session_key = f"smoke-readiness:{int(time.time() * 1000)}"
    now = time.time()
    writer.upsert(
        "session_lanes",
        {
            "session_key": session_key,
            "lane_key": f"{args.lane_namespace}:{session_key}",
            "worker_id": "smoke-readiness-worker",
            "task_id": "smoke-readiness-task",
            "owner_token": "smoke-readiness-worker:smoke-readiness-task",
            "state": "owned",
            "ttl_seconds": max(1, args.lane_ttl_seconds),
            "acquired_at": now,
            "renewed_at": now,
            "updated_at": now,
            "metadata": {"smoke": "readiness"},
        },
    )
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="SmokeMain", model="smoke-model"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    channels = ChannelManager()
    channels.accounts = [ChannelAccount(channel="cli", account_id="cli-local", label="CLI")]
    control = GatewayControlPlane(
        settings=settings,
        agents=agents,
        bindings=bindings,
        profiles=ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="smoke")]),
        channels=channels,
        autonomy=SmokeAutonomy(),
        channel_runtime=SmokeChannelRuntime(),
        delivery_queue=DeliveryQueue(settings.delivery_queue_dir),
        redis_client=redis,
        postgres_client=postgres,
        state_repository=reader,
        state_write_repository=writer,
        task_worker=SmokeTaskWorker(
            broker={
                **rabbitmq_stats,
                "messages": int(rabbitmq_stats.get("messages", 0) or 0),
                "dead_letter_messages": int(rabbitmq_stats.get("dead_letter_messages", 0) or 0),
            }
        ),
        task_queue=LocalTaskQueue(LocalTaskStore(settings.tasks_dir)),
        event_store=RuntimeEventStore(settings.events_dir),
    )
    try:
        report = control.lane_doctor(limit=20)
        result = {
            "status": "ok" if report.get("readiness", {}).get("ready") else "failed",
            "readiness": report.get("readiness", {}),
            "summary": report.get("summary", {}),
            "redis_ok": bool(redis_health.get("ok")),
            "postgres_ok": bool(postgres_health.get("ok")),
            "rabbitmq_ok": rabbitmq_ok,
            "redis_health": redis_health,
            "postgres_health": postgres_health,
            "rabbitmq_stats": rabbitmq_stats,
            "session_key": session_key,
        }
        result["failures"] = assert_readiness_result(result)
        if result["failures"]:
            result["status"] = "failed"
        return result
    finally:
        try:
            writer.delete("session_lanes", session_key)
        except Exception:
            pass
        inbound_broker.close()


async def run_ttl_takeover_smoke(args: argparse.Namespace) -> dict:
    redis = RedisClient(
        enabled=True,
        url=args.redis_url,
        socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
    )
    coordinator = RedisLaneCoordinator(redis, namespace=args.lane_namespace)
    session_key = args.takeover_session_key
    first_owner = LaneOwnerToken(worker_id="smoke-worker-old", task_id="smoke-task-old")
    second_owner = LaneOwnerToken(worker_id="smoke-worker-new", task_id="smoke-task-new")
    first = await asyncio.to_thread(
        coordinator.acquire,
        session_key,
        owner=first_owner,
        ttl_seconds=max(1, args.lane_ttl_seconds),
    )
    blocked = await asyncio.to_thread(
        coordinator.acquire,
        session_key,
        owner=second_owner,
        ttl_seconds=max(1, args.lane_ttl_seconds),
    )
    before = coordinator.inspect(session_key).to_dict()
    wait_seconds = (
        max(0.05, float(args.takeover_wait_seconds))
        if args.takeover_wait_seconds
        else max(1, args.lane_ttl_seconds) + 0.25
    )
    await asyncio.sleep(wait_seconds)
    second = await asyncio.to_thread(
        coordinator.acquire,
        session_key,
        owner=second_owner,
        ttl_seconds=max(1, args.lane_ttl_seconds),
    )
    after = coordinator.inspect(session_key).to_dict()
    if second is not None:
        await asyncio.to_thread(coordinator.release, second)
    result = {
        "status": "ok",
        "scenario": "ttl-takeover",
        "session_key": session_key,
        "lane_namespace": args.lane_namespace,
        "lane_ttl_seconds": max(1, args.lane_ttl_seconds),
        "wait_seconds": wait_seconds,
        "first_worker_id": first_owner.worker_id,
        "first_task_id": first_owner.task_id,
        "second_worker_id": second_owner.worker_id,
        "second_task_id": second_owner.task_id,
        "first_owner_acquired": first is not None,
        "second_owner_blocked_before_ttl": blocked is None,
        "second_owner_acquired_after_ttl": second is not None,
        "before_takeover": before,
        "after_takeover": after,
        "redis_health": redis.health().to_dict(),
    }
    result["failures"] = assert_ttl_takeover_result(result)
    result["status"] = "ok" if not result["failures"] else "failed"
    return result


async def run_worker_crash_smoke(args: argparse.Namespace) -> dict:
    """Exercise abandoned lane takeover through TaskWorkerRuntime execution."""

    redis = RedisClient(
        enabled=True,
        url=args.redis_url,
        socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
    )
    coordinator = RedisLaneCoordinator(redis, namespace=args.lane_namespace)
    task_dir = args.work_dir / f"worker-crash-{int(time.time())}"
    store = LocalTaskStore(task_dir)
    queue = LocalTaskQueue(store)
    session_key = f"{args.takeover_session_key}-worker-crash"
    old_worker_id = "smoke-worker-crashed"
    new_worker_id = "smoke-worker-takeover"
    old_owner = LaneOwnerToken(worker_id=old_worker_id, task_id="smoke-task-crashed")
    old_ownership = await asyncio.to_thread(
        coordinator.acquire,
        session_key,
        owner=old_owner,
        ttl_seconds=max(1, args.lane_ttl_seconds),
    )
    task = queue.enqueue(
        task_type="agent_inbound",
        source="smoke",
        session_key=session_key,
        payload={"text": "worker crash takeover"},
        metadata={"smoke": "worker-crash"},
    )
    before_ttl_owner = coordinator.inspect(session_key).to_dict()
    handler_calls = 0
    during_handler_owner: dict[str, object] = {}

    class SmokeLaneHandler:
        """Minimal production-shaped handler with lane inspection hooks."""

        def __init__(self) -> None:
            self.worker_id = new_worker_id

        def is_session_locked(self, item) -> bool:
            del item
            return coordinator.is_owned(session_key)

        def inspect_session_lane(self, item) -> dict[str, object]:
            del item
            return coordinator.inspect(session_key).to_dict()

        async def __call__(self, item) -> str:
            nonlocal handler_calls, during_handler_owner
            owner = LaneOwnerToken(worker_id=self.worker_id, task_id=item.id)
            ownership = coordinator.acquire(
                item.session_key,
                owner=owner,
                ttl_seconds=max(1, args.lane_ttl_seconds),
            )
            if ownership is None:
                raise RuntimeError("lane still owned during takeover handler")
            try:
                handler_calls += 1
                during_handler_owner = coordinator.inspect(item.session_key).to_dict()
                await asyncio.sleep(max(0.0, args.agent_delay_ms) / 1000.0)
                return f"handled:{item.id}"
            finally:
                coordinator.release(ownership)

    worker = TaskWorkerRuntime(queue, worker_id=new_worker_id)
    worker.register_handler("agent_inbound", SmokeLaneHandler())
    blocked_before_ttl = not await worker.run_once()
    after_blocked = store.get(task.id)
    wait_seconds = (
        max(0.05, float(args.takeover_wait_seconds))
        if args.takeover_wait_seconds
        else max(1, args.lane_ttl_seconds) + 0.25
    )
    await asyncio.sleep(wait_seconds)
    handled_after_ttl = await worker.run_once()
    after_takeover = store.get(task.id)
    after_completion_owner = coordinator.inspect(session_key).to_dict()
    if old_ownership is not None:
        try:
            coordinator.release(old_ownership)
        except Exception:
            pass
    result = {
        "status": "ok",
        "scenario": "worker-crash",
        "task_id": task.id,
        "session_key": session_key,
        "lane_namespace": args.lane_namespace,
        "lane_ttl_seconds": max(1, args.lane_ttl_seconds),
        "wait_seconds": wait_seconds,
        "old_worker_id": old_worker_id,
        "new_worker_id": new_worker_id,
        "old_owner_acquired": old_ownership is not None,
        "blocked_before_ttl": blocked_before_ttl,
        "handled_after_ttl": handled_after_ttl,
        "task_status_after_blocked": getattr(after_blocked, "status", ""),
        "task_status_after_takeover": getattr(after_takeover, "status", ""),
        "handler_calls": handler_calls,
        "before_ttl_owner": before_ttl_owner,
        "during_handler_owner": during_handler_owner,
        "after_completion_owner": after_completion_owner,
        "queue_stats": queue.stats(),
        "redis_health": redis.health().to_dict(),
    }
    result["failures"] = assert_worker_crash_result(result)
    result["status"] = "ok" if not result["failures"] else "failed"
    return result


async def run_broker_unavailable_smoke(args: argparse.Namespace) -> dict:
    broker = FailingInboundBroker()
    task_dir = args.work_dir / f"broker-unavailable-{int(time.time())}"
    store = LocalTaskStore(task_dir)
    queue = LocalTaskQueue(store, broker=broker)
    task = queue.enqueue(
        task_type="agent_inbound",
        source="smoke",
        session_key="smoke-session-broker-unavailable",
        payload={"text": "broker unavailable fallback"},
        metadata={"smoke": "broker-unavailable"},
    )
    after_enqueue = store.get(task.id)
    handler_calls = 0

    def handler(item) -> str:
        nonlocal handler_calls
        handler_calls += 1
        return f"handled:{item.id}"

    worker = TaskWorkerRuntime(queue, worker_id="smoke-worker-broker-fallback")
    worker.register_handler("agent_inbound", handler)
    handled = await worker.run_once()
    after_worker = store.get(task.id)
    result = {
        "status": "ok",
        "scenario": "broker-unavailable",
        "task_id": task.id,
        "publish_failed": broker.publish_attempts == 1,
        "worker_handled": handled,
        "task_status_after_enqueue": getattr(after_enqueue, "status", ""),
        "task_status_after_worker": getattr(after_worker, "status", ""),
        "result_preview": getattr(after_worker, "result_preview", ""),
        "handler_calls": handler_calls,
        "broker_publish_attempts": broker.publish_attempts,
        "broker_consume_attempts": broker.consume_attempts,
        "broker_stats": broker.stats(),
        "queue_stats": queue.stats(),
    }
    result["failures"] = assert_broker_unavailable_result(result)
    result["status"] = "ok" if not result["failures"] else "failed"
    return result


async def run_primary_unavailable_smoke(args: argparse.Namespace) -> dict:
    task_dir = args.work_dir / f"primary-unavailable-{int(time.time())}"
    store = LocalTaskStore(task_dir)
    primary = FailingPrimaryBackend()
    store.write_backend = primary
    queue = LocalTaskQueue(store)
    task = queue.enqueue(
        task_type="agent_inbound",
        source="smoke",
        session_key="smoke-session-primary-unavailable",
        payload={"text": "primary unavailable fallback"},
        metadata={"smoke": "primary-unavailable"},
    )
    broker = SinglePayloadBroker(
        {
            "task_id": task.id,
            "task_type": task.task_type,
            "session_key": task.session_key,
            "partition": 0,
            "idempotency_key": task.idempotency_key,
        }
    )
    queue.broker = broker
    handler_calls = 0

    def handler(item) -> str:
        nonlocal handler_calls
        handler_calls += 1
        return f"handled:{item.id}"

    worker = TaskWorkerRuntime(queue, worker_id="smoke-worker-primary-fallback")
    worker.register_handler("agent_inbound", handler)
    handled = await worker.run_once()
    after_worker = store.get(task.id)
    result = {
        "status": "ok",
        "scenario": "primary-unavailable",
        "task_id": task.id,
        "primary_reserve_failed": primary.reserve_task_id_attempts >= 1,
        "primary_reserve_attempts": primary.reserve_task_id_attempts,
        "worker_handled": handled,
        "task_status_after_worker": getattr(after_worker, "status", ""),
        "result_preview": getattr(after_worker, "result_preview", ""),
        "handler_calls": handler_calls,
        "broker_stats": broker.stats(),
        "queue_stats": queue.stats(),
    }
    result["failures"] = assert_primary_unavailable_result(result)
    result["status"] = "ok" if not result["failures"] else "failed"
    return result


async def run_postgres_lane_smoke(args: argparse.Namespace) -> dict:
    """Exercise session_lanes write/list/release against a real PostgreSQL store."""

    now = time.time()
    session_key = f"smoke-postgres-lane-{int(now * 1000)}"
    owner_token = "smoke-pg-worker:smoke-pg-task"
    release_reason = "smoke postgres lane release"
    writer = PostgresWriteRepository(
        url=args.postgres_url,
        enabled=True,
        connect_timeout_seconds=max(0.2, args.postgres_connect_timeout_seconds),
    )
    reader = PostgresReadRepository(
        url=args.postgres_url,
        enabled=True,
        connect_timeout_seconds=max(0.2, args.postgres_connect_timeout_seconds),
    )
    initialize_postgres_schema(
        url=args.postgres_url,
        connect_timeout_seconds=max(0.2, args.postgres_connect_timeout_seconds),
    )
    stale_renewed_at = now - max(2, args.lane_ttl_seconds + 1)
    row = {
        "session_key": session_key,
        "lane_key": f"{args.lane_namespace}:{session_key}",
        "worker_id": "smoke-pg-worker",
        "task_id": "smoke-pg-task",
        "owner_token": owner_token,
        "state": "owned",
        "ttl_seconds": max(1, args.lane_ttl_seconds),
        "acquired_at": stale_renewed_at,
        "renewed_at": stale_renewed_at,
        "updated_at": now,
        "metadata": {"smoke": True, "scenario": "postgres-lane"},
    }
    try:
        written = writer.write_session_lane(row)
        writer.write_session_lane_event(
            {
                **row,
                "event": "acquired",
                "occurred_at": now,
                "metadata": {"smoke": True, "scenario": "postgres-lane", "step": "acquired"},
            }
        )
        owned_rows = reader.list(
            "session_lanes",
            limit=5,
            filters={"state": "owned", "session_key": session_key},
        )
        owned_row = owned_rows[0] if owned_rows else {}
        mismatch_release = writer.release_session_lane(
            session_key,
            owner_token="wrong-owner-token",
            reason="wrong owner token smoke release",
            now=now + 1,
        )
        matched_release = writer.release_session_lane(
            session_key,
            owner_token=owner_token,
            reason=release_reason,
            now=now + 2,
        )
        writer.write_session_lane_event(
            {
                **row,
                "event": "released",
                "occurred_at": now + 2,
                "metadata": {"smoke": True, "scenario": "postgres-lane", "step": "released"},
            }
        )
        released_rows = reader.list(
            "session_lanes",
            limit=5,
            filters={"state": "released", "session_key": session_key},
        )
        released_row = released_rows[0] if released_rows else {}
        history_rows = reader.list(
            "session_lane_events",
            limit=10,
            filters={"session_key": session_key},
        )
        renewed_at = float(owned_row.get("renewed_at", 0.0) or 0.0)
        ttl_seconds = int(owned_row.get("ttl_seconds", 0) or 0)
        result = {
            "status": "ok",
            "scenario": "postgres-lane",
            "session_key": session_key,
            "postgres_url": args.postgres_url,
            "write_ok": written.get("session_key") == session_key,
            "listed_owned": bool(owned_row) and owned_row.get("owner_token") == owner_token,
            "stale_before_release": renewed_at > 0 and ttl_seconds > 0 and now >= renewed_at + ttl_seconds,
            "mismatch_release": mismatch_release,
            "matched_release": matched_release,
            "release_reason": release_reason,
            "owned_row": owned_row,
            "released_row": released_row,
            "history_count": len(history_rows),
            "history_events": [row.get("event") for row in history_rows],
        }
        result["failures"] = assert_postgres_lane_result(result)
        result["status"] = "ok" if not result["failures"] else "failed"
        return result
    finally:
        try:
            writer.delete("session_lanes", session_key)
        except Exception:
            pass
        for event_row in reader.list("session_lane_events", limit=20, filters={"session_key": session_key}):
            try:
                writer.delete("session_lane_events", str(event_row.get("id", "")))
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scenario in {"inbound", "ttl-takeover", "worker-crash"}:
        redis = RedisClient(
            enabled=True,
            url=args.redis_url,
            socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
        )
        redis_health = redis.health().to_dict()
        if not redis_health.get("ok"):
            print(
                json.dumps(
                    {"status": "failed", "reason": "redis unavailable", "redis": redis_health},
                    ensure_ascii=False,
                )
            )
            return 2

    try:
        if args.scenario == "readiness":
            readiness_result = asyncio.run(run_readiness_smoke(args))
            print(json.dumps(readiness_result, ensure_ascii=False))
            return 0 if not readiness_result.get("failures") else 1
        if args.scenario == "postgres-lane":
            postgres_result = asyncio.run(run_postgres_lane_smoke(args))
            print(json.dumps(postgres_result, ensure_ascii=False))
            return 0 if not postgres_result.get("failures") else 1
        if args.scenario == "primary-unavailable":
            primary_result = asyncio.run(run_primary_unavailable_smoke(args))
            print(json.dumps(primary_result, ensure_ascii=False))
            return 0 if not primary_result.get("failures") else 1
        if args.scenario == "broker-unavailable":
            broker_result = asyncio.run(run_broker_unavailable_smoke(args))
            print(json.dumps(broker_result, ensure_ascii=False))
            return 0 if not broker_result.get("failures") else 1
        if args.scenario == "worker-crash":
            crash_result = asyncio.run(run_worker_crash_smoke(args))
            print(json.dumps(crash_result, ensure_ascii=False))
            return 0 if not crash_result.get("failures") else 1
        if args.scenario == "ttl-takeover":
            takeover_result = asyncio.run(run_ttl_takeover_smoke(args))
            print(json.dumps(takeover_result, ensure_ascii=False))
            return 0 if not takeover_result.get("failures") else 1
        result, json_path, md_path = asyncio.run(run_smoke(args))
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        return 1

    failures = assert_smoke_result(result)
    output = {
        "status": "ok" if not failures else "failed",
        "failures": failures,
        "summary": result.get("summary", {}),
        "context": {
            "lane_mode": result.get("context", {}).get("lane_mode"),
            "max_same_session_concurrency": result.get("context", {}).get(
                "max_same_session_concurrency"
            ),
            "max_active_lanes": result.get("context", {}).get("max_active_lanes"),
            "broker_after_consume": result.get("context", {}).get("broker_after_consume"),
            "redis_health": result.get("context", {}).get("redis_health"),
        },
        "json": str(json_path),
        "markdown": str(md_path),
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
