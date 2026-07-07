from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


@dataclass(slots=True)
class PersonalTodo:
    """个人秘书待办事项。"""

    id: str
    title: str
    status: str
    priority: str
    due_at: str
    notes: str
    created_at: str
    completed_at: str = ""


class PersonalStore:
    """个人秘书结构化数据存储。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.root = workspace_root / "personal"
        self.root.mkdir(parents=True, exist_ok=True)

    def add_todo(
        self,
        title: str,
        *,
        priority: str = "normal",
        due_at: str = "",
        notes: str = "",
        user_scope: str = "",
    ) -> dict[str, Any]:
        """新增一个个人待办。"""

        todo = PersonalTodo(
            id=f"todo-{uuid.uuid4().hex[:10]}",
            title=title.strip(),
            status="open",
            priority=self._normalize_priority(priority),
            due_at=due_at.strip(),
            notes=notes.strip(),
            created_at=self._now(),
        )
        self._append_jsonl(self._todos_path(user_scope), self._todo_to_dict(todo))
        return self._todo_to_dict(todo)

    def list_todos(
        self,
        *,
        status: str = "open",
        limit: int = 20,
        user_scope: str = "",
    ) -> list[dict[str, Any]]:
        """列出个人待办。"""

        safe_limit = max(1, min(int(limit), 100))
        wanted = status.strip().lower()
        rows = self._read_jsonl(self._todos_path(user_scope))
        if wanted and wanted != "all":
            rows = [row for row in rows if str(row.get("status", "")).lower() == wanted]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return rows[:safe_limit]

    def complete_todo(
        self,
        todo_id: str,
        *,
        result: str = "",
        user_scope: str = "",
    ) -> dict[str, Any] | None:
        """完成指定待办。"""

        rows = self._read_jsonl(self._todos_path(user_scope))
        now = self._now()
        updated = None
        for row in rows:
            if str(row.get("id", "")) == todo_id:
                row["status"] = "done"
                row["completed_at"] = now
                if result.strip():
                    row["result"] = result.strip()
                updated = row
                break
        if updated is None:
            return None
        self._write_jsonl(self._todos_path(user_scope), rows)
        return updated

    def add_review(
        self,
        summary: str,
        *,
        completed: list[str] | None = None,
        blockers: list[str] | None = None,
        next_step: str = "",
        user_scope: str = "",
    ) -> dict[str, Any]:
        """写入一次个人复盘。"""

        row = {
            "id": f"review-{uuid.uuid4().hex[:10]}",
            "ts": self._now(),
            "summary": summary.strip(),
            "completed": self._clean_list(completed or []),
            "blockers": self._clean_list(blockers or []),
            "next_step": next_step.strip(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
        }
        self._append_jsonl(self._reviews_path(user_scope), row)
        return row

    def recent_reviews(self, *, limit: int = 5, user_scope: str = "") -> list[dict[str, Any]]:
        """读取最近个人复盘。"""

        safe_limit = max(1, min(int(limit), 50))
        rows = self._read_jsonl(self._reviews_path(user_scope))
        rows.sort(key=lambda row: str(row.get("ts", "")), reverse=True)
        return rows[:safe_limit]

    def _scope_dir(self, user_scope: str) -> Path:
        scope = MemoryStore.normalize_scope(user_scope)
        name = MemoryStore._scope_dir_name(scope) if scope else "global"
        path = self.root / "users" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _todos_path(self, user_scope: str) -> Path:
        return self._scope_dir(user_scope) / "todos.jsonl"

    def _reviews_path(self, user_scope: str) -> Path:
        return self._scope_dir(user_scope) / "reviews.jsonl"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_priority(value: str) -> str:
        priority = value.strip().lower()
        return priority if priority in {"low", "normal", "high", "urgent"} else "normal"

    @staticmethod
    def _clean_list(items: list[str]) -> list[str]:
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _todo_to_dict(todo: PersonalTodo) -> dict[str, Any]:
        return {
            "id": todo.id,
            "title": todo.title,
            "status": todo.status,
            "priority": todo.priority,
            "due_at": todo.due_at,
            "notes": todo.notes,
            "created_at": todo.created_at,
            "completed_at": todo.completed_at,
        }

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows

    @staticmethod
    def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def register_personal_tools(registry: ToolRegistry, personal_store: PersonalStore) -> None:
    """注册个人秘书结构化工具。"""

    def _scope(__runtime_context: dict[str, Any] | None, user_scope: str = "") -> str:
        return user_scope or str((__runtime_context or {}).get("memory_user_scope", ""))

    def personal_todo_add(
        title: str,
        priority: str = "normal",
        due_at: str = "",
        notes: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not title.strip():
            return "Error: title is required"
        row = personal_store.add_todo(
            title,
            priority=priority,
            due_at=due_at,
            notes=notes,
            user_scope=_scope(__runtime_context, user_scope),
        )
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_todo_list(
        status: str = "open",
        limit: int = 20,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        rows = personal_store.list_todos(
            status=status,
            limit=limit,
            user_scope=_scope(__runtime_context, user_scope),
        )
        return json.dumps({"items": rows, "count": len(rows)}, ensure_ascii=False, indent=2)

    def personal_todo_complete(
        todo_id: str,
        result: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        row = personal_store.complete_todo(
            todo_id,
            result=result,
            user_scope=_scope(__runtime_context, user_scope),
        )
        if row is None:
            return f"Error: todo not found: {todo_id}"
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_review_add(
        summary: str,
        completed: list[str] | None = None,
        blockers: list[str] | None = None,
        next_step: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not summary.strip():
            return "Error: summary is required"
        row = personal_store.add_review(
            summary,
            completed=completed or [],
            blockers=blockers or [],
            next_step=next_step,
            user_scope=_scope(__runtime_context, user_scope),
        )
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_review_recent(
        limit: int = 5,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        rows = personal_store.recent_reviews(
            limit=limit,
            user_scope=_scope(__runtime_context, user_scope),
        )
        return json.dumps({"items": rows, "count": len(rows)}, ensure_ascii=False, indent=2)

    registry.register(
        RegisteredTool(
            name="personal_todo_add",
            description="Add a structured personal todo for the current user.",
            input_schema={
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "due_at": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            handler=personal_todo_add,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_todo_list",
            description="List structured personal todos for the current user.",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "done", "all"]},
                    "limit": {"type": "integer"},
                },
            },
            handler=personal_todo_list,
            tags=("personal", "todo", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_todo_complete",
            description="Mark a structured personal todo as done.",
            input_schema={
                "type": "object",
                "required": ["todo_id"],
                "properties": {
                    "todo_id": {"type": "string"},
                    "result": {"type": "string"},
                },
            },
            handler=personal_todo_complete,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_review_add",
            description="Save a structured personal daily review.",
            input_schema={
                "type": "object",
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string"},
                    "completed": {"type": "array", "items": {"type": "string"}},
                    "blockers": {"type": "array", "items": {"type": "string"}},
                    "next_step": {"type": "string"},
                },
            },
            handler=personal_review_add,
            tags=("personal", "review", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_review_recent",
            description="Read recent structured personal daily reviews.",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
            handler=personal_review_recent,
            tags=("personal", "review", "read"),
        )
    )
