from pathlib import Path

from agent_gateway.ai.context.skills import SkillsManager


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
