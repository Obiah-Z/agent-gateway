#!/usr/bin/env python3
"""Run or print the Phase 20.8 capacity boundary test matrix."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


DEFAULT_REPORT_DIR = Path("workspace/reports/load-tests")
DEFAULT_BASELINE_OUTPUT = Path("workspace/reports/capacity-baseline.md")


@dataclass(frozen=True, slots=True)
class MatrixCase:
    """One reproducible capacity test case."""

    name: str
    layer: str
    scenario: str
    requests: int
    concurrency: int
    purpose: str
    external: bool = False
    requires_rabbitmq: bool = False
    requires_redis: bool = False
    extra_args: tuple[str, ...] = ()

    def command(self, report_dir: Path) -> list[str]:
        """Build the load-test command for this case."""

        command = [
            sys.executable,
            "scripts/load_test_gateway.py",
            "--scenario",
            self.scenario,
            "--requests",
            str(self.requests),
            "--concurrency",
            str(self.concurrency),
            "--report-dir",
            str(report_dir),
            "--basename",
            self.name,
        ]
        command.extend(self.extra_args)
        return command


SAFE_MATRIX: tuple[MatrixCase, ...] = (
    MatrixCase(
        name="matrix-mock-c1",
        layer="本地闭环",
        scenario="mock-local",
        requests=200,
        concurrency=1,
        purpose="建立单并发本地调度基线。",
    ),
    MatrixCase(
        name="matrix-mock-c8",
        layer="本地闭环",
        scenario="mock-local",
        requests=1000,
        concurrency=8,
        purpose="观察提高并发后本地调度吞吐提升幅度。",
    ),
    MatrixCase(
        name="matrix-mock-c16",
        layer="本地闭环",
        scenario="mock-local",
        requests=2000,
        concurrency=16,
        purpose="定位本地调度吞吐平台期和 P95 变化。",
    ),
    MatrixCase(
        name="matrix-delivery-local-c1",
        layer="队列链路",
        scenario="delivery-local",
        requests=200,
        concurrency=1,
        purpose="测量本地文件投递 fallback/audit 路径单 worker 能力。",
        extra_args=("--delivery-delay-ms", "0"),
    ),
    MatrixCase(
        name="matrix-delivery-rabbitmq-c4",
        layer="队列链路",
        scenario="delivery-rabbitmq",
        requests=500,
        concurrency=4,
        purpose="测量 RabbitMQ 出站投递分发、reserve 和 ack 能力。",
        requires_rabbitmq=True,
        extra_args=("--delivery-delay-ms", "0"),
    ),
    MatrixCase(
        name="matrix-inbound-rabbitmq-local-c4",
        layer="入站队列",
        scenario="inbound-rabbitmq",
        requests=500,
        concurrency=4,
        purpose="测量 RabbitMQ 入站分区、task_id 精确预占和本地 lane 探针。",
        requires_rabbitmq=True,
        extra_args=(
            "--agent-delay-ms",
            "0",
            "--inbound-session-count",
            "50",
            "--inbound-rabbitmq-partitions",
            "8",
            "--inbound-lane-mode",
            "local",
        ),
    ),
    MatrixCase(
        name="matrix-inbound-rabbitmq-redis-c4",
        layer="入站队列",
        scenario="inbound-rabbitmq",
        requests=500,
        concurrency=4,
        purpose="测量真实 Redis lane ownership 下的同 session 串行和队列清空能力。",
        requires_rabbitmq=True,
        requires_redis=True,
        extra_args=(
            "--agent-delay-ms",
            "0",
            "--inbound-session-count",
            "50",
            "--inbound-rabbitmq-partitions",
            "8",
            "--inbound-lane-mode",
            "redis",
        ),
    ),
)


EXTERNAL_MATRIX: tuple[MatrixCase, ...] = (
    MatrixCase(
        name="matrix-model-real-c1",
        layer="真实模型",
        scenario="model-real",
        requests=3,
        concurrency=1,
        purpose="低并发验证真实模型、Prompt 装配和 AgentLoopRunner 延迟。",
        external=True,
        extra_args=(
            "--allow-real-external",
            "--prompt",
            "请用一句中文回复 pong，不要调用工具。",
        ),
    ),
    MatrixCase(
        name="matrix-feishu-send-real-c1",
        layer="真实飞书",
        scenario="feishu-send-real",
        requests=3,
        concurrency=1,
        purpose="低并发验证飞书出站 API、token 刷新和平台限流。",
        external=True,
        extra_args=(
            "--allow-real-external",
            "--message-text",
            "AI Agent Gateway 飞书发送压测。",
        ),
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Run AI Agent Gateway capacity matrix.")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--baseline-output", type=Path, default=DEFAULT_BASELINE_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="include real model/Feishu commands in the matrix output",
    )
    parser.add_argument(
        "--run-external",
        action="store_true",
        help="actually run real model/Feishu cases; requires case-specific args",
    )
    parser.add_argument("--feishu-account-id", default="")
    parser.add_argument("--feishu-peer-id", default="")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="continue running remaining cases after a failed case",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run selected matrix cases and then rebuild capacity baseline."""

    args = parse_args(argv)
    cases = list(SAFE_MATRIX)
    if args.include_external or args.run_external:
        cases.extend(_external_cases(args))

    manifest = _build_manifest(cases, args.report_dir, args.baseline_output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    failures: list[dict[str, Any]] = []
    for case in cases:
        if case.external and not args.run_external:
            print(f"[skip] {case.name}: external case requires --run-external")
            continue
        command = case.command(args.report_dir)
        print(f"[run] {case.name}: {_shell_join(command)}")
        completed = subprocess.run(command, text=True)
        if completed.returncode != 0:
            failures.append({"case": case.name, "returncode": completed.returncode})
            if not args.continue_on_error:
                break

    baseline_command = [
        sys.executable,
        "scripts/build_capacity_baseline.py",
        "--input-dir",
        str(args.report_dir),
        "--output",
        str(args.baseline_output),
    ]
    if not failures or args.continue_on_error:
        print(f"[run] capacity-baseline: {_shell_join(baseline_command)}")
        completed = subprocess.run(baseline_command, text=True)
        if completed.returncode != 0:
            failures.append({"case": "capacity-baseline", "returncode": completed.returncode})

    summary = {
        "ran": [case.name for case in cases if not case.external or args.run_external],
        "skipped_external": [
            case.name for case in cases if case.external and not args.run_external
        ],
        "failures": failures,
        "baseline": str(args.baseline_output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def _external_cases(args: argparse.Namespace) -> list[MatrixCase]:
    """Return external cases with user-provided Feishu target args injected."""

    cases: list[MatrixCase] = []
    for case in EXTERNAL_MATRIX:
        if case.scenario != "feishu-send-real":
            cases.append(case)
            continue
        extra = list(case.extra_args)
        if args.feishu_account_id:
            extra.extend(["--feishu-account-id", args.feishu_account_id])
        if args.feishu_peer_id:
            extra.extend(["--feishu-peer-id", args.feishu_peer_id])
        cases.append(
            MatrixCase(
                name=case.name,
                layer=case.layer,
                scenario=case.scenario,
                requests=case.requests,
                concurrency=case.concurrency,
                purpose=case.purpose,
                external=case.external,
                requires_rabbitmq=case.requires_rabbitmq,
                requires_redis=case.requires_redis,
                extra_args=tuple(extra),
            )
        )
    return cases


def _build_manifest(
    cases: list[MatrixCase],
    report_dir: Path,
    baseline_output: Path,
) -> dict[str, Any]:
    """Build a machine-readable matrix manifest."""

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "report_dir": str(report_dir),
        "baseline_output": str(baseline_output),
        "cases": [
            {
                "name": case.name,
                "layer": case.layer,
                "scenario": case.scenario,
                "requests": case.requests,
                "concurrency": case.concurrency,
                "purpose": case.purpose,
                "external": case.external,
                "requires_rabbitmq": case.requires_rabbitmq,
                "requires_redis": case.requires_redis,
                "command": _shell_join(case.command(report_dir)),
            }
            for case in cases
        ],
        "baseline_command": _shell_join(
            [
                sys.executable,
                "scripts/build_capacity_baseline.py",
                "--input-dir",
                str(report_dir),
                "--output",
                str(baseline_output),
            ]
        ),
    }


def _shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in command)


if __name__ == "__main__":
    raise SystemExit(main())
