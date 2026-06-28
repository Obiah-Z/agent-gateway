#!/usr/bin/env python3
"""Build a capacity baseline report from load-test JSON outputs."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import platform
from pathlib import Path
import subprocess
import sys
from typing import Any


DEFAULT_INPUT_DIR = Path("workspace/reports/load-tests")
DEFAULT_OUTPUT = Path("workspace/reports/capacity-baseline.md")


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


def load_results(input_dir: Path) -> list[dict[str, Any]]:
    """Load valid load-test result JSON files from a directory."""

    if not input_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload.get("scenario"), dict) and isinstance(payload.get("summary"), dict):
            payload["_source_path"] = str(path)
            results.append(payload)
    return results


def pick_best_per_scenario(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the strongest successful result per scenario for a concise baseline."""

    best: dict[str, dict[str, Any]] = {}
    for result in results:
        scenario = str(result.get("scenario", {}).get("name", "unknown"))
        current = best.get(scenario)
        if current is None or _score(result) > _score(current):
            best[scenario] = result
    return sorted(best.values(), key=lambda item: str(item.get("scenario", {}).get("name", "")))


def render_capacity_baseline(results: list[dict[str, Any]]) -> str:
    """Render a Markdown capacity baseline report."""

    selected = pick_best_per_scenario(results)
    lines = [
        "# AI Agent Gateway 容量基线报告",
        "",
        "## 基本信息",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 机器：{platform.platform()}",
        f"- Git commit：{git_commit()}",
        f"- Python 版本：{sys.version.split()[0]}",
        f"- 原始报告数量：{len(results)}",
        f"- 纳入基线场景数：{len(selected)}",
        "",
    ]
    if not selected:
        lines.extend(
            [
                "## 结果摘要",
                "",
                "暂无可用压测 JSON。请先运行 `scripts/load_test_gateway.py` 生成报告。",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## 场景基线",
            "",
            "| 场景 | 请求数 | 并发 | 成功/失败 | 错误率 | 吞吐 req/s | E2E P95 ms | Agent P95 ms | Delivery P95 ms | 最大投递积压 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in selected:
        scenario = result["scenario"]
        summary = result["summary"]
        lines.append(
            "| "
            f"{scenario.get('name', 'unknown')} | "
            f"{scenario.get('requests', 0)} | "
            f"{scenario.get('concurrency', 0)} | "
            f"{summary.get('success', 0)}/{summary.get('failed', 0)} | "
            f"{summary.get('error_rate', 0)} | "
            f"{summary.get('throughput_rps', 0)} | "
            f"{_latency(summary, 'e2e_ms', 'p95')} | "
            f"{_latency(summary, 'agent_turn_ms', 'p95')} | "
            f"{_latency(summary, 'delivery_ms', 'p95')} | "
            f"{summary.get('max_delivery_backlog', 0)} |"
        )

    lines.extend(["", "## 瓶颈判断", ""])
    for result in selected:
        lines.extend(_scenario_judgement(result))

    lines.extend(
        [
            "## 使用边界",
            "",
            "- `mock-local` 代表网关本地调度上限，不代表真实模型或飞书链路。",
            "- `delivery-local` 代表本地文件投递 fallback/audit 路径，默认按单 worker 观察。",
            "- `delivery-rabbitmq` 代表 RabbitMQ 分发和 DeliveryRuntime broker consume 路径，当前不包含真实 PostgreSQL 锁竞争。",
            "- `model-real` 和 `feishu-send-real` 会调用真实外部服务，容量结论受 API 限流、网络和平台状态影响。",
            "- 基线报告用于对比趋势，不应作为严格 SLA；正式 SLA 需要固定机器、固定依赖版本和多轮重复压测。",
            "",
            "## 原始报告",
            "",
        ]
    )
    for result in selected:
        lines.append(
            f"- `{result.get('_source_path', '')}`："
            f"{result.get('scenario', {}).get('name', 'unknown')}"
        )
    lines.append("")
    return "\n".join(lines)


def write_capacity_baseline(results: list[dict[str, Any]], output: Path) -> Path:
    """Write the capacity baseline report."""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_capacity_baseline(results), encoding="utf-8")
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build AI Agent Gateway capacity baseline report.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = load_results(args.input_dir)
    output = write_capacity_baseline(results, args.output)
    print(json.dumps({"input_count": len(results), "output": str(output)}, ensure_ascii=False))
    return 0


def _score(result: dict[str, Any]) -> tuple[int, float, int]:
    summary = result.get("summary", {})
    scenario = result.get("scenario", {})
    success = int(summary.get("success", 0) or 0)
    throughput = float(summary.get("throughput_rps", 0) or 0)
    requests = int(scenario.get("requests", 0) or 0)
    return success, throughput, requests


def _latency(summary: dict[str, Any], section: str, key: str) -> float:
    payload = summary.get(section, {})
    if not isinstance(payload, dict):
        return 0.0
    value = payload.get(key, 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _scenario_judgement(result: dict[str, Any]) -> list[str]:
    scenario = result.get("scenario", {})
    summary = result.get("summary", {})
    name = str(scenario.get("name", "unknown"))
    error_rate = float(summary.get("error_rate", 0) or 0)
    throughput = float(summary.get("throughput_rps", 0) or 0)
    e2e_p95 = _latency(summary, "e2e_ms", "p95")
    lines = [f"### {name}", ""]
    if error_rate > 0:
        lines.append(f"- 存在失败请求，错误率为 {error_rate}，优先查看原始报告中的 `errors`。")
    elif name == "mock-local":
        lines.append(f"- 本地调度基线吞吐约 {throughput} req/s，E2E P95 约 {e2e_p95} ms。")
    elif name.startswith("delivery"):
        backlog = summary.get("max_delivery_backlog", 0)
        lines.append(f"- 投递链路吞吐约 {throughput} req/s，最大投递积压 {backlog}，Delivery P95 约 {_latency(summary, 'delivery_ms', 'p95')} ms。")
    elif name == "model-real":
        lines.append(f"- 真实模型链路 E2E P95 约 {e2e_p95} ms，主要受模型 API、网络和上下文装配影响。")
    elif name == "feishu-send-real":
        lines.append(f"- 飞书出站链路 E2E P95 约 {e2e_p95} ms，主要受飞书 API、lark-cli/token 刷新和网络影响。")
    else:
        lines.append(f"- 当前场景吞吐约 {throughput} req/s，E2E P95 约 {e2e_p95} ms。")
    lines.append("")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
