#!/usr/bin/env python3
"""Export draw.io files into the shared Gateway workspace reports directory."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


SUPPORTED_FORMATS = {"png", "jpg", "svg", "pdf"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def workspace_root() -> Path:
    raw = os.getenv("GATEWAY_WORKSPACE_ROOT", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (repo_root() / candidate).resolve()
    return (repo_root() / "workspace").resolve()


def reports_dir() -> Path:
    return (workspace_root() / "reports" / "diagrams").resolve()


def resolve_workspace_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    root = repo_root()
    workspace = workspace_root()
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0] == "workspace":
            candidate = root / candidate
        else:
            candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit(f"input must be inside workspace: {raw_path}") from exc
    return resolved


def resolve_output(input_path: Path, raw_output: str | None, fmt: str, *, recursive: bool) -> Path:
    if raw_output:
        candidate = Path(raw_output).expanduser()
        if not candidate.is_absolute():
            if candidate.parts and candidate.parts[0] == "workspace":
                candidate = repo_root() / candidate
            else:
                candidate = workspace_root() / candidate
        output = candidate.resolve()
    elif recursive:
        output = reports_dir()
    else:
        output = reports_dir() / f"{input_path.stem}.{fmt}"
    try:
        output.relative_to(reports_dir())
    except ValueError as exc:
        raise SystemExit("output must be inside workspace/reports/diagrams") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def drawio_command() -> str:
    for command in ("drawio", "/snap/bin/drawio"):
        if shutil.which(command) or Path(command).exists():
            return command
    raise SystemExit("drawio CLI not found; install diagrams.net/drawio CLI first")


def build_command(args: argparse.Namespace, input_path: Path, output_path: Path) -> list[str]:
    command = [
        drawio_command(),
        "-x",
        "-f",
        args.format,
    ]
    if args.recursive:
        command.append("-r")
    if args.page is not None:
        command.extend(["-p", str(args.page)])
    if args.all_pages:
        command.append("-a")
    if args.transparent:
        command.append("-t")
    if args.border is not None:
        command.extend(["-b", str(args.border)])
    if args.scale is not None:
        command.extend(["-s", str(args.scale)])
    if args.width is not None:
        command.extend(["--width", str(args.width)])
    if args.height is not None:
        command.extend(["--height", str(args.height)])
    command.extend(["-o", str(output_path), str(input_path)])
    return command


def validate_input(input_path: Path, *, recursive: bool) -> None:
    if recursive:
        if not input_path.is_dir():
            raise SystemExit("--recursive requires an input directory")
        if not any(input_path.rglob("*.drawio")):
            raise SystemExit(f"input directory contains no .drawio files: {input_path}")
        return
    if input_path.suffix.lower() != ".drawio":
        raise SystemExit("input file must end with .drawio")
    if not input_path.is_file():
        raise SystemExit(f"input file not found: {input_path}")


def validate_output(output_path: Path, *, recursive: bool, fmt: str) -> None:
    if recursive:
        if not output_path.is_dir():
            raise SystemExit(f"recursive export did not create output directory: {output_path}")
        if not any(path.is_file() and path.stat().st_size > 0 for path in output_path.rglob(f"*.{fmt}")):
            raise SystemExit(f"recursive export did not create non-empty .{fmt} files: {output_path}")
        return
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise SystemExit(f"export did not create a non-empty file: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="workspace-relative or absolute .drawio path")
    parser.add_argument("--format", choices=sorted(SUPPORTED_FORMATS), default="png")
    parser.add_argument("--output", help="output path under workspace/reports/diagrams")
    parser.add_argument("--page", type=int, help="1-based draw.io page index")
    parser.add_argument("--transparent", action="store_true", help="transparent PNG background")
    parser.add_argument("--border", type=int, help="border width in pixels")
    parser.add_argument("--scale", type=float, help="export scale")
    parser.add_argument("--width", type=int, help="target output width")
    parser.add_argument("--height", type=int, help="target output height")
    parser.add_argument("-a", "--all-pages", action="store_true", help="export all pages")
    parser.add_argument("-r", "--recursive", action="store_true", help="recursively export a folder")
    args = parser.parse_args()

    input_path = resolve_workspace_path(args.input)
    validate_input(input_path, recursive=args.recursive)

    output_path = resolve_output(input_path, args.output, args.format, recursive=args.recursive)
    result = subprocess.run(
        build_command(args, input_path, output_path),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SystemExit(
            "drawio export failed:"
            f" exit={result.returncode}"
            f" stderr={result.stderr.strip()[:500]}"
        )
    validate_output(output_path, recursive=args.recursive, fmt=args.format)
    print(output_path.relative_to(repo_root()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
