from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


DEFAULT_LOG_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _clean_strings(items: list[object] | None) -> list[str]:
    return [str(item).strip() for item in items or [] if str(item).strip()]


def _markdown_bullets(items: list[object] | None) -> str:
    cleaned = _clean_strings(items)
    if not cleaned:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in cleaned)


@dataclass(slots=True)
class InternshipLog:
    """实习记录助手的单条事实记录。"""

    id: str
    log_date: str
    category: str
    title: str
    content: str
    project: str
    tags: list[str]
    people: list[str]
    next_actions: list[str]
    created_at: str


class InternshipStore:
    """张海波实习过程记录的结构化数据存储。"""

    VALID_CATEGORIES = {
        "task",
        "meeting",
        "learning",
        "blocker",
        "feedback",
        "achievement",
        "reflection",
        "other",
    }

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.root = workspace_root / "internship"
        self.root.mkdir(parents=True, exist_ok=True)

    def add_log(
        self,
        title: str,
        content: str,
        *,
        log_date: str = "",
        category: str = "task",
        project: str = "",
        tags: list[str] | None = None,
        people: list[str] | None = None,
        next_actions: list[str] | None = None,
        user_scope: str = "",
    ) -> dict[str, Any]:
        """新增一条实习记录。"""

        now = self._now()
        log = InternshipLog(
            id=f"internship-{uuid.uuid4().hex[:10]}",
            log_date=self._normalize_date(log_date),
            category=self._normalize_category(category),
            title=title.strip(),
            content=content.strip(),
            project=project.strip(),
            tags=_clean_strings(tags),
            people=_clean_strings(people),
            next_actions=_clean_strings(next_actions),
            created_at=now,
        )
        row = self._log_to_dict(log)
        self._append_jsonl(self._logs_path(user_scope), row)
        return row

    def list_logs(
        self,
        *,
        log_date: str = "",
        category: str = "all",
        project: str = "",
        limit: int = 20,
        user_scope: str = "",
    ) -> list[dict[str, Any]]:
        """按日期、类别和项目列出实习记录。"""

        safe_limit = max(1, min(int(limit), 100))
        wanted_date = log_date.strip()
        wanted_category = category.strip().lower()
        wanted_project = project.strip().lower()
        rows = self._read_jsonl(self._logs_path(user_scope))
        if wanted_date:
            rows = [row for row in rows if str(row.get("log_date", "")) == wanted_date]
        if wanted_category and wanted_category != "all":
            rows = [row for row in rows if str(row.get("category", "")).lower() == wanted_category]
        if wanted_project:
            rows = [row for row in rows if wanted_project in str(row.get("project", "")).lower()]
        rows.sort(key=lambda row: (str(row.get("log_date", "")), str(row.get("created_at", ""))), reverse=True)
        return rows[:safe_limit]

    def search_logs(
        self,
        query: str,
        *,
        limit: int = 20,
        user_scope: str = "",
    ) -> list[dict[str, Any]]:
        """搜索标题、内容、项目、标签、相关人和下一步。"""

        normalized_query = " ".join(query.strip().split()).lower()
        if not normalized_query:
            return []
        matches = []
        rows = self._read_jsonl(self._logs_path(user_scope))
        rows.sort(key=lambda row: (str(row.get("log_date", "")), str(row.get("created_at", ""))), reverse=True)
        for row in rows:
            haystack = " ".join(
                [
                    str(row.get("title", "")),
                    str(row.get("content", "")),
                    str(row.get("project", "")),
                    " ".join(str(item) for item in row.get("tags", []) if item),
                    " ".join(str(item) for item in row.get("people", []) if item),
                    " ".join(str(item) for item in row.get("next_actions", []) if item),
                ]
            ).lower()
            if normalized_query in haystack:
                matches.append(row)
        return matches[: max(1, min(int(limit), 50))]

    def generate_daily_report(self, *, log_date: str, user_scope: str = "") -> dict[str, Any]:
        """基于当天实习记录生成日报草稿。"""

        date_value = self._normalize_date(log_date)
        rows = self.list_logs(log_date=date_value, limit=100, user_scope=user_scope)
        completed = [
            self._summary_line(row)
            for row in rows
            if str(row.get("category", "")) in {"task", "achievement"}
        ]
        learnings = [
            self._summary_line(row)
            for row in rows
            if str(row.get("category", "")) in {"learning", "feedback", "reflection"}
        ]
        blockers = [self._summary_line(row) for row in rows if str(row.get("category", "")) == "blocker"]
        next_actions: list[str] = []
        for row in rows:
            next_actions.extend(_clean_strings(row.get("next_actions") if isinstance(row.get("next_actions"), list) else []))
        projects = sorted({str(row.get("project", "")).strip() for row in rows if str(row.get("project", "")).strip()})
        return {
            "type": "internship_daily_report",
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "log_date": date_value,
            "record_count": len(rows),
            "projects": projects,
            "completed": completed,
            "learnings": learnings,
            "blockers": blockers,
            "next_actions": next_actions,
            "source_ids": [str(row.get("id", "")) for row in rows if str(row.get("id", ""))],
            "note": "这是基于已记录事实生成的实习日报草稿，不会自动夸大成果或补写未记录内容。",
        }

    def _scope_dir(self, user_scope: str) -> Path:
        scope = MemoryStore.normalize_scope(user_scope)
        name = MemoryStore._scope_dir_name(scope) if scope else "global"
        path = self.root / "users" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _logs_path(self, user_scope: str) -> Path:
        return self._scope_dir(user_scope) / "logs.jsonl"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _normalize_category(cls, value: str) -> str:
        category = value.strip().lower()
        return category if category in cls.VALID_CATEGORIES else "other"

    @staticmethod
    def _normalize_date(value: str) -> str:
        raw = value.strip()
        if raw:
            try:
                return datetime.fromisoformat(raw[:10]).date().isoformat()
            except ValueError:
                return raw[:10]
        return datetime.now(DEFAULT_LOG_TIMEZONE).date().isoformat()

    @staticmethod
    def _log_to_dict(log: InternshipLog) -> dict[str, Any]:
        return {
            "id": log.id,
            "log_date": log.log_date,
            "category": log.category,
            "title": log.title,
            "content": log.content,
            "project": log.project,
            "tags": log.tags,
            "people": log.people,
            "next_actions": log.next_actions,
            "created_at": log.created_at,
        }

    @staticmethod
    def _summary_line(row: dict[str, Any]) -> str:
        title = str(row.get("title", "")).strip()
        project = str(row.get("project", "")).strip()
        content = str(row.get("content", "")).strip()
        prefix = f"[{project}] " if project else ""
        if content and content != title:
            return f"{prefix}{title}：{content}"
        return f"{prefix}{title}".strip()

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        rows = self._read_jsonl(path)
        rows.append(row)
        self._write_jsonl(path, rows)


