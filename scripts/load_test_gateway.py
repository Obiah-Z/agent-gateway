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


DEFAULT_REPORT_DIR = Path("workspace/reports/load-tests")
SUPPORTED_SCENARIOS = {"mock-local"}


@dataclass(slots=True)
class RequestSample:
    """Single synthetic request timing sample."""

    request_id: str
    ok: bool
    error: str
    e2e_ms: float
    agent_turn_ms: float
    delivery_ms: float


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
) -> dict[str, Any]:
    """Build a stable JSON-serializable load test result."""

    successful = [sample for sample in samples if sample.ok]
    failed = [sample for sample in samples if not sample.ok]
    e2e_values = [sample.e2e_ms for sample in successful]
    agent_values = [sample.agent_turn_ms for sample in successful]
    delivery_values = [sample.delivery_ms for sample in successful]
    throughput = len(successful) / wall_seconds if wall_seconds > 0 else 0.0
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
            "max_delivery_backlog": 0,
        },
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
            "- 运行角色：mock-local script",
            "- Redis / PostgreSQL / RabbitMQ 配置：本场景不访问外部中间件",
            "",
            "## 场景配置",
            f"- 场景：{scenario['name']}",
            f"- 并发：{scenario['concurrency']}",
            f"- 请求数：{scenario['requests']}",
            f"- 是否真实模型：{scenario['uses_real_model']}",
            f"- 是否真实飞书：{scenario['uses_real_feishu']}",
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
            "- 证据：该场景只模拟本地 agent/delivery 延迟，不调用真实模型、飞书、PostgreSQL 或 RabbitMQ。",
            "- 建议：下一步运行 delivery-local / delivery-rabbitmq 场景，分离队列瓶颈。",
            "",
            "## 原始指标摘要",
            "- runtime.status：mock-local 未采集运行中网关状态",
            "- delivery.stats：mock-local 未访问真实投递队列",
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
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--basename", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requests = max(1, args.requests)
    concurrency = max(1, args.concurrency)
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
