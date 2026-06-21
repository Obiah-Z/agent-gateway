#!/usr/bin/env python3
"""Read-only disk usage scanner for the Gateway server-space-advisor skill."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Iterable


DEFAULT_EXCLUDES = {
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/snap",
    "/boot/efi",
    "/mnt",
    "/media",
}

SAFE_PATTERNS = {
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "node_modules",
    ".next",
    ".turbo",
    "dist",
    "build",
}

CONFIRM_PATTERNS = {
    ".venv",
    "venv",
    "env",
    ".cache",
    "docker",
    "backup",
    "backups",
    "dump",
    "downloads",
    "models",
    "datasets",
}

DO_NOT_TOUCH_PREFIXES = (
    "/var/lib/mysql",
    "/var/lib/postgresql",
    "/var/lib/redis",
    "/var/lib/docker/volumes",
    "/etc",
    "/root/.ssh",
    "/home/obiah/.ssh",
)


@dataclass(slots=True)
class DiskInfo:
    filesystem: str
    size: str
    used: str
    available: str
    percent: str
    mount: str


@dataclass(slots=True)
class PathUsage:
    path: str
    size_bytes: int | None
    size_human: str
    category_hint: str
    reason: str


@dataclass(slots=True)
class ScanResult:
    disks: list[DiskInfo]
    top_paths: list[PathUsage]
    large_files: list[PathUsage]
    skipped_paths: list[str]
    warnings: list[str]


def run_text(command: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def parse_df() -> list[DiskInfo]:
    code, stdout, _ = run_text(["df", "-hP"], timeout=15)
    if code != 0:
        return []
    rows: list[DiskInfo] = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        rows.append(
            DiskInfo(
                filesystem=parts[0],
                size=parts[1],
                used=parts[2],
                available=parts[3],
                percent=parts[4],
                mount=" ".join(parts[5:]),
            )
        )
    return rows


def human_to_bytes(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    suffix = raw[-1].upper()
    try:
        if suffix in units:
            return int(float(raw[:-1]) * units[suffix])
        return int(raw)
    except ValueError:
        return None


def format_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def should_skip(path: str) -> bool:
    normalized = os.path.abspath(path)
    return any(normalized == excluded or normalized.startswith(excluded + "/") for excluded in DEFAULT_EXCLUDES)


def classify(path: str) -> tuple[str, str]:
    normalized = os.path.abspath(path)
    lowered = normalized.lower()
    parts = set(part.lower() for part in normalized.split(os.sep) if part)
    name = os.path.basename(normalized).lower()

    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in DO_NOT_TOUCH_PREFIXES):
        return "不建议动", "系统、数据库、凭据或服务运行数据目录"
    if parts.intersection(SAFE_PATTERNS) or name in SAFE_PATTERNS:
        return "可安全清理", "常见缓存、构建产物或可重新生成目录"
    if lowered.endswith((".log", ".log.1", ".gz", ".zip", ".tar", ".tgz", ".tar.gz", ".bak", ".dump", ".sql")):
        return "需确认", "日志、归档、备份或导出文件需要确认保留策略"
    if any(pattern in lowered for pattern in CONFIRM_PATTERNS):
        return "需确认", "可能是依赖、镜像、备份、下载或业务数据"
    return "需确认", "未命中安全清理规则，需要人工确认用途"


def scan_top_paths(paths: Iterable[str], depth: int, limit: int, warnings: list[str]) -> list[PathUsage]:
    rows: list[PathUsage] = []
    for raw_path in paths:
        path = os.path.abspath(os.path.expanduser(raw_path))
        if should_skip(path):
            continue
        code, stdout, stderr = run_text(["du", "-xhd", str(depth), path], timeout=90)
        if code not in (0, 1):
            warnings.append(f"du scan failed for {path}: {stderr.strip() or 'unknown error'}")
            continue
        if stderr.strip():
            warnings.append(f"du scan partial for {path}: {stderr.strip()[:300]}")
        for line in stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            size_human, item_path = parts
            category, reason = classify(item_path)
            rows.append(PathUsage(item_path, human_to_bytes(size_human), size_human, category, reason))
    rows.sort(key=lambda item: item.size_bytes or -1, reverse=True)
    return rows[:limit]


def scan_large_files(paths: Iterable[str], min_size: str, limit: int, warnings: list[str]) -> list[PathUsage]:
    rows: list[PathUsage] = []
    for raw_path in paths:
        path = os.path.abspath(os.path.expanduser(raw_path))
        if should_skip(path):
            continue
        code, stdout, stderr = run_text(
            ["find", path, "-xdev", "-type", "f", "-size", f"+{min_size}", "-printf", "%s\t%p\n"],
            timeout=90,
        )
        if code not in (0, 1):
            warnings.append(f"find scan failed for {path}: {stderr.strip() or 'unknown error'}")
            continue
        if stderr.strip():
            warnings.append(f"find scan partial for {path}: {stderr.strip()[:300]}")
        for line in stdout.splitlines():
            size_text, _, item_path = line.partition("\t")
            try:
                size_bytes = int(size_text)
            except ValueError:
                continue
            category, reason = classify(item_path)
            rows.append(PathUsage(item_path, size_bytes, format_bytes(size_bytes), category, reason))
    rows.sort(key=lambda item: item.size_bytes or -1, reverse=True)
    return rows[:limit]


def print_markdown(result: ScanResult) -> None:
    print("**磁盘概览**")
    for disk in result.disks:
        print(f"- {disk.mount}: {disk.used}/{disk.size}，剩余 {disk.available}，使用率 {disk.percent}")
    if result.warnings:
        print("\n**扫描提示**")
        for warning in result.warnings:
            print(f"- {warning}")
    print("\n**目录占用 Top**")
    for item in result.top_paths:
        print(f"- {item.path}: {item.size_human}，分类建议：{item.category_hint}，原因：{item.reason}")
    print("\n**大文件 Top**")
    for item in result.large_files:
        print(f"- {item.path}: {item.size_human}，分类建议：{item.category_hint}，原因：{item.reason}")
    if result.skipped_paths:
        print("\n**已跳过路径**")
        for path in result.skipped_paths:
            print(f"- {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only disk usage scanner.")
    parser.add_argument("--paths", nargs="+", default=["/", "/home", "/var", "/tmp"])
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--large-file-min", default="200M")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    skipped = [
        os.path.abspath(os.path.expanduser(path))
        for path in args.paths
        if should_skip(os.path.abspath(os.path.expanduser(path)))
    ]
    warnings: list[str] = []
    result = ScanResult(
        disks=parse_df(),
        top_paths=scan_top_paths(args.paths, args.depth, args.limit, warnings),
        large_files=scan_large_files(args.paths, args.large_file_min, args.limit, warnings),
        skipped_paths=skipped,
        warnings=warnings,
    )

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