def register_internship_tools(registry: ToolRegistry, internship_store: InternshipStore) -> None:
    """注册实习记录助手结构化工具。"""

    def _scope(__runtime_context: dict[str, Any] | None, user_scope: str = "") -> str:
        return user_scope or str((__runtime_context or {}).get("memory_user_scope", ""))

    def internship_log_add(
        title: str,
        content: str,
        log_date: str = "",
        category: str = "task",
        project: str = "",
        tags: list[str] | None = None,
        people: list[str] | None = None,
        next_actions: list[str] | None = None,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not title.strip():
            return "Error: title is required"
        if not content.strip():
            return "Error: content is required"
        scope = _scope(__runtime_context, user_scope)
        if not scope.strip():
            return "Error: user_scope is required"
        row = internship_store.add_log(
            title,
            content,
            log_date=log_date,
            category=category,
            project=project,
            tags=tags,
            people=people,
            next_actions=next_actions,
            user_scope=scope,
        )
        return json.dumps(row, ensure_ascii=False, indent=2)

    def internship_log_list(
        log_date: str = "",
        category: str = "all",
        project: str = "",
        limit: int = 20,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _scope(__runtime_context, user_scope)
        rows = internship_store.list_logs(
            log_date=log_date,
            category=category,
            project=project,
            limit=limit,
            user_scope=scope,
        )
        return json.dumps({"items": rows, "count": len(rows)}, ensure_ascii=False, indent=2)

    def internship_log_search(
        query: str,
        limit: int = 20,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not query.strip():
            return "Error: query is required"
        scope = _scope(__runtime_context, user_scope)
        rows = internship_store.search_logs(query, limit=limit, user_scope=scope)
        return json.dumps(
            {"type": "internship_log_search", "query": query.strip(), "items": rows, "count": len(rows)},
            ensure_ascii=False,
            indent=2,
        )

    def internship_daily_report_generate(
        log_date: str,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not log_date.strip():
            return "Error: log_date is required"
        scope = _scope(__runtime_context, user_scope)
        report = internship_store.generate_daily_report(log_date=log_date, user_scope=scope)
        return json.dumps(report, ensure_ascii=False, indent=2)

    def format_internship_log_entry(log_json: str) -> str:
        if not log_json.strip():
            return "Error: log_json is required"
        data = json.loads(log_json)
        if not isinstance(data, dict) or not data.get("title"):
            return "Error: log_json must be an internship_log_add object"
        details = [
            f"日期：{data.get('log_date')}",
            f"类别：{data.get('category')}",
            f"标题：{data.get('title')}",
        ]
        if data.get("project"):
            details.append(f"项目：{data.get('project')}")
        if data.get("content"):
            details.append(f"内容：{data.get('content')}")
        if data.get("people"):
            details.append(f"相关人：{'、'.join(_clean_strings(data.get('people')))}")
        if data.get("next_actions"):
            details.append(f"下一步：{'；'.join(_clean_strings(data.get('next_actions')))}")
        return "\n".join(
            [
                "## 实习记录已保存",
                _markdown_bullets(details),
                "",
                "> 边界：这是事实记录确认，不会自动生成日报、写长期记忆或夸大成果。",
            ]
        ).strip()

    def format_internship_log_list(log_list_json: str) -> str:
        if not log_list_json.strip():
            return "Error: log_list_json is required"
        data = json.loads(log_list_json)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return "Error: log_list_json must contain an items list"
        lines = []
        for index, item in enumerate([row for row in data["items"] if isinstance(row, dict)][:20], start=1):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            parts = [str(item.get("log_date", "")).strip(), str(item.get("category", "")).strip()]
            project = str(item.get("project", "")).strip()
            if project:
                parts.append(project)
            lines.append(f"{index}. {title}（{' / '.join(part for part in parts if part)}）")
        return "\n".join(
            [
                "## 实习记录列表",
                f"- 当前显示：{len(lines)} 条",
                "",
                "\n".join(lines) if lines else "暂无符合条件的实习记录。",
                "",
                "> 边界：这是结构化记录查询，不会自动新增或修改记录。",
            ]
        ).strip()

    def format_internship_daily_report(report_json: str) -> str:
        if not report_json.strip():
            return "Error: report_json is required"
        data = json.loads(report_json)
        if not isinstance(data, dict) or data.get("type") != "internship_daily_report":
            return "Error: report_json must be an internship_daily_report object"
        return "\n".join(
            [
                f"## 实习日报草稿｜{data.get('log_date')}",
                f"- 记录数：{data.get('record_count', 0)}",
                f"- 项目：{('、'.join(_clean_strings(data.get('projects'))) or '暂无')}",
                "",
                "## 今日完成",
                _markdown_bullets(data.get("completed") if isinstance(data.get("completed"), list) else []),
                "",
                "## 学到/反馈",
                _markdown_bullets(data.get("learnings") if isinstance(data.get("learnings"), list) else []),
                "",
                "## 卡点风险",
                _markdown_bullets(data.get("blockers") if isinstance(data.get("blockers"), list) else []),
                "",
                "## 下一步",
                _markdown_bullets(data.get("next_actions") if isinstance(data.get("next_actions"), list) else []),
                "",
                "> 边界：这是基于已记录事实的日报草稿；未记录的成果、指标或结论需要你确认后再补充。",
            ]
        ).strip()

    log_properties = {
        "user_scope": {"type": "string", "description": "可选；默认使用运行时用户 scope"},
        "title": {"type": "string", "description": "记录标题"},
        "content": {"type": "string", "description": "事实内容"},
        "log_date": {"type": "string", "description": "日期，YYYY-MM-DD；为空则使用今天"},
        "category": {
            "type": "string",
            "enum": sorted(InternshipStore.VALID_CATEGORIES),
            "description": "记录类别",
        },
        "project": {"type": "string", "description": "项目或模块名称"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "people": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    }
    registry.register(
        RegisteredTool(
            name="internship_log_add",
            description="保存一条张海波实习过程事实记录。",
            input_schema={"type": "object", "properties": log_properties, "required": ["title", "content"]},
            handler=internship_log_add,
            tags=("internship", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="internship_log_list",
            description="按日期、类别或项目列出实习记录。",
            input_schema={
                "type": "object",
                "properties": {
                    "user_scope": {"type": "string"},
                    "log_date": {"type": "string"},
                    "category": {"type": "string"},
                    "project": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            handler=internship_log_list,
            tags=("internship", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="internship_log_search",
            description="搜索实习记录标题、内容、项目、标签、相关人和下一步。",
            input_schema={
                "type": "object",
                "properties": {
                    "user_scope": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
            handler=internship_log_search,
            tags=("internship", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="internship_daily_report_generate",
            description="基于指定日期的实习记录生成日报草稿。",
            input_schema={
                "type": "object",
                "properties": {"user_scope": {"type": "string"}, "log_date": {"type": "string"}},
                "required": ["log_date"],
            },
            handler=internship_daily_report_generate,
            tags=("internship", "report"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_internship_log_entry",
            description="把 internship_log_add 的 JSON 结果格式化为企业微信可读确认。",
            input_schema={"type": "object", "properties": {"log_json": {"type": "string"}}, "required": ["log_json"]},
            handler=format_internship_log_entry,
            tags=("internship", "format"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_internship_log_list",
            description="把 internship_log_list 或 internship_log_search 的 JSON 结果格式化为列表。",
            input_schema={
                "type": "object",
                "properties": {"log_list_json": {"type": "string"}},
                "required": ["log_list_json"],
            },
            handler=format_internship_log_list,
            tags=("internship", "format"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_internship_daily_report",
            description="把 internship_daily_report_generate 的 JSON 结果格式化为日报草稿。",
            input_schema={
                "type": "object",
                "properties": {"report_json": {"type": "string"}},
                "required": ["report_json"],
            },
            handler=format_internship_daily_report,
            tags=("internship", "format"),
        )
    )
