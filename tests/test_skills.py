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
