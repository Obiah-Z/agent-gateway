#!/usr/bin/env python3
"""Export draw.io files into the shared Gateway workspace reports directory."""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from html import escape
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


def drawio_command() -> str | None:
    for command in ("drawio", "/snap/bin/drawio"):
        if shutil.which(command) or Path(command).exists():
            return command
    return None


def build_command(args: argparse.Namespace, input_path: Path, output_path: Path) -> list[str]:
    command_name = drawio_command()
    if command_name is None:
        raise SystemExit("drawio CLI not found")
    command = [
        command_name,
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


def _cell_geometry(cell: ET.Element) -> tuple[float, float, float, float] | None:
    geometry = None
    for child in cell:
        if child.tag == "mxGeometry":
            geometry = child
            break
    if geometry is None and "mxGeometry" in cell.attrib:
        try:
            raw_geometry = ast.literal_eval(cell.attrib["mxGeometry"])
        except (SyntaxError, ValueError):
            raw_geometry = {}
        if isinstance(raw_geometry, dict):
            try:
                return (
                    float(raw_geometry.get("x", "0")),
                    float(raw_geometry.get("y", "0")),
                    float(raw_geometry.get("width", "0")),
                    float(raw_geometry.get("height", "0")),
                )
            except ValueError:
                return None
    if geometry is None:
        return None
    try:
        return (
            float(geometry.attrib.get("x", "0")),
            float(geometry.attrib.get("y", "0")),
            float(geometry.attrib.get("width", "0")),
            float(geometry.attrib.get("height", "0")),
        )
    except ValueError:
        return None


def _extract_cells(input_path: Path) -> tuple[list[ET.Element], dict[str, tuple[float, float, float, float]]]:
    tree = ET.parse(input_path)
    root = tree.getroot()
    cells = list(root.iter("mxCell"))
    vertices: dict[str, tuple[float, float, float, float]] = {}
    for cell in cells:
        if cell.attrib.get("vertex") != "1":
            continue
        geometry = _cell_geometry(cell)
        if geometry is not None:
            vertices[cell.attrib["id"]] = geometry
    return cells, vertices


def _svg_text(value: str, x: float, y: float, width: float, height: float) -> str:
    lines = [line for line in value.replace("&#10;", "\n").splitlines() if line.strip()]
    if not lines:
        return ""
    line_height = 14
    start_y = y + height / 2 - ((len(lines) - 1) * line_height / 2) + 4
    rendered = []
    for index, line in enumerate(lines[:8]):
        rendered.append(
            f'<text x="{x + width / 2:.1f}" y="{start_y + index * line_height:.1f}" '
            f'text-anchor="middle">{escape(line.strip())}</text>'
        )
    return "\n".join(rendered)


def _svg_vertex(cell: ET.Element, geometry: tuple[float, float, float, float]) -> str:
    x, y, width, height = geometry
    value = cell.attrib.get("value", "")
    style = cell.attrib.get("style", "")
    text = _svg_text(value, x, y, width, height)
    if "rhombus=1" in style:
        points = [
            (x + width / 2, y),
            (x + width, y + height / 2),
            (x + width / 2, y + height),
            (x, y + height / 2),
        ]
        point_text = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        shape = f'<polygon points="{point_text}" fill="#fff" stroke="#000" stroke-width="1"/>'
    else:
        rx = 18 if "rounded=1" in style else 0
        shape = (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
            f'rx="{rx}" ry="{rx}" fill="#fff" stroke="#000" stroke-width="1"/>'
        )
    return f"{shape}\n{text}" if text else shape


def _edge_points(
    cell: ET.Element,
    vertices: dict[str, tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    source = vertices.get(cell.attrib.get("source", ""))
    target = vertices.get(cell.attrib.get("target", ""))
    if source is None or target is None:
        return None
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    return (sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2)


def fallback_svg_export(input_path: Path, output_path: Path) -> None:
    """在没有 drawio CLI 时生成简化 SVG 预览，避免任务卡死在导出环节。"""

    cells, vertices = _extract_cells(input_path)
    if not vertices:
        raise SystemExit("fallback SVG export failed: no vertex cells found")
    min_x = min(x for x, _, _, _ in vertices.values()) - 30
    min_y = min(y for _, y, _, _ in vertices.values()) - 30
    max_x = max(x + width for x, _, width, _ in vertices.values()) + 30
    max_y = max(y + height for _, y, _, height in vertices.values()) + 30
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.1f} {min_y:.1f} '
            f'{max_x - min_x:.1f} {max_y - min_y:.1f}" '
            f'width="{max_x - min_x:.0f}" height="{max_y - min_y:.0f}" '
            'style="font-family:SimSun,serif;font-size:12px">'
        ),
        '<defs><marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">'
        '<path d="M0,0 L8,3 L0,6 Z" fill="#000"/></marker></defs>',
        f'<rect x="{min_x:.1f}" y="{min_y:.1f}" width="{max_x - min_x:.1f}" '
        f'height="{max_y - min_y:.1f}" fill="#fff"/>',
    ]
    for cell in cells:
        if cell.attrib.get("edge") != "1":
            continue
        points = _edge_points(cell, vertices)
        if points is None:
            continue
        sx, sy, tx, ty = points
        parts.append(
            f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" '
            'stroke="#000" stroke-width="1" marker-end="url(#arrow)"/>'
        )
    for cell_id, geometry in vertices.items():
        cell = next(cell for cell in cells if cell.attrib.get("id") == cell_id)
        parts.append(_svg_vertex(cell, geometry))
    parts.append("</svg>")
    output_path.write_text("\n".join(parts), encoding="utf-8")


