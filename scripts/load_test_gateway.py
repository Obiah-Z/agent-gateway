#!/usr/bin/env python3
"""AI Agent Gateway load test helper.

Phase 20.8 starts with a deterministic mock-local scenario. It measures the
gateway's local scheduling/reporting baseline without calling model providers or
external messaging platforms.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid
from typing import Any

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.infra.rabbitmq import RabbitMQDeliveryBroker
from agent_gateway.runtime.state.queue import DeliveryQueue


DEFAULT_REPORT_DIR = Path("workspace/reports/load-tests")
SUPPORTED_SCENARIOS = {"delivery-local", "delivery-rabbitmq", "mock-local"}


@dataclass(slots=True)
class RequestSample:
    """Single synthetic request timing sample."""

    request_id: str
    ok: bool
    error: str
    e2e_ms: float
    agent_turn_ms: float
    delivery_ms: float


class LoadTestChannel(Channel):
    """Mock channel used by delivery-local load tests."""

    name = "load"

    def __init__(self, *, delay_ms: float = 0.0) -> None:
        self.delay_ms = max(0.0, delay_ms)
        self.sent: list[OutboundMessage] = []

    def receive(self) -> InboundMessage | None:
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000)
        self.sent.append(outbound)
        return True


class InMemoryDeliveryBackend:
    """Minimal delivery_entries backend for broker load tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def list(self, table: str, *, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if table != "delivery_entries":
            return []
        state = str((filters or {}).get("state", ""))
        rows = [row for row in self.rows.values() if not state or row.get("state") == state]
        rows.sort(key=lambda row: float(row.get("enqueued_at", 0.0) or 0.0))
        return [dict(row) for row in rows[:limit]]

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        if table != "delivery_entries":
            return None
        row = self.rows.get(key)
        return dict(row) if row is not None else None

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table != "delivery_entries":
            return row
        payload = dict(row)
        self.rows[str(payload["id"])] = payload
        return payload

    def delete(self, table: str, key: str) -> bool:
        if table != "delivery_entries":
            return False
        return self.rows.pop(key, None) is not None

    def write_delivery_entry(self, entry: Any, *, state: str = "pending") -> dict[str, Any]:
        payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        payload["state"] = state
        payload["updated_at"] = time.time()
        self.rows[str(payload["id"])] = payload
        return payload

    def delete_delivery_entry(self, delivery_id: str) -> bool:
        return self.rows.pop(delivery_id, None) is not None

    def reserve_delivery(
        self,
        *,
        worker_id: str,
        now: float | None = None,
        delivery_id: str = "",
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        candidates = [
            row
            for row in self.rows.values()
            if row.get("state") in {"pending", "retrying"}
            and (not delivery_id or row.get("id") == delivery_id)
            and float(row.get("next_retry_at", 0.0) or 0.0) <= current
        ]
        candidates.sort(key=lambda row: float(row.get("enqueued_at", 0.0) or 0.0))
        if not candidates:
            return None
        row = candidates[0]
        row["state"] = "running"
        row["locked_by"] = worker_id
        row["locked_at"] = current
        row["updated_at"] = current
        return dict(row)


def percentile(values: list[float], percent: float) -> float:
    """Return a nearest-rank percentile value."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize_latencies(values: list[float]) -> dict[str, float]:
    """Build latency summary used by JSON and Markdown reports."""

    if not values:
        return {"min": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 3),
        "avg": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "p99": round(percentile(values, 99), 3),
        "max": round(max(values), 3),
    }


async def _run_mock_request(
    *,
    request_index: int,
    semaphore: asyncio.Semaphore,
    agent_delay_ms: float,
    delivery_delay_ms: float,
) -> RequestSample:
    """Run one synthetic local request through mock agent and delivery phases."""

    request_id = f"load-{request_index:06d}-{uuid.uuid4().hex[:8]}"
    async with semaphore:
        started = time.perf_counter()
        try:
            agent_started = time.perf_counter()
            await asyncio.sleep(agent_delay_ms / 1000)
            agent_turn_ms = (time.perf_counter() - agent_started) * 1000

            delivery_started = time.perf_counter()
            await asyncio.sleep(delivery_delay_ms / 1000)
            delivery_ms = (time.perf_counter() - delivery_started) * 1000

            return RequestSample(
                request_id=request_id,
                ok=True,
                error="",
                e2e_ms=(time.perf_counter() - started) * 1000,
                agent_turn_ms=agent_turn_ms,
                delivery_ms=delivery_ms,
            )
        except Exception as exc:  # pragma: no cover - defensive guard for report stability.
            return RequestSample(
                request_id=request_id,
                ok=False,
                error=str(exc),
                e2e_ms=(time.perf_counter() - started) * 1000,
                agent_turn_ms=0.0,
                delivery_ms=0.0,
            )


async def run_mock_local(
    *,
    requests: int,
    concurrency: int,
    agent_delay_ms: float,
    delivery_delay_ms: float,
) -> tuple[list[RequestSample], float]:
    """Execute the mock-local scenario and return samples plus wall time."""

    semaphore = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    samples = await asyncio.gather(
        *[
            _run_mock_request(
                request_index=index,
                semaphore=semaphore,
                agent_delay_ms=agent_delay_ms,
                delivery_delay_ms=delivery_delay_ms,
            )
            for index in range(1, requests + 1)
        ]
    )
    return list(samples), time.perf_counter() - started


async def run_delivery_local(
    *,
    requests: int,
    concurrency: int,
    delivery_delay_ms: float,
    work_dir: Path,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run real DeliveryQueue + DeliveryRuntime with a mock local channel."""

    queue_dir = work_dir / f"delivery-local-{uuid.uuid4().hex[:8]}"
    queue = DeliveryQueue(queue_dir)
    manager = ChannelManager()
    channel = LoadTestChannel(delay_ms=delivery_delay_ms)
    manager.register(channel, ChannelAccount(channel="load", account_id="load-local"))
    runtime = DeliveryRuntime(queue, manager)
    samples: list[RequestSample] = []

    enqueue_started = time.perf_counter()
    for index in range(1, requests + 1):
        request_id = f"delivery-{index:06d}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        delivery_id = queue.enqueue(
            "load",
            "load-peer",
            f"load test message {index}",
            {
                "account_id": "load-local",
                "kind": "load-test",
                "request_id": request_id,
            },
        )
        samples.append(
            RequestSample(
                request_id=delivery_id,
                ok=True,
                error="",
                e2e_ms=0.0,
                agent_turn_ms=0.0,
                delivery_ms=(time.perf_counter() - started) * 1000,
            )
        )
    max_delivery_backlog = len(queue.pending_entries())
    enqueue_seconds = time.perf_counter() - enqueue_started

    started = time.perf_counter()
    # The local file queue is a fallback/audit path and is intentionally measured
    # as a single delivery worker. Multi-worker correctness is covered by the
    # PostgreSQL/RabbitMQ path, not by concurrent local file scans.
    while len(queue.pending_entries()) > 0:
        await runtime.flush_once()
    wall_seconds = time.perf_counter() - started

    delivery_finished_ms = wall_seconds * 1000
    if samples:
        per_message_ms = delivery_finished_ms / len(samples)
        for sample in samples:
            sample.e2e_ms = per_message_ms
            sample.delivery_ms = per_message_ms

    context = {
        "enqueue_seconds": round(enqueue_seconds, 3),
        "sent": len(channel.sent),
        "max_delivery_backlog": max_delivery_backlog,
        "queue_dir": str(queue_dir),
        "uses_real_delivery_queue": True,
        "effective_delivery_workers": 1,
        "requested_concurrency": concurrency,
    }
    return samples, wall_seconds, context


async def run_delivery_rabbitmq(
    *,
    requests: int,
    concurrency: int,
    delivery_delay_ms: float,
    work_dir: Path,
    rabbitmq_url: str,
    rabbitmq_exchange: str,
    rabbitmq_queue: str,
    rabbitmq_dead_letter_exchange: str,
    rabbitmq_dead_letter_queue: str,
    connect_timeout_seconds: float,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run RabbitMQ-backed delivery publishing and consumption."""

    queue_dir = work_dir / f"delivery-rabbitmq-{uuid.uuid4().hex[:8]}"
    queue = DeliveryQueue(queue_dir)
    backend = InMemoryDeliveryBackend()
    queue.read_backend = backend
    queue.write_backend = backend
    broker = RabbitMQDeliveryBroker(
        url=rabbitmq_url,
        exchange=rabbitmq_exchange,
        queue=rabbitmq_queue,
        dead_letter_exchange=rabbitmq_dead_letter_exchange,
        dead_letter_queue=rabbitmq_dead_letter_queue,
        connect_timeout_seconds=connect_timeout_seconds,
        enabled=True,
    )
    queue.broker = broker
    manager = ChannelManager()
    channel = LoadTestChannel(delay_ms=delivery_delay_ms)
    manager.register(channel, ChannelAccount(channel="load", account_id="load-local"))
    runtime = DeliveryRuntime(queue, manager)
    samples: list[RequestSample] = []

    enqueue_started = time.perf_counter()
    for index in range(1, requests + 1):
        request_id = f"rabbitmq-{index:06d}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        delivery_id = queue.enqueue(
            "load",
            "load-peer",
            f"rabbitmq load test message {index}",
            {
                "account_id": "load-local",
                "kind": "load-test",
                "request_id": request_id,
                "correlation_id": request_id,
            },
        )
        samples.append(
            RequestSample(
                request_id=delivery_id,
                ok=True,
                error="",
                e2e_ms=0.0,
                agent_turn_ms=0.0,
                delivery_ms=(time.perf_counter() - started) * 1000,
            )
        )
    broker_stats_after_publish = queue.broker_stats()
    max_delivery_backlog = len(queue.pending_entries())
    enqueue_seconds = time.perf_counter() - enqueue_started

    started = time.perf_counter()
    while len(channel.sent) < requests:
        tasks = [runtime.flush_once() for _ in range(max(1, concurrency))]
        await asyncio.gather(*tasks)
    wall_seconds = time.perf_counter() - started
    broker_stats_after_consume = queue.broker_stats()
    broker.close()

    delivery_finished_ms = wall_seconds * 1000
    if samples:
        per_message_ms = delivery_finished_ms / len(samples)
        for sample in samples:
            sample.e2e_ms = per_message_ms
            sample.delivery_ms = per_message_ms

    context = {
        "enqueue_seconds": round(enqueue_seconds, 3),
        "sent": len(channel.sent),
        "max_delivery_backlog": max_delivery_backlog,
        "queue_dir": str(queue_dir),
        "uses_real_delivery_queue": True,
        "uses_rabbitmq": True,
        "effective_delivery_workers": max(1, concurrency),
        "broker_after_publish": broker_stats_after_publish,
        "broker_after_consume": broker_stats_after_consume,
    }
    return samples, wall_seconds, context


def git_commit() -> str:
    """Return current short git commit for report reproducibility."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except Exception:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def build_result(
    *,
    scenario: str,
    requests: int,
    concurrency: int,
    wall_seconds: float,
    samples: list[RequestSample],
    agent_delay_ms: float,
    delivery_delay_ms: float,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable JSON-serializable load test result."""

    successful = [sample for sample in samples if sample.ok]
    failed = [sample for sample in samples if not sample.ok]
    e2e_values = [sample.e2e_ms for sample in successful]
    agent_values = [sample.agent_turn_ms for sample in successful]
    delivery_values = [sample.delivery_ms for sample in successful]
    throughput = len(successful) / wall_seconds if wall_seconds > 0 else 0.0
    context = context or {}
    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "scenario": {
            "name": scenario,
            "requests": requests,
            "concurrency": concurrency,
            "agent_delay_ms": agent_delay_ms,
            "delivery_delay_ms": delivery_delay_ms,
            "uses_real_model": False,
            "uses_real_feishu": False,
            "uses_real_delivery_queue": bool(context.get("uses_real_delivery_queue", False)),
            "uses_rabbitmq": bool(context.get("uses_rabbitmq", False)),
        },
        "summary": {
            "success": len(successful),
            "failed": len(failed),
            "error_rate": round(len(failed) / requests, 6) if requests else 0.0,
            "wall_seconds": round(wall_seconds, 3),
            "throughput_rps": round(throughput, 3),
            "e2e_ms": summarize_latencies(e2e_values),
            "agent_turn_ms": summarize_latencies(agent_values),
            "delivery_ms": summarize_latencies(delivery_values),
            "max_inbound_backlog": 0,
            "max_delivery_backlog": int(context.get("max_delivery_backlog", 0) or 0),
        },
        "context": context,
        "errors": [
            {"request_id": sample.request_id, "error": sample.error}
            for sample in failed[:20]
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render the fixed Phase 20.8 report structure."""

    meta = result["meta"]
    scenario = result["scenario"]
    summary = result["summary"]
    bottleneck = "mock-local 基线未发现外部服务瓶颈"
    evidence = "该场景只模拟本地 agent/delivery 延迟，不调用真实模型、飞书、PostgreSQL 或 RabbitMQ。"
    suggestion = "下一步运行 delivery-local / delivery-rabbitmq 场景，分离队列瓶颈。"
    middleware = "本场景不访问外部中间件"
    runtime_role = "mock-local script"
    if scenario["name"] == "delivery-local":
        bottleneck = "delivery-local 基线主要反映本地文件队列和 DeliveryRuntime flush 吞吐"
        evidence = "该场景使用真实 DeliveryQueue、DeliveryRuntime 和 mock channel，不调用真实模型、飞书、PostgreSQL 或 RabbitMQ。"
        suggestion = "下一步接入 delivery-rabbitmq 场景，对比 RabbitMQ 分发和本地轮询差异。"
        middleware = "使用本地文件 DeliveryQueue，不访问 Redis/PostgreSQL/RabbitMQ"
        runtime_role = "delivery-local script"
    if scenario["name"] == "delivery-rabbitmq":
        bottleneck = "delivery-rabbitmq 基线主要反映 RabbitMQ 分发和 DeliveryRuntime broker consume 吞吐"
        evidence = "该场景使用真实 RabbitMQ broker、DeliveryQueue、DeliveryRuntime 和内存事实状态 backend，不调用真实模型或飞书。"
        suggestion = "对比 delivery-local 报告，观察 RabbitMQ 引用分发、ack 和多 worker flush 的成本。"
        middleware = "使用 RabbitMQ；事实状态使用脚本内内存 backend，不访问真实 PostgreSQL"
        runtime_role = "delivery-rabbitmq script"
    if summary["error_rate"] > 0:
        bottleneck = "存在请求失败，优先检查脚本错误和本地资源"
    return "\n".join(
        [
            "# AI Agent Gateway 压测报告",
            "",
            "## 基本信息",
            f"- 时间：{meta['generated_at']}",
            f"- 机器：{meta['platform']}",
            f"- Git commit：{meta['git_commit']}",
            f"- Python 版本：{meta['python']}",
            f"- 运行角色：{runtime_role}",
            f"- Redis / PostgreSQL / RabbitMQ 配置：{middleware}",
            "",
            "## 场景配置",
            f"- 场景：{scenario['name']}",
            f"- 并发：{scenario['concurrency']}",
            f"- 请求数：{scenario['requests']}",
            f"- 是否真实模型：{scenario['uses_real_model']}",
            f"- 是否真实飞书：{scenario['uses_real_feishu']}",
            f"- 是否真实 RabbitMQ：{scenario['uses_rabbitmq']}",
            "",
            "## 结果摘要",
            f"- 成功数 / 失败数 / 错误率：{summary['success']} / {summary['failed']} / {summary['error_rate']}",
            f"- 吞吐：{summary['throughput_rps']} req/s",
            (
                "- E2E P50 / P95 / P99："
                f"{summary['e2e_ms']['p50']} / {summary['e2e_ms']['p95']} / {summary['e2e_ms']['p99']} ms"
            ),
            (
                "- Agent P50 / P95 / P99："
                f"{summary['agent_turn_ms']['p50']} / {summary['agent_turn_ms']['p95']} / {summary['agent_turn_ms']['p99']} ms"
            ),
            (
                "- Delivery P50 / P95 / P99："
                f"{summary['delivery_ms']['p50']} / {summary['delivery_ms']['p95']} / {summary['delivery_ms']['p99']} ms"
            ),
            f"- 最大入站积压：{summary['max_inbound_backlog']}",
            f"- 最大投递积压：{summary['max_delivery_backlog']}",
            "",
            "## 瓶颈判断",
            f"- 主要瓶颈：{bottleneck}",
            f"- 证据：{evidence}",
            f"- 建议：{suggestion}",
            "",
            "## 原始指标摘要",
            "- runtime.status：mock-local 未采集运行中网关状态",
            f"- delivery.stats：最大投递积压 {summary['max_delivery_backlog']}",
            "- metrics.summary：mock-local 未访问控制面 metrics",
            f"- errors.recent：{len(result['errors'])} 条脚本内错误",
            "",
        ]
    )


def write_reports(result: dict[str, Any], report_dir: Path, basename: str) -> tuple[Path, Path]:
    """Write JSON and Markdown reports."""

    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{basename}.json"
    md_path = report_dir / f"{basename}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Agent Gateway load tests.")
    parser.add_argument("--scenario", default="mock-local", choices=sorted(SUPPORTED_SCENARIOS))
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--agent-delay-ms", type=float, default=10.0)
    parser.add_argument("--delivery-delay-ms", type=float, default=2.0)
    parser.add_argument("--work-dir", type=Path, default=Path(".load-test-tmp"))
    parser.add_argument("--rabbitmq-url", default="amqp://admin:admin123@127.0.0.1:5672/")
    parser.add_argument("--rabbitmq-exchange", default="agent_gateway.delivery.load_test")
    parser.add_argument("--rabbitmq-queue", default="agent_gateway.delivery.load_test.outbound")
    parser.add_argument("--rabbitmq-dead-letter-exchange", default="agent_gateway.delivery.load_test.dlx")
    parser.add_argument("--rabbitmq-dead-letter-queue", default="agent_gateway.delivery.load_test.dead")
    parser.add_argument("--rabbitmq-connect-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--basename", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requests = max(1, args.requests)
    concurrency = max(1, args.concurrency)
    context: dict[str, Any] = {}
    if args.scenario == "delivery-rabbitmq":
        samples, wall_seconds, context = asyncio.run(
            run_delivery_rabbitmq(
                requests=requests,
                concurrency=concurrency,
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
                work_dir=args.work_dir,
                rabbitmq_url=args.rabbitmq_url,
                rabbitmq_exchange=args.rabbitmq_exchange,
                rabbitmq_queue=args.rabbitmq_queue,
                rabbitmq_dead_letter_exchange=args.rabbitmq_dead_letter_exchange,
                rabbitmq_dead_letter_queue=args.rabbitmq_dead_letter_queue,
                connect_timeout_seconds=max(0.2, args.rabbitmq_connect_timeout_seconds),
            )
        )
    elif args.scenario == "delivery-local":
        samples, wall_seconds, context = asyncio.run(
            run_delivery_local(
                requests=requests,
                concurrency=concurrency,
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
                work_dir=args.work_dir,
            )
        )
    else:
        samples, wall_seconds = asyncio.run(
            run_mock_local(
                requests=requests,
                concurrency=concurrency,
                agent_delay_ms=max(0.0, args.agent_delay_ms),
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
            )
        )
    result = build_result(
        scenario=args.scenario,
        requests=requests,
        concurrency=concurrency,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=max(0.0, args.agent_delay_ms),
        delivery_delay_ms=max(0.0, args.delivery_delay_ms),
        context=context,
    )
    basename = args.basename or f"{args.scenario}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path, md_path = write_reports(result, args.report_dir, basename)
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "success": result["summary"]["success"],
                "failed": result["summary"]["failed"],
                "throughput_rps": result["summary"]["throughput_rps"],
                "e2e_p95_ms": result["summary"]["e2e_ms"]["p95"],
                "json": str(json_path),
                "markdown": str(md_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
