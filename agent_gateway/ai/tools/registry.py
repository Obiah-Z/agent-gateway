from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[..., str]


@dataclass(slots=True)
class RegisteredTool:
    """单个工具的注册描述。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    tags: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        """导出为模型 tool calling 所需的 schema。"""

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """工具注册表。

    负责统一维护工具元数据、按名称/标签筛选，以及执行真实 handler。
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """注册或覆盖一个工具。"""

        self._tools[tool.name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        """按名称获取工具定义。"""

        return self._tools.get(name)

    def names(self) -> list[str]:
        """返回当前所有工具名。"""

        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """导出全部工具 schema。"""

        return [tool.schema() for tool in self._tools.values()]

    def schemas_for(self, allowed_names: list[str]) -> list[dict[str, Any]]:
        """只导出允许名单中的工具 schema。"""

        return [
            tool.schema()
            for name, tool in self._tools.items()
            if name in set(allowed_names)
        ]

    def describe_tools(self) -> list[dict[str, Any]]:
        """返回适合控制面展示的工具描述。"""

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "tags": list(tool.tags),
            }
            for tool in self._tools.values()
        ]

    def names_for_tags(self, tags: list[str]) -> list[str]:
        """按 capability tag 反查对应工具名。"""

        wanted = {tag.strip().lower() for tag in tags if tag.strip()}
        names: list[str] = []
        for tool in self._tools.values():
            current_tags = {tag.strip().lower() for tag in tool.tags}
            if wanted & current_tags:
                names.append(tool.name)
        return names

    def dispatch(
        self,
        name: str,
        tool_input: dict[str, Any],
        *,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        """执行指定工具，并把异常转成模型可读错误文本。"""

        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            call_input = dict(tool_input)
            signature = inspect.signature(tool.handler)
            if "__runtime_context" in signature.parameters:
                call_input["__runtime_context"] = dict(runtime_context or {})
            return tool.handler(**call_input)
        except Exception as exc:
            return f"Error: {name} failed: {exc}"
