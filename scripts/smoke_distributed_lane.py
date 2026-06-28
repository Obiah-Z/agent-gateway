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
from scripts.load_test_gateway import (
    DEFAULT_REPORT_DIR,
    build_result,
    run_inbound_rabbitmq,
    write_reports,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run distributed lane smoke checks.")
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
    parser.add_argument("--work-dir", type=Path, default=Path(".load-test-tmp"))
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--basename", default="")
    return parser.parse_args(argv)


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    redis = RedisClient(
        enabled=True,
        url=args.redis_url,
        socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
    )
    redis_health = redis.health().to_dict()
    if not redis_health.get("ok"):
        print(json.dumps({"status": "failed", "reason": "redis unavailable", "redis": redis_health}, ensure_ascii=False))
        return 2

    try:
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
