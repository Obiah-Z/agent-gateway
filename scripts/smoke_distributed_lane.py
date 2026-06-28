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
        choices=("inbound", "ttl-takeover", "broker-unavailable", "primary-unavailable"),
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scenario in {"inbound", "ttl-takeover"}:
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
        if args.scenario == "primary-unavailable":
            primary_result = asyncio.run(run_primary_unavailable_smoke(args))
            print(json.dumps(primary_result, ensure_ascii=False))
            return 0 if not primary_result.get("failures") else 1
        if args.scenario == "broker-unavailable":
            broker_result = asyncio.run(run_broker_unavailable_smoke(args))
            print(json.dumps(broker_result, ensure_ascii=False))
            return 0 if not broker_result.get("failures") else 1
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