def image_convert_command() -> list[str] | None:
    for command in ("rsvg-convert", "magick", "convert"):
        if shutil.which(command):
            if command == "rsvg-convert":
                return [command, "-o"]
            return [command]
    return None


def fallback_image_export(input_path: Path, output_path: Path, fmt: str) -> None:
    if fmt == "pdf":
        raise SystemExit("drawio CLI is required for PDF export")
    svg_path = output_path.with_suffix(".svg")
    fallback_svg_export(input_path, svg_path)
    if fmt == "svg":
        if svg_path != output_path:
            svg_path.replace(output_path)
        return
    command = image_convert_command()
    if command is None:
        raise SystemExit(
            "drawio CLI not found and no SVG converter found; install imagemagick or librsvg2-bin"
        )
    if command[0] == "rsvg-convert":
        result = subprocess.run(
            [*command, str(output_path), str(svg_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    else:
        result = subprocess.run(
            [*command, str(svg_path), str(output_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        raise SystemExit(
            "fallback image export failed:"
            f" exit={result.returncode}"
            f" stderr={result.stderr.strip()[:500]}"
        )


def run_drawio_export(args: argparse.Namespace, input_path: Path, output_path: Path) -> bool:
    result = subprocess.run(
        build_command(args, input_path, output_path),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if args.format in {"svg", "png", "jpg"} and not args.recursive:
            print(
                "drawio export failed; falling back to built-in renderer:"
                f" exit={result.returncode}"
                f" stderr={result.stderr.strip()[:300]}",
                file=sys.stderr,
            )
            return False
        raise SystemExit(
            "drawio export failed:"
            f" exit={result.returncode}"
            f" stderr={result.stderr.strip()[:500]}"
        )
    if args.recursive:
        return True
    if output_path.is_file() and output_path.stat().st_size > 0:
        return True
    if args.format in {"svg", "png", "jpg"}:
        print(
            "drawio export produced no output; falling back to built-in renderer",
            file=sys.stderr,
        )
        return False
    return True


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
    command_name = drawio_command()
    if command_name is None:
        if args.recursive:
            raise SystemExit("drawio CLI is required for recursive export")
        fallback_image_export(input_path, output_path, args.format)
    else:
        if not run_drawio_export(args, input_path, output_path):
            fallback_image_export(input_path, output_path, args.format)
    validate_output(output_path, recursive=args.recursive, fmt=args.format)
    print(output_path.relative_to(repo_root()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
