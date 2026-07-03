import importlib.util
from pathlib import Path
from types import SimpleNamespace

from agent_gateway.ai.context.skills import SkillsManager


def _load_draw_export_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "skills"
        / "draw-skill"
        / "scripts"
        / "export_drawio.py"
    )
    spec = importlib.util.spec_from_file_location("draw_skill_export_drawio", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_draw_create_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "skills"
        / "draw-skill"
        / "scripts"
        / "create_academic_drawio.py"
    )
    spec = importlib.util.spec_from_file_location("draw_skill_create_academic_drawio", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skills_manager_discovers_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "example"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Example Skill\n"
        "description: Example description\n"
        "invocation: $example\n"
        "---\n"
        "\n"
        "Skill body content.\n",
        encoding="utf-8",
    )

    manager = SkillsManager(workspace)
    manager.discover()

    assert len(manager.skills) == 1
    assert manager.skills[0].name == "Example Skill"
    assert "Example description" in manager.format_prompt_block()


def test_workspace_server_space_advisor_skill_is_discoverable() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"

    manager = SkillsManager(workspace)
    manager.discover()

    names = {skill.name for skill in manager.skills}
    assert "server-space-advisor" in names
    prompt = manager.format_prompt_block()
    assert "只读分析服务器磁盘占用" in prompt
    assert "禁止删除" in prompt


def test_workspace_github_skill_finder_skill_is_discoverable() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"

    manager = SkillsManager(workspace)
    manager.discover()

    names = {skill.name for skill in manager.skills}
    assert "github-skill-finder" in names
    prompt = manager.format_prompt_block()
    assert "GitHub 热门 Skill 发现" in prompt
    assert "/github-skill-finder" in prompt


def test_workspace_github_repo_analyzer_skill_is_discoverable() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"

    manager = SkillsManager(workspace)
    manager.discover()

    names = {skill.name for skill in manager.skills}
    assert "github-repo-analyzer" in names
    prompt = manager.format_prompt_block()
    assert "GitHub 仓库分析" in prompt
    assert "workspace/reports/github-repos/仓库分析-{owner}-{repo}.md" in prompt
    assert "/github-repo-analyzer" in prompt


def test_workspace_draw_skill_is_discoverable() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"

    manager = SkillsManager(workspace)
    manager.discover()

    names = {skill.name for skill in manager.skills}
    assert "draw-skill" in names
    prompt = manager.format_prompt_block()
    assert "draw.io" in prompt
    assert "workspace/reports/diagrams" in prompt


def test_draw_skill_export_output_is_restricted_to_shared_diagrams_dir() -> None:
    export_drawio = _load_draw_export_module()
    repo_root = Path(__file__).resolve().parents[1]
    input_path = repo_root / "workspace" / "reports" / "diagrams" / "示例架构图.drawio"

    allowed = export_drawio.resolve_output(
        input_path,
        "workspace/reports/diagrams/导出.png",
        "png",
        recursive=False,
    )

    assert allowed == repo_root / "workspace" / "reports" / "diagrams" / "导出.png"
    for rejected in ("workspace/reports/other/导出.png", "/tmp/导出.png"):
        try:
            export_drawio.resolve_output(input_path, rejected, "png", recursive=False)
        except SystemExit as exc:
            assert "workspace/reports/diagrams" in str(exc)
        else:
            raise AssertionError(f"unexpectedly allowed output path: {rejected}")


def test_draw_skill_fallback_svg_export_without_drawio_cli(tmp_path: Path) -> None:
    export_drawio = _load_draw_export_module()
    output = tmp_path / "fallback.svg"

    export_drawio.fallback_svg_export(
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "reports"
        / "diagrams"
        / "示例流程图.drawio",
        output,
    )

    content = output.read_text(encoding="utf-8")
    assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<svg" in content
    assert "<rect" in content or "<polygon" in content


def test_draw_skill_uses_fallback_when_drawio_creates_no_output(tmp_path: Path, monkeypatch) -> None:
    export_drawio = _load_draw_export_module()
    repo_root = Path(__file__).resolve().parents[1]
    input_path = repo_root / "workspace" / "reports" / "diagrams" / "示例流程图.drawio"
    output_path = repo_root / "workspace" / "reports" / "diagrams" / "fallback-no-output-test.svg"

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(export_drawio, "drawio_command", lambda: "drawio")
    monkeypatch.setattr(export_drawio.subprocess, "run", lambda *args, **kwargs: FakeResult())
    try:
        assert not export_drawio.run_drawio_export(
            SimpleNamespace(
                format="svg",
                recursive=False,
                page=None,
                all_pages=False,
                transparent=False,
                border=None,
                scale=None,
                width=None,
                height=None,
            ),
            input_path,
            output_path,
        )
    finally:
        output_path.unlink(missing_ok=True)


def test_draw_skill_create_output_is_restricted_to_shared_diagrams_dir() -> None:
    create_drawio = _load_draw_create_module()
    repo_root = Path(__file__).resolve().parents[1]

    allowed = create_drawio.resolve_output("workspace/reports/diagrams/新图.drawio")

    assert allowed == repo_root / "workspace" / "reports" / "diagrams" / "新图.drawio"
    for rejected in ("workspace/reports/other/新图.drawio", "/tmp/新图.drawio", "workspace/reports/diagrams/新图.png"):
        try:
            create_drawio.resolve_output(rejected)
        except SystemExit as exc:
            assert "workspace/reports/diagrams" in str(exc) or ".drawio" in str(exc)
        else:
            raise AssertionError(f"unexpectedly allowed output path: {rejected}")


def test_workspace_active_skills_match_pruned_skill_set() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"

    manager = SkillsManager(workspace)
    manager.discover()

    names = {skill.name for skill in manager.skills}
    assert {
        "draw-skill",
        "github-repo-analyzer",
        "github-skill-finder",
        "server-space-advisor",
    }.issubset(names)
    assert {
        "article-writing",
        "content-distribution",
        "frontend-slides",
        "video-editing",
        "audio-media",
    }.isdisjoint(names)
