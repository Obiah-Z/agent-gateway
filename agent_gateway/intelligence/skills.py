from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    invocation: str
    body: str
    path: str


class SkillsManager:
    def __init__(
        self,
        workspace_root: Path,
        *,
        max_skills: int = 64,
        max_prompt_chars: int = 30_000,
    ) -> None:
        self.workspace_root = workspace_root
        self.max_skills = max_skills
        self.max_prompt_chars = max_prompt_chars
        self.skills: list[SkillDefinition] = []

    def discover(self, extra_dirs: list[Path] | None = None) -> None:
        scan_order: list[Path] = []
        if extra_dirs:
            scan_order.extend(extra_dirs)
        scan_order.extend(
            [
                self.workspace_root / "skills",
                self.workspace_root / ".skills",
                self.workspace_root / ".agents" / "skills",
                Path.cwd() / ".agents" / "skills",
                Path.cwd() / "skills",
            ]
        )

        seen: dict[str, SkillDefinition] = {}
        for base in scan_order:
            for skill in self._scan_dir(base):
                seen[skill.name] = skill
        self.skills = list(seen.values())[: self.max_skills]

    def snapshot(self) -> list[dict[str, str]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "invocation": skill.invocation,
                "path": skill.path,
            }
            for skill in self.skills
        ]

    def format_prompt_block(self) -> str:
        if not self.skills:
            return ""
        lines = ["## Available Skills", ""]
        total = 0
        for skill in self.skills:
            block = (
                f"### Skill: {skill.name}\n"
                f"Description: {skill.description}\n"
                f"Invocation: {skill.invocation}\n"
            )
            if skill.body:
                block += f"\n{skill.body}\n"
            block += "\n"
            if total + len(block) > self.max_prompt_chars:
                lines.append("(... more skills truncated)")
                break
            lines.append(block)
            total += len(block)
        return "\n".join(lines)

    def _scan_dir(self, base: Path) -> list[SkillDefinition]:
        found: list[SkillDefinition] = []
        if not base.is_dir():
            return found
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
            except OSError:
                continue
            metadata = self._parse_frontmatter(content)
            name = metadata.get("name", "")
            if not name:
                continue
            body = self._extract_body(content)
            found.append(
                SkillDefinition(
                    name=name,
                    description=metadata.get("description", ""),
                    invocation=metadata.get("invocation", ""),
                    body=body,
                    path=str(child),
                )
            )
        return found

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        if not text.startswith("---"):
            return meta
        parts = text.split("---", 2)
        if len(parts) < 3:
            return meta
        for line in parts[1].strip().splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        return meta

    @staticmethod
    def _extract_body(text: str) -> str:
        if not text.startswith("---"):
            return text.strip()
        parts = text.split("---", 2)
        if len(parts) < 3:
            return ""
        return parts[2].strip()
