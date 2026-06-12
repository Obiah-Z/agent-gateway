from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[..., str]


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    tags: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def schemas_for(self, allowed_names: list[str]) -> list[dict[str, Any]]:
        return [
            tool.schema()
            for name, tool in self._tools.items()
            if name in set(allowed_names)
        ]

    def describe_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "tags": list(tool.tags),
            }
            for tool in self._tools.values()
        ]

    def names_for_tags(self, tags: list[str]) -> list[str]:
        wanted = {tag.strip().lower() for tag in tags if tag.strip()}
        names: list[str] = []
        for tool in self._tools.values():
            current_tags = {tag.strip().lower() for tag in tool.tags}
            if wanted & current_tags:
                names.append(tool.name)
        return names

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return tool.handler(**tool_input)
        except Exception as exc:
            return f"Error: {name} failed: {exc}"
