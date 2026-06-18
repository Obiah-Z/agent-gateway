"""系统 prompt 组装。

PromptAssembler 负责把 workspace 中的身份、人格、工具说明、记忆、技能和运行时上下文
组装成一次模型调用的 system prompt。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_gateway.intelligence.memory import MemoryStore
from agent_gateway.intelligence.skills import SkillsManager
from agent_gateway.core.models import AgentConfig


BOOTSTRAP_FILES = (
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "MEMORY.md",
    "USER.md",
    "BOOTSTRAP.md",
    "AGENTS.md",
    "HEARTBEAT.md",
)


@dataclass(slots=True)
class BootstrapLoader:
    """从 workspace 读取全局和 Agent 局部 prompt 文件。"""

    workspace_root: Path
    per_file_limit: int = 20_000
    total_limit: int = 150_000

    def truncate_file(self, content: str, max_chars: int | None = None) -> str:
        """按字符数截断单个 prompt 文件，避免系统提示词过大。"""

        limit = max_chars or self.per_file_limit
        if len(content) <= limit:
            return content
        cut = content.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        return (
            content[:cut]
            + f"\n\n[... truncated ({len(content)} chars total, showing first {cut}) ...]"
        )

    def load(
        self,
        *,
        use_global_files: bool = True,
        prompt_dir: str = "",
    ) -> dict[str, str]:
        """加载全局 prompt，并用 Agent 局部 prompt 覆盖同名文件。"""

        loaded: dict[str, str] = {}
        global_loaded = self._load_from_dir(self.workspace_root) if use_global_files else {}
        loaded.update(global_loaded)
        if prompt_dir:
            prompt_path = (self.workspace_root / prompt_dir).resolve()
            try:
                prompt_path.relative_to(self.workspace_root.resolve())
            except ValueError:
                prompt_path = self.workspace_root
            if prompt_path.exists() and prompt_path.is_dir():
                loaded.update(self._load_from_dir(prompt_path))
        return loaded

    def _load_from_dir(self, root: Path) -> dict[str, str]:
        """从指定目录读取允许的 bootstrap 文件并应用总大小限制。"""

        loaded: dict[str, str] = {}
        total = 0
        for filename in BOOTSTRAP_FILES:
            path = root / filename
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            content = self.truncate_file(content)
            if total + len(content) > self.total_limit:
                remaining = self.total_limit - total
                if remaining <= 0:
                    break
                content = self.truncate_file(content, remaining)
            loaded[filename] = content
            total += len(content)
        return loaded


class PromptAssembler:
    """把 Agent 配置和 workspace 内容拼成模型 system prompt。"""

    def __init__(
        self,
        workspace_root: Path,
        *,
        memory_store: MemoryStore | None = None,
        skills_manager: SkillsManager | None = None,
    ) -> None:
        self.loader = BootstrapLoader(workspace_root)
        self.memory_store = memory_store
        self.skills_manager = skills_manager

    def build(
        self,
        agent: AgentConfig,
        *,
        mode: str = "full",
        channel: str = "gateway",
        user_text: str = "",
        runtime_context: dict[str, str] | None = None,
        memory_context: str = "",
    ) -> str:
        """构建一次模型调用的 system prompt。"""

        runtime_context = runtime_context or {}
        bootstrap = self.loader.load(
            use_global_files=agent.use_global_prompt_files,
            prompt_dir=agent.resolved_prompt_dir(),
        )
        sections: list[str] = []
        skills_block = (
            self.skills_manager.format_prompt_block()
            if self.skills_manager and agent.skills_enabled
            else ""
        )
        if (
            not memory_context
            and user_text
            and self.memory_store
            and agent.should_auto_recall_memory()
        ):
            # 自动记忆召回只在 Agent 配置允许时发生，避免所有后台任务都注入过多历史。
            memory_context = self.memory_store.auto_recall(user_text, top_k=agent.memory_top_k)

        identity = bootstrap.get("IDENTITY.md", "").strip()
        sections.append(identity or "You are a helpful personal AI assistant.")

        soul = bootstrap.get("SOUL.md", "").strip()
        if mode == "full" and soul:
            sections.append(f"## Personality\n\n{soul}")
        elif mode == "full" and agent.personality:
            sections.append(f"## Personality\n\n{agent.personality}")

        tools_guidance = bootstrap.get("TOOLS.md", "").strip()
        if tools_guidance:
            sections.append(f"## Tool Usage Guidelines\n\n{tools_guidance}")

        if mode == "full" and skills_block:
            sections.append(skills_block)

        if agent.uses_memory(mode):
            memory = bootstrap.get("MEMORY.md", "").strip()
            memory_parts: list[str] = []
            if memory:
                memory_parts.append(f"### Evergreen Memory\n\n{memory}")
            if memory_context:
                memory_parts.append(f"### Recalled Memories (auto-searched)\n\n{memory_context}")
            if memory_parts:
                sections.append("## Memory\n\n" + "\n\n".join(memory_parts))
            sections.append(
                "## Memory Instructions\n\n"
                "- Use memory_write to save important user facts and preferences.\n"
                "- Reference remembered facts naturally when useful.\n"
                "- Use memory_search when you need precise recall from past context."
            )

        extras = []
        for filename in ("USER.md", "BOOTSTRAP.md", "AGENTS.md", "HEARTBEAT.md"):
            value = bootstrap.get(filename, "").strip()
            if value:
                extras.append(f"### {filename}\n{value}")
        if extras:
            sections.append("## Workspace Context\n\n" + "\n\n".join(extras))

        if runtime_context:
            runtime_lines = [f"- {key}: {value}" for key, value in runtime_context.items()]
            sections.append("## Runtime Context\n\n" + "\n".join(runtime_lines))
            disabled_tools = str(runtime_context.get("disabled_tools", "")).strip()
            if disabled_tools:
                sections.append(
                    "## Runtime Tool Restrictions\n\n"
                    f"The following tools are disabled for this turn and must not be used: {disabled_tools}."
                )

        channel_hints = {
            "terminal": "You are responding via a terminal REPL. Markdown is supported.",
            "cli": "You are responding via a local CLI channel. Keep replies concise and readable.",
            "telegram": "You are responding via Telegram. Keep messages concise and split long replies when needed.",
            "feishu": "You are responding via Feishu. Keep responses short and compatible with chat delivery.",
            "websocket": "You are responding through a gateway websocket client.",
            "gateway": "You are responding through the gateway runtime.",
        }
        sections.append(f"## Channel\n\n{channel_hints.get(channel, f'You are responding via {channel}.')}")
        sections.append(agent.basic_system_prompt())
        return "\n\n".join(section for section in sections if section)
