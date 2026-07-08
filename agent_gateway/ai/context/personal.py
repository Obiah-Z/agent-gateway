from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


def _clean_strings(items: list[object] | None) -> list[str]:
    """清洗工具输出中用于 Markdown 展示的字符串列表。"""

    return [str(item).strip() for item in items or [] if str(item).strip()]


def _markdown_bullets(items: list[object] | None) -> str:
    """把列表渲染成 Markdown bullet list。"""

    cleaned = _clean_strings(items)
    if not cleaned:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in cleaned)


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

    def search_todos(
        self,
        query: str,
        *,
        status: str = "open",
        limit: int = 20,
        user_scope: str = "",
    ) -> list[dict[str, Any]]:
        """按标题、备注或结果关键词搜索个人待办。"""

        normalized_query = " ".join(query.strip().split()).lower()
        if not normalized_query:
            return []
        rows = self.list_todos(status=status, limit=100, user_scope=user_scope)
        matches = []
        for row in rows:
            haystack = " ".join(
                str(row.get(field, ""))
                for field in ("title", "notes", "result", "cancel_reason")
            ).lower()
            if normalized_query in haystack:
                matches.append(row)
        matches.sort(key=self._todo_sort_key)
        return matches[: max(1, min(int(limit), 50))]

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

    def cancel_todo(
        self,
        todo_id: str,
        *,
        reason: str = "",
        user_scope: str = "",
    ) -> dict[str, Any] | None:
        """取消指定待办，同时保留原始记录用于审计。"""

        rows = self._read_jsonl(self._todos_path(user_scope))
        now = self._now()
        updated = None
        for row in rows:
            if str(row.get("id", "")) == todo_id:
                row["status"] = "canceled"
                row["canceled_at"] = now
                if reason.strip():
                    row["cancel_reason"] = reason.strip()
                updated = row
                break
        if updated is None:
            return None
        self._write_jsonl(self._todos_path(user_scope), rows)
        return updated

    def reopen_todo(
        self,
        todo_id: str,
        *,
        reason: str = "",
        user_scope: str = "",
    ) -> dict[str, Any] | None:
        """把已完成或已取消的待办恢复为 open。"""

        rows = self._read_jsonl(self._todos_path(user_scope))
        now = self._now()
        updated = None
        for row in rows:
            if str(row.get("id", "")) == todo_id:
                row["status"] = "open"
                row["reopened_at"] = now
                row.pop("completed_at", None)
                row.pop("canceled_at", None)
                if reason.strip():
                    row["reopen_reason"] = reason.strip()
                updated = row
                break
        if updated is None:
            return None
        self._write_jsonl(self._todos_path(user_scope), rows)
        return updated

    def update_todo(
        self,
        todo_id: str,
        *,
        title: str | None = None,
        priority: str | None = None,
        due_at: str | None = None,
        notes: str | None = None,
        user_scope: str = "",
    ) -> dict[str, Any] | None:
        """更新指定待办的标题、优先级、时间或备注。"""

        rows = self._read_jsonl(self._todos_path(user_scope))
        updated = None
        now = self._now()
        for row in rows:
            if str(row.get("id", "")) == todo_id:
                if title is not None and title.strip():
                    row["title"] = title.strip()
                if priority is not None and priority.strip():
                    row["priority"] = self._normalize_priority(priority)
                if due_at is not None:
                    row["due_at"] = due_at.strip()
                if notes is not None:
                    row["notes"] = notes.strip()
                row["updated_at"] = now
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

    def generate_briefing(
        self,
        *,
        user_scope: str = "",
        todo_limit: int = 8,
        review_limit: int = 3,
    ) -> dict[str, Any]:
        """汇总个人秘书简报所需的待办和近期复盘。"""

        open_todos = self.list_todos(
            status="open",
            limit=todo_limit,
            user_scope=user_scope,
        )
        recent_reviews = self.recent_reviews(
            limit=review_limit,
            user_scope=user_scope,
        )
        urgent_todos = [
            row
            for row in open_todos
            if str(row.get("priority", "")).lower() in {"high", "urgent"}
        ]
        next_steps = [
            str(row.get("next_step", "")).strip()
            for row in recent_reviews
            if str(row.get("next_step", "")).strip()
        ]
        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "open_todos": open_todos,
            "urgent_todos": urgent_todos,
            "recent_reviews": recent_reviews,
            "suggested_focus": self._suggest_focus(open_todos, next_steps),
            "next_steps": next_steps[:5],
        }

    def generate_time_blocks(
        self,
        *,
        user_scope: str = "",
        todo_limit: int = 9,
    ) -> dict[str, Any]:
        """把未完成待办安排到上午、下午、晚上三个时间块。"""

        open_todos = self.list_todos(status="open", limit=todo_limit, user_scope=user_scope)
        sorted_todos = sorted(open_todos, key=self._todo_sort_key)
        blocks = [
            {"name": "上午", "focus": "", "items": []},
            {"name": "下午", "focus": "", "items": []},
            {"name": "晚上", "focus": "", "items": []},
        ]
        for index, todo in enumerate(sorted_todos[:todo_limit]):
            blocks[index % len(blocks)]["items"].append(todo)
        for block in blocks:
            if block["items"]:
                block["focus"] = str(block["items"][0].get("title", "")).strip()
            else:
                block["focus"] = "留作缓冲或处理临时事项"
        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "source_todo_count": len(open_todos),
            "blocks": blocks,
            "first_action": self._first_time_block_action(blocks),
            "note": "这是基于未完成待办生成的建议时间块，不会自动修改待办状态。",
        }

    def generate_daily_workflow(
        self,
        *,
        user_scope: str = "",
        todo_limit: int = 9,
        review_limit: int = 3,
    ) -> dict[str, Any]:
        """组合待办、复盘和时间块，生成个人每日工作流。"""

        briefing = self.generate_briefing(
            user_scope=user_scope,
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        time_blocks = self.generate_time_blocks(
            user_scope=user_scope,
            todo_limit=todo_limit,
        )
        recent_reviews = briefing.get("recent_reviews", [])
        review_reminders = []
        for review in recent_reviews:
            next_step = str(review.get("next_step", "")).strip()
            if next_step:
                review_reminders.append(next_step)
        if not review_reminders and recent_reviews:
            review_reminders.append(str(recent_reviews[0].get("summary", "")).strip())

        open_todos = briefing.get("open_todos", [])
        urgent_todos = briefing.get("urgent_todos", [])
        needs_confirmation = []
        if not open_todos:
            needs_confirmation.append("今天最重要的一件事是什么？")
        if not review_reminders:
            needs_confirmation.append("是否需要补一次昨日/今日复盘？")

        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "current_focus": briefing.get("suggested_focus", ""),
            "today_priorities": [
                {
                    "title": str(todo.get("title", "")),
                    "priority": str(todo.get("priority", "")),
                    "due_at": str(todo.get("due_at", "")),
                }
                for todo in (urgent_todos or open_todos)[:5]
            ],
            "time_blocks": time_blocks.get("blocks", []),
            "first_action": time_blocks.get("first_action", ""),
            "review_reminders": review_reminders[:5],
            "needs_confirmation": needs_confirmation,
            "source": {
                "open_todo_count": len(open_todos),
                "urgent_todo_count": len(urgent_todos),
                "recent_review_count": len(recent_reviews),
            },
            "note": "这是基于个人待办和近期复盘生成的每日工作流，不会自动完成或修改待办。",
        }

    def generate_focus_card(
        self,
        *,
        user_scope: str = "",
        todo_limit: int = 8,
        review_limit: int = 3,
    ) -> dict[str, Any]:
        """生成一个当前最该推进事项的聚焦卡片，不写入数据。"""

        briefing = self.generate_briefing(
            user_scope=user_scope,
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        open_todos = sorted(briefing.get("open_todos", []), key=self._todo_sort_key)
        urgent_todos = sorted(briefing.get("urgent_todos", []), key=self._todo_sort_key)
        recent_reviews = briefing.get("recent_reviews", [])
        focus_todo = (urgent_todos or open_todos or [None])[0]
        blockers = []
        review_next_steps = []
        for review in recent_reviews:
            for blocker in review.get("blockers") or []:
                text = str(blocker).strip()
                if text:
                    blockers.append(text)
            next_step = str(review.get("next_step", "")).strip()
            if next_step:
                review_next_steps.append(next_step)

        if isinstance(focus_todo, dict):
            focus_title = str(focus_todo.get("title", "")).strip()
            first_action = f"先用 25 分钟推进「{focus_title}」。"
            why_now = self._focus_reason(focus_todo, has_blockers=bool(blockers))
            defer_candidates = [
                str(todo.get("title", "")).strip()
                for todo in open_todos
                if str(todo.get("id", "")) != str(focus_todo.get("id", ""))
            ][:3]
        else:
            focus_title = briefing.get("suggested_focus", "")
            first_action = "先确认今天最重要的一件事，并写成一个待办。"
            why_now = "当前没有未完成待办，先明确焦点比继续发散更重要。"
            defer_candidates = []

        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "type": "personal_focus_card",
            "focus": focus_title,
            "focus_todo": focus_todo or {},
            "why_now": why_now,
            "first_action": first_action,
            "blockers": blockers[:3],
            "review_next_steps": review_next_steps[:3],
            "defer": defer_candidates,
            "needs_confirmation": []
            if focus_todo
            else ["今天最重要的一件事是什么？"],
            "source": {
                "open_todo_count": len(open_todos),
                "urgent_todo_count": len(urgent_todos),
                "recent_review_count": len(recent_reviews),
            },
            "note": "这是个人秘书当前聚焦卡片，不会自动完成、修改或新增待办。",
        }

    def generate_day_review_plan(
        self,
        *,
        user_scope: str = "",
        today_summary: str = "",
        completed: list[str] | None = None,
        blockers: list[str] | None = None,
        tomorrow_focus: str = "",
        todo_limit: int = 6,
        review_limit: int = 3,
    ) -> dict[str, Any]:
        """生成个人日复盘和明日计划草稿，不写入复盘或待办。"""

        briefing = self.generate_briefing(
            user_scope=user_scope,
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        time_blocks = self.generate_time_blocks(
            user_scope=user_scope,
            todo_limit=todo_limit,
        )
        open_todos = briefing.get("open_todos", [])
        recent_reviews = briefing.get("recent_reviews", [])
        completed_items = self._clean_list(completed or [])
        blocker_items = self._clean_list(blockers or [])
        review_summary = today_summary.strip()
        if not review_summary and recent_reviews:
            review_summary = str(recent_reviews[0].get("summary", "")).strip()
        tomorrow_first_step = tomorrow_focus.strip() or self._first_time_block_action(
            time_blocks.get("blocks", [])
        )

        confirmations = []
        if not review_summary:
            confirmations.append("今天最重要的完成事项是什么？")
        if not tomorrow_focus and not open_todos:
            confirmations.append("明天第一步要做什么？")
        if blocker_items:
            confirmations.append("这些卡点是否需要拆成待办或求助事项？")

        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "type": "personal_day_review_plan",
            "review_draft": {
                "summary": review_summary or "待补充今日复盘摘要",
                "completed": completed_items,
                "blockers": blocker_items,
                "next_step": tomorrow_first_step,
            },
            "tomorrow_plan": {
                "focus": tomorrow_focus.strip() or briefing.get("suggested_focus", ""),
                "first_step": tomorrow_first_step,
                "priority_todos": [
                    {
                        "title": str(todo.get("title", "")),
                        "priority": str(todo.get("priority", "")),
                        "due_at": str(todo.get("due_at", "")),
                    }
                    for todo in open_todos[:5]
                ],
                "time_blocks": time_blocks.get("blocks", []),
            },
            "needs_confirmation": confirmations,
            "next_actions": [
                "确认后可调用 personal_review_add 写入复盘。",
                "如明日第一步不是已有待办，确认后可调用 personal_todo_add 写入待办。",
            ],
            "source": {
                "open_todo_count": len(open_todos),
                "recent_review_count": len(recent_reviews),
            },
            "note": "这是个人日复盘和明日计划草稿，不会自动写入复盘、待办或长期记忆。",
        }

    def generate_weekly_plan(
        self,
        *,
        user_scope: str = "",
        week_goal: str = "",
        focus_areas: list[str] | None = None,
        constraints: list[str] | None = None,
        todo_limit: int = 12,
        review_limit: int = 5,
    ) -> dict[str, Any]:
        """生成个人周计划草稿，不写入待办或复盘。"""

        briefing = self.generate_briefing(
            user_scope=user_scope,
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        open_todos = sorted(briefing.get("open_todos", []), key=self._todo_sort_key)
        urgent_todos = sorted(briefing.get("urgent_todos", []), key=self._todo_sort_key)
        recent_reviews = briefing.get("recent_reviews", [])
        focus_items = self._clean_list(focus_areas or [])
        constraint_items = self._clean_list(constraints or [])
        if not focus_items:
            focus_items = [
                str(todo.get("title", "")).strip()
                for todo in (urgent_todos or open_todos)[:3]
                if str(todo.get("title", "")).strip()
            ]
        if not focus_items and week_goal.strip():
            focus_items = [week_goal.strip()]

        weekly_priorities = []
        for todo in (urgent_todos or open_todos)[:5]:
            weekly_priorities.append(
                {
                    "title": str(todo.get("title", "")),
                    "priority": str(todo.get("priority", "")),
                    "due_at": str(todo.get("due_at", "")),
                }
            )
        review_signals = []
        for review in recent_reviews:
            next_step = str(review.get("next_step", "")).strip()
            summary = str(review.get("summary", "")).strip()
            if next_step:
                review_signals.append(next_step)
            elif summary:
                review_signals.append(summary)

        milestones = []
        for index, item in enumerate(focus_items[:3], start=1):
            milestones.append(
                {
                    "name": f"里程碑 {index}",
                    "focus": item,
                    "done": f"围绕「{item}」完成至少一个可验证产出。",
                }
            )
        if not milestones:
            milestones.append(
                {
                    "name": "里程碑 1",
                    "focus": "确认本周最重要目标",
                    "done": "本周目标、优先级和第一步均已明确。",
                }
            )

        needs_confirmation = []
        if not week_goal.strip():
            needs_confirmation.append("本周最重要目标是什么？")
        if not open_todos:
            needs_confirmation.append("是否需要先补充本周待办？")
        if constraint_items:
            needs_confirmation.append("这些限制是否需要拆成避坑动作或求助事项？")

        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "type": "personal_weekly_plan",
            "week_goal": week_goal.strip() or briefing.get("suggested_focus", ""),
            "focus_areas": focus_items[:5],
            "weekly_priorities": weekly_priorities,
            "milestones": milestones,
            "review_signals": review_signals[:5],
            "constraints": constraint_items,
            "first_action": (
                f"先推进「{weekly_priorities[0]['title']}」。"
                if weekly_priorities
                else "先确认本周最重要目标，再拆第一步。"
            ),
            "needs_confirmation": needs_confirmation,
            "next_actions": [
                "确认后可把关键里程碑拆成 personal_todo_add 待办。",
                "周末复盘时可用 personal_review_add 写入本周总结。",
            ],
            "source": {
                "open_todo_count": len(open_todos),
                "urgent_todo_count": len(urgent_todos),
                "recent_review_count": len(recent_reviews),
            },
            "note": "这是个人周计划草稿，不会自动写入待办、复盘或长期记忆。",
        }

    def triage_inbox(
        self,
        text: str,
        *,
        user_scope: str = "",
        context: str = "",
    ) -> dict[str, Any]:
        """把用户碎片输入整理成可执行收件箱建议，不直接写入数据。"""

        normalized = " ".join(str(text or "").strip().split())
        context_text = " ".join(str(context or "").strip().split())
        signals = self._inbox_signals(normalized)
        suggested_todos = self._suggest_todos_from_text(normalized)
        suggested_review = self._suggest_review_from_text(normalized, signals)
        suggested_memory = self._suggest_memory_from_text(normalized, signals)
        confirmations = self._inbox_confirmations(
            normalized,
            suggested_todos=suggested_todos,
            suggested_review=suggested_review,
            suggested_memory=suggested_memory,
        )
        return {
            "generated_at": self._now(),
            "user_scope": MemoryStore.normalize_scope(user_scope),
            "type": "personal_inbox_triage",
            "source_text": normalized,
            "context": context_text,
            "intent": self._inbox_intent(signals, suggested_todos, suggested_review),
            "suggested_todos": suggested_todos,
            "suggested_review": suggested_review,
            "suggested_memory": suggested_memory,
            "needs_confirmation": confirmations,
            "next_actions": self._inbox_next_actions(
                suggested_todos=suggested_todos,
                suggested_review=suggested_review,
                suggested_memory=suggested_memory,
                confirmations=confirmations,
            ),
            "note": "这是个人秘书收件箱整理建议，不会自动写入待办、复盘或长期记忆。",
        }

    def commit_inbox_triage(
        self,
        triage: dict[str, Any],
        *,
        user_scope: str = "",
        commit_todos: bool = True,
        commit_review: bool = True,
    ) -> dict[str, Any]:
        """把用户已确认的收件箱整理结果批量写入待办和复盘。"""

        if triage.get("type") != "personal_inbox_triage":
            raise ValueError("triage type must be personal_inbox_triage")
        scope = MemoryStore.normalize_scope(user_scope or str(triage.get("user_scope", "")))
        suggested_todos = triage.get("suggested_todos") if isinstance(triage.get("suggested_todos"), list) else []
        suggested_review = (
            triage.get("suggested_review") if isinstance(triage.get("suggested_review"), dict) else None
        )
        suggested_memory = (
            triage.get("suggested_memory") if isinstance(triage.get("suggested_memory"), dict) else None
        )

        written_todos = []
        if commit_todos:
            for item in suggested_todos:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                written_todos.append(
                    self.add_todo(
                        title,
                        priority=str(item.get("priority", "normal")),
                        due_at=str(item.get("due_at", "")),
                        notes=str(item.get("notes", "")),
                        user_scope=scope,
                    )
                )

        written_review = None
        if commit_review and suggested_review is not None:
            summary = str(suggested_review.get("summary", "")).strip()
            if summary:
                written_review = self.add_review(
                    summary,
                    completed=[
                        str(item).strip()
                        for item in suggested_review.get("completed", [])
                        if str(item).strip()
                    ],
                    blockers=[
                        str(item).strip()
                        for item in suggested_review.get("blockers", [])
                        if str(item).strip()
                    ],
                    next_step=str(suggested_review.get("next_step", "")),
                    user_scope=scope,
                )

        skipped = []
        if suggested_memory is not None:
            skipped.append(
                {
                    "type": "memory",
                    "reason": "长期记忆候选需要用户单独确认后再调用 memory_write。",
                    "candidate": suggested_memory,
                }
            )
        return {
            "generated_at": self._now(),
            "user_scope": scope,
            "type": "personal_inbox_commit",
            "written_todos": written_todos,
            "written_review": written_review,
            "skipped": skipped,
            "source": {
                "suggested_todo_count": len(suggested_todos),
                "committed_todo_count": len(written_todos),
                "has_review": suggested_review is not None,
                "committed_review": written_review is not None,
                "has_memory_candidate": suggested_memory is not None,
            },
            "note": "这是基于已确认收件箱整理结果的批量写入；长期记忆不会自动写入。",
        }

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
    def _suggest_focus(open_todos: list[dict[str, Any]], next_steps: list[str]) -> str:
        urgent = [
            row
            for row in open_todos
            if str(row.get("priority", "")).lower() in {"high", "urgent"}
        ]
        if urgent:
            return str(urgent[0].get("title", "")).strip()
        if next_steps:
            return next_steps[0]
        if open_todos:
            return str(open_todos[0].get("title", "")).strip()
        return "暂无明确待办，建议先确认今天最重要的一件事。"

    @staticmethod
    def _todo_sort_key(todo: dict[str, Any]) -> tuple[int, str, str]:
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        priority = priority_order.get(str(todo.get("priority", "")).lower(), 2)
        due_at = str(todo.get("due_at", "") or "9999-99-99")
        created_at = str(todo.get("created_at", ""))
        return (priority, due_at, created_at)

    @staticmethod
    def _focus_reason(todo: dict[str, Any], *, has_blockers: bool) -> str:
        priority = str(todo.get("priority", "")).lower()
        due_at = str(todo.get("due_at", "")).strip()
        if priority == "urgent":
            return "它是当前最高优先级事项，适合先处理，避免继续积压。"
        if priority == "high":
            return "它是高优先级事项，先推进可以减少后续压力。"
        if due_at:
            return f"它有时间约束（{due_at}），适合先推进。"
        if has_blockers:
            return "近期复盘里有卡点，先处理一个明确待办有助于恢复推进。"
        return "它是当前待办队列里最靠前的事项，适合作为下一步。"

    @staticmethod
    def _first_time_block_action(blocks: list[dict[str, Any]]) -> str:
        for block in blocks:
            items = block.get("items")
            if isinstance(items, list) and items:
                return f"先处理「{items[0].get('title', '')}」。"
        return "先确认今天最重要的一件事，再开始执行。"

    @staticmethod
    def _inbox_signals(text: str) -> set[str]:
        signals: set[str] = set()
        lowered = text.lower()
        if any(word in text for word in ["要做", "待办", "提醒", "记一下", "明天要", "今天要", "本周要", "下周要"]):
            signals.add("todo")
        if any(word in text for word in ["完成", "做完", "复盘", "卡点", "明天第一步", "今天复盘"]):
            signals.add("review")
        if any(word in text for word in ["长期", "偏好", "固定", "以后", "目标", "习惯"]):
            signals.add("memory")
        if any(word in lowered for word in ["urgent", "asap"]) or any(word in text for word in ["紧急", "马上", "今天必须"]):
            signals.add("urgent")
        if any(word in text for word in ["不确定", "可能", "待确认", "看情况"]):
            signals.add("uncertain")
        return signals

    @classmethod
    def _suggest_todos_from_text(cls, text: str) -> list[dict[str, Any]]:
        if not text:
            return []
        todos: list[dict[str, Any]] = []
        for fragment in cls._split_inbox_fragments(text):
            if not cls._looks_like_todo(fragment):
                continue
            todos.append(
                {
                    "title": cls._clean_todo_title(fragment),
                    "priority": cls._infer_priority(fragment),
                    "due_at": cls._infer_due_at(fragment),
                    "notes": "",
                }
            )
        return todos[:5]

    @staticmethod
    def _suggest_review_from_text(text: str, signals: set[str]) -> dict[str, Any] | None:
        if "review" not in signals:
            return None
        completed = []
        blockers = []
        next_step = ""
        for fragment in PersonalStore._split_inbox_fragments(text):
            if any(word in fragment for word in ["完成", "做完"]):
                completed.append(fragment)
            elif any(word in fragment for word in ["卡点", "卡住", "焦虑", "不清楚"]):
                blockers.append(fragment)
            elif "明天" in fragment or "下一步" in fragment:
                next_step = fragment
        return {
            "summary": text[:160],
            "completed": completed[:5],
            "blockers": blockers[:5],
            "next_step": next_step,
        }

    @staticmethod
    def _suggest_memory_from_text(text: str, signals: set[str]) -> dict[str, Any] | None:
        if "memory" not in signals:
            return None
        return {
            "content": text[:300],
            "category": "personal_preference",
            "reason": "包含长期目标、固定偏好或稳定习惯信号。",
        }

    @staticmethod
    def _inbox_confirmations(
        text: str,
        *,
        suggested_todos: list[dict[str, Any]],
        suggested_review: dict[str, Any] | None,
        suggested_memory: dict[str, Any] | None,
    ) -> list[str]:
        confirmations: list[str] = []
        if not text:
            confirmations.append("需要补充要整理的原始内容。")
        if not suggested_todos and suggested_review is None and suggested_memory is None:
            confirmations.append("这段内容更像普通对话，是否需要整理成待办或复盘？")
        if len(suggested_todos) > 3:
            confirmations.append("待办较多，是否需要只保留今天最重要的 3 件？")
        if suggested_memory is not None:
            confirmations.append("是否确认写入长期记忆？")
        return confirmations

    @staticmethod
    def _inbox_next_actions(
        *,
        suggested_todos: list[dict[str, Any]],
        suggested_review: dict[str, Any] | None,
        suggested_memory: dict[str, Any] | None,
        confirmations: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if suggested_todos:
            actions.append("确认后调用 personal_todo_add 写入待办。")
        if suggested_review is not None:
            actions.append("确认后调用 personal_review_add 写入复盘。")
        if suggested_memory is not None:
            actions.append("确认后调用 memory_write 写入长期记忆。")
        if confirmations:
            actions.append("先向用户确认含糊项，再写入结构化数据。")
        if not actions:
            actions.append("直接用简短中文回复，不需要写入结构化数据。")
        return actions

    @staticmethod
    def _inbox_intent(
        signals: set[str],
        suggested_todos: list[dict[str, Any]],
        suggested_review: dict[str, Any] | None,
    ) -> str:
        if suggested_todos and suggested_review is not None:
            return "mixed"
        if suggested_todos or "todo" in signals:
            return "todo"
        if suggested_review is not None or "review" in signals:
            return "review"
        if "memory" in signals:
            return "memory"
        return "chat"

    @staticmethod
    def _split_inbox_fragments(text: str) -> list[str]:
        raw_parts = []
        for line in text.replace("；", "\n").replace("。", "\n").replace("，", "\n").splitlines():
            raw_parts.extend(line.split(";"))
        return [part.strip(" -\t") for part in raw_parts if part.strip(" -\t")]

    @staticmethod
    def _looks_like_todo(fragment: str) -> bool:
        if any(word in fragment for word in ["完成", "做完"]) and not any(
            word in fragment for word in ["要完成", "需要完成", "必须完成"]
        ):
            return False
        return any(
            word in fragment
            for word in ["要", "待办", "提醒", "准备", "整理", "复习", "背", "练", "写", "提交"]
        )

    @staticmethod
    def _clean_todo_title(fragment: str) -> str:
        title = fragment.strip()
        for prefix in ["记一下", "待办", "提醒我", "我要", "需要", "今天", "明天"]:
            if title.startswith(prefix):
                title = title[len(prefix) :].strip(" ：:，,")
        return title[:120] or fragment[:120]

    @staticmethod
    def _infer_priority(fragment: str) -> str:
        if any(word in fragment.lower() for word in ["urgent", "asap"]) or any(
            word in fragment for word in ["紧急", "马上", "今天必须", "必须"]
        ):
            return "urgent"
        if any(word in fragment for word in ["重要", "优先", "明天"]):
            return "high"
        return "normal"

    @staticmethod
    def _infer_due_at(fragment: str) -> str:
        if "今天" in fragment:
            return "today"
        if "明天" in fragment:
            return "tomorrow"
        if "本周" in fragment:
            return "this_week"
        if "下周" in fragment:
            return "next_week"
        return ""

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

    def format_personal_todo_entry(todo_json: str) -> str:
        if not todo_json.strip():
            return "Error: todo_json is required"
        data = json.loads(todo_json)
        if not isinstance(data, dict):
            return "Error: todo_json must be a JSON object"
        if not data.get("title"):
            return "Error: todo_json must be a personal_todo_add object"

        details = [
            f"事项：{data.get('title')}",
            f"状态：{data.get('status') or 'open'}",
            f"优先级：{data.get('priority') or 'normal'}",
        ]
        if data.get("due_at"):
            details.append(f"时间：{data.get('due_at')}")
        notes = str(data.get("notes") or "").strip()
        if notes:
            details.append(f"备注：{notes}")

        sections = [
            "## 待办已记录",
            _markdown_bullets(details),
            "",
            "## 下一步",
            "- 后续查询待办时可使用待办列表。",
            "- 完成后可以告诉我“这项完成了”，我会把它标记为完成。",
            "",
            "> 边界：这是待办记录确认，只格式化已保存结果，不会自动完成待办、写复盘或写入长期记忆。",
        ]
        return "\n".join(sections).strip()

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

    def personal_todo_search(
        query: str,
        status: str = "open",
        limit: int = 20,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not query.strip():
            return "Error: query is required"
        scope = _scope(__runtime_context, user_scope)
        rows = personal_store.search_todos(
            query,
            status=status,
            limit=limit,
            user_scope=scope,
        )
        result = {
            "type": "personal_todo_search",
            "query": query.strip(),
            "status": status.strip().lower() or "open",
            "items": rows,
            "count": len(rows),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def format_personal_todo_list(todo_list_json: str) -> str:
        if not todo_list_json.strip():
            return "Error: todo_list_json is required"
        data = json.loads(todo_list_json)
        if not isinstance(data, dict):
            return "Error: todo_list_json must be a JSON object"
        items = data.get("items")
        if not isinstance(items, list):
            return "Error: todo_list_json must contain an items list"

        sorted_items = sorted(
            [item for item in items if isinstance(item, dict)],
            key=PersonalStore._todo_sort_key,
        )
        todo_lines = []
        for index, item in enumerate(sorted_items[:12], start=1):
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            details = []
            if item.get("status"):
                details.append(f"状态：{item.get('status')}")
            if item.get("priority"):
                details.append(f"优先级：{item.get('priority')}")
            if item.get("due_at"):
                details.append(f"时间：{item.get('due_at')}")
            notes = str(item.get("notes") or "").strip()
            if notes:
                details.append(f"备注：{notes}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            todo_lines.append(f"{index}. {title}{suffix}")

        sections = [
            "## 待办列表",
            f"- 当前显示：{len(sorted_items)} 项",
            "",
            "## 明细",
            "\n".join(todo_lines) if todo_lines else "暂无符合条件的待办。",
            "",
            "> 边界：这是待办查询结果，只读取结构化待办，不会自动新增、完成或修改待办。",
        ]
        return "\n".join(sections).strip()

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

    def personal_todo_complete_by_title(
        title_query: str,
        result: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        query = " ".join(title_query.strip().split()).lower()
        if not query:
            return "Error: title_query is required"
        scope = _scope(__runtime_context, user_scope)
        open_todos = personal_store.list_todos(status="open", limit=100, user_scope=scope)
        exact_matches = [
            todo
            for todo in open_todos
            if str(todo.get("title", "")).strip().lower() == query
        ]
        partial_matches = [
            todo
            for todo in open_todos
            if query in str(todo.get("title", "")).strip().lower()
        ]
        matches = exact_matches or partial_matches
        if not matches:
            return f"Error: no open todo matched title: {title_query}"
        if len(matches) > 1:
            titles = "；".join(str(todo.get("title", "")) for todo in matches[:5])
            return f"Error: multiple open todos matched title: {titles}"
        row = personal_store.complete_todo(
            str(matches[0].get("id", "")),
            result=result,
            user_scope=scope,
        )
        if row is None:
            return f"Error: todo not found after title match: {title_query}"
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_todo_update_by_title(
        title_query: str,
        new_title: str | None = None,
        priority: str | None = None,
        due_at: str | None = None,
        notes: str | None = None,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        query = " ".join(title_query.strip().split()).lower()
        if not query:
            return "Error: title_query is required"
        update_values = [new_title, priority, due_at, notes]
        if not any(value is not None and str(value).strip() for value in update_values):
            return "Error: at least one update field is required"
        scope = _scope(__runtime_context, user_scope)
        open_todos = personal_store.list_todos(status="open", limit=100, user_scope=scope)
        exact_matches = [
            todo
            for todo in open_todos
            if str(todo.get("title", "")).strip().lower() == query
        ]
        partial_matches = [
            todo
            for todo in open_todos
            if query in str(todo.get("title", "")).strip().lower()
        ]
        matches = exact_matches or partial_matches
        if not matches:
            return f"Error: no open todo matched title: {title_query}"
        if len(matches) > 1:
            titles = "；".join(str(todo.get("title", "")) for todo in matches[:5])
            return f"Error: multiple open todos matched title: {titles}"
        row = personal_store.update_todo(
            str(matches[0].get("id", "")),
            title=new_title,
            priority=priority,
            due_at=due_at,
            notes=notes,
            user_scope=scope,
        )
        if row is None:
            return f"Error: todo not found after title match: {title_query}"
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_todo_cancel_by_title(
        title_query: str,
        reason: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        query = " ".join(title_query.strip().split()).lower()
        if not query:
            return "Error: title_query is required"
        scope = _scope(__runtime_context, user_scope)
        open_todos = personal_store.list_todos(status="open", limit=100, user_scope=scope)
        exact_matches = [
            todo
            for todo in open_todos
            if str(todo.get("title", "")).strip().lower() == query
        ]
        partial_matches = [
            todo
            for todo in open_todos
            if query in str(todo.get("title", "")).strip().lower()
        ]
        matches = exact_matches or partial_matches
        if not matches:
            return f"Error: no open todo matched title: {title_query}"
        if len(matches) > 1:
            titles = "；".join(str(todo.get("title", "")) for todo in matches[:5])
            return f"Error: multiple open todos matched title: {titles}"
        row = personal_store.cancel_todo(
            str(matches[0].get("id", "")),
            reason=reason,
            user_scope=scope,
        )
        if row is None:
            return f"Error: todo not found after title match: {title_query}"
        return json.dumps(row, ensure_ascii=False, indent=2)

    def personal_todo_reopen_by_title(
        title_query: str,
        reason: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        query = " ".join(title_query.strip().split()).lower()
        if not query:
            return "Error: title_query is required"
        scope = _scope(__runtime_context, user_scope)
        inactive_todos = [
            todo
            for todo in personal_store.list_todos(status="all", limit=100, user_scope=scope)
            if str(todo.get("status", "")).lower() in {"done", "canceled"}
        ]
        exact_matches = [
            todo
            for todo in inactive_todos
            if str(todo.get("title", "")).strip().lower() == query
        ]
        partial_matches = [
            todo
            for todo in inactive_todos
            if query in str(todo.get("title", "")).strip().lower()
        ]
        matches = exact_matches or partial_matches
        if not matches:
            return f"Error: no done or canceled todo matched title: {title_query}"
        if len(matches) > 1:
            titles = "；".join(str(todo.get("title", "")) for todo in matches[:5])
            return f"Error: multiple done or canceled todos matched title: {titles}"
        row = personal_store.reopen_todo(
            str(matches[0].get("id", "")),
            reason=reason,
            user_scope=scope,
        )
        if row is None:
            return f"Error: todo not found after title match: {title_query}"
        return json.dumps(row, ensure_ascii=False, indent=2)

    def format_personal_todo_update(todo_json: str) -> str:
        if not todo_json.strip():
            return "Error: todo_json is required"
        data = json.loads(todo_json)
        if not isinstance(data, dict):
            return "Error: todo_json must be a JSON object"
        if not data.get("title"):
            return "Error: todo_json must be an updated personal todo object"

        details = [
            f"事项：{data.get('title')}",
            f"状态：{data.get('status') or 'open'}",
            f"优先级：{data.get('priority') or 'normal'}",
        ]
        if data.get("due_at"):
            details.append(f"时间：{data.get('due_at')}")
        notes = str(data.get("notes") or "").strip()
        if notes:
            details.append(f"备注：{notes}")
        if data.get("updated_at"):
            details.append(f"更新时间：{data.get('updated_at')}")

        sections = [
            "## 待办已更新",
            _markdown_bullets(details),
            "",
            "## 下一步",
            "- 后续查询待办时会显示更新后的内容。",
            "- 如果这项已完成，可以继续告诉我完成结果。",
            "",
            "> 边界：这是待办更新确认，只修改已匹配的结构化待办，不会新增待办、完成待办或写入长期记忆。",
        ]
        return "\n".join(sections).strip()

    def format_personal_todo_cancellation(cancellation_json: str) -> str:
        if not cancellation_json.strip():
            return "Error: cancellation_json is required"
        data = json.loads(cancellation_json)
        if not isinstance(data, dict):
            return "Error: cancellation_json must be a JSON object"
        if data.get("status") != "canceled":
            return "Error: cancellation_json must be a canceled personal todo object"

        title = str(data.get("title") or "未命名待办").strip()
        details = []
        if data.get("priority"):
            details.append(f"优先级：{data.get('priority')}")
        if data.get("due_at"):
            details.append(f"原计划时间：{data.get('due_at')}")
        reason = str(data.get("cancel_reason") or "").strip()
        if reason:
            details.append(f"取消原因：{reason}")
        if data.get("canceled_at"):
            details.append(f"取消时间：{data.get('canceled_at')}")

        sections = [
            "## 待办已取消",
            f"- 事项：{title}",
            "",
            "## 取消信息",
            _markdown_bullets(details),
            "",
            "> 边界：这是待办取消确认，只把已匹配的结构化待办标记为 canceled，不会删除记录或写入长期记忆。",
        ]
        return "\n".join(sections).strip()

    def format_personal_todo_reopen(reopen_json: str) -> str:
        if not reopen_json.strip():
            return "Error: reopen_json is required"
        data = json.loads(reopen_json)
        if not isinstance(data, dict):
            return "Error: reopen_json must be a JSON object"
        if data.get("status") != "open" or not data.get("reopened_at"):
            return "Error: reopen_json must be a reopened personal todo object"

        details = [
            f"事项：{data.get('title') or '未命名待办'}",
            f"状态：{data.get('status') or 'open'}",
            f"优先级：{data.get('priority') or 'normal'}",
        ]
        if data.get("due_at"):
            details.append(f"计划时间：{data.get('due_at')}")
        reason = str(data.get("reopen_reason") or "").strip()
        if reason:
            details.append(f"恢复原因：{reason}")
        if data.get("reopened_at"):
            details.append(f"恢复时间：{data.get('reopened_at')}")

        sections = [
            "## 待办已恢复",
            _markdown_bullets(details),
            "",
            "## 下一步",
            "- 这项待办已回到未完成列表。",
            "- 后续可以继续修改、完成或取消它。",
            "",
            "> 边界：这是待办恢复确认，只恢复已匹配的结构化待办，不会新增待办、写复盘或写入长期记忆。",
        ]
        return "\n".join(sections).strip()

    def format_personal_todo_completion(completion_json: str) -> str:
        if not completion_json.strip():
            return "Error: completion_json is required"
        data = json.loads(completion_json)
        if not isinstance(data, dict):
            return "Error: completion_json must be a JSON object"
        if data.get("status") != "done":
            return "Error: completion_json must be a completed personal todo object"

        title = str(data.get("title") or "未命名待办").strip()
        result = str(data.get("result") or "").strip()
        details = []
        if data.get("priority"):
            details.append(f"优先级：{data.get('priority')}")
        if data.get("due_at"):
            details.append(f"原计划时间：{data.get('due_at')}")
        if data.get("completed_at"):
            details.append(f"完成时间：{data.get('completed_at')}")
        if result:
            details.append(f"完成结果：{result}")

        sections = [
            "## 待办已完成",
            f"- 事项：{title}",
            "",
            "## 完成信息",
            _markdown_bullets(details),
            "",
            "> 边界：这是待办完成确认，只格式化已完成结果，不会新增待办、复盘或长期记忆。",
        ]
        return "\n".join(sections).strip()

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

    def format_personal_review_entry(review_json: str) -> str:
        if not review_json.strip():
            return "Error: review_json is required"
        data = json.loads(review_json)
        if not isinstance(data, dict):
            return "Error: review_json must be a JSON object"
        if not data.get("summary"):
            return "Error: review_json must be a personal_review_add object"

        completed = data.get("completed") if isinstance(data.get("completed"), list) else []
        blockers = data.get("blockers") if isinstance(data.get("blockers"), list) else []
        next_step = str(data.get("next_step") or "").strip()

        sections = [
            "## 复盘已记录",
            f"- 摘要：{data.get('summary')}",
            "",
            "## 完成事项",
            _markdown_bullets(completed),
            "",
            "## 卡点",
            _markdown_bullets(blockers),
            "",
            "## 下一步",
            f"- {next_step}" if next_step else "- 暂无明确下一步",
            "",
            "> 边界：这是复盘记录确认，只格式化已保存结果，不会自动新增待办、完成待办或写入长期记忆。",
        ]
        return "\n".join(sections).strip()

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

    def format_personal_review_recent(review_list_json: str) -> str:
        if not review_list_json.strip():
            return "Error: review_list_json is required"
        data = json.loads(review_list_json)
        if not isinstance(data, dict):
            return "Error: review_list_json must be a JSON object"
        items = data.get("items")
        if not isinstance(items, list):
            return "Error: review_list_json must contain an items list"

        review_lines = []
        next_steps = []
        blockers = []
        for index, item in enumerate([row for row in items if isinstance(row, dict)][:8], start=1):
            summary = str(item.get("summary") or "").strip()
            completed = item.get("completed") if isinstance(item.get("completed"), list) else []
            item_blockers = item.get("blockers") if isinstance(item.get("blockers"), list) else []
            next_step = str(item.get("next_step") or "").strip()
            title = summary or "未填写复盘摘要"
            details = []
            if completed:
                details.append("完成：" + "、".join(str(row) for row in completed[:3]))
            if item_blockers:
                details.append("卡点：" + "、".join(str(row) for row in item_blockers[:3]))
                blockers.extend(str(row) for row in item_blockers if str(row).strip())
            if next_step:
                details.append(f"下一步：{next_step}")
                next_steps.append(next_step)
            suffix = f"\n   {'；'.join(details)}" if details else ""
            review_lines.append(f"{index}. {title}{suffix}")

        sections = [
            "## 最近复盘",
            f"- 当前显示：{len([row for row in items if isinstance(row, dict)])} 条",
            "",
            "## 复盘明细",
            "\n".join(review_lines) if review_lines else "暂无近期复盘。",
            "",
            "## 近期卡点",
            _markdown_bullets(blockers[:5]),
            "",
            "## 下一步线索",
            _markdown_bullets(next_steps[:5]),
            "",
            "> 边界：这是复盘查询结果，只读取结构化复盘，不会自动新增、修改待办或写入记忆。",
        ]
        return "\n".join(sections).strip()

    def personal_briefing_generate(
        todo_limit: int = 8,
        review_limit: int = 3,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        briefing = personal_store.generate_briefing(
            user_scope=_scope(__runtime_context, user_scope),
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        return json.dumps(briefing, ensure_ascii=False, indent=2)

    def format_personal_briefing(briefing_json: str) -> str:
        if not briefing_json.strip():
            return "Error: briefing_json is required"
        data = json.loads(briefing_json)
        if not isinstance(data, dict):
            return "Error: briefing_json must be a JSON object"
        if not {"open_todos", "recent_reviews", "suggested_focus"}.issubset(data):
            return "Error: briefing_json must be a personal_briefing_generate object"

        open_todos = data.get("open_todos") if isinstance(data.get("open_todos"), list) else []
        urgent_todos = data.get("urgent_todos") if isinstance(data.get("urgent_todos"), list) else []
        recent_reviews = (
            data.get("recent_reviews")
            if isinstance(data.get("recent_reviews"), list)
            else []
        )
        next_steps = data.get("next_steps") if isinstance(data.get("next_steps"), list) else []

        todo_lines = []
        sorted_todos = sorted(
            [todo for todo in open_todos if isinstance(todo, dict)],
            key=PersonalStore._todo_sort_key,
        )
        for index, todo in enumerate(sorted_todos[:6], start=1):
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            if not title:
                continue
            details = []
            if todo.get("priority"):
                details.append(f"优先级：{todo.get('priority')}")
            if todo.get("due_at"):
                details.append(f"时间：{todo.get('due_at')}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            todo_lines.append(f"{index}. {title}{suffix}")

        urgent_lines = []
        for todo in urgent_todos[:4]:
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            if title:
                urgent_lines.append(f"- {title}")

        review_lines = []
        for review in recent_reviews[:3]:
            if not isinstance(review, dict):
                continue
            summary = str(review.get("summary") or "").strip()
            next_step = str(review.get("next_step") or "").strip()
            if summary and next_step:
                review_lines.append(f"- {summary}；下一步：{next_step}")
            elif summary:
                review_lines.append(f"- {summary}")
            elif next_step:
                review_lines.append(f"- 下一步：{next_step}")

        sections = [
            "## 个人简报",
            f"- 当前重点：{data.get('suggested_focus') or '待确认'}",
            f"- 未完成待办：{len(open_todos)} 项",
            f"- 紧急/高优先级：{len(urgent_todos)} 项",
            "",
            "## 待办",
            "\n".join(todo_lines) if todo_lines else "暂无未完成待办。",
            "",
            "## 紧急项",
            "\n".join(urgent_lines) if urgent_lines else "- 暂无紧急项。",
            "",
            "## 最近复盘",
            "\n".join(review_lines) if review_lines else "- 暂无近期复盘。",
            "",
            "## 下一步",
            _markdown_bullets(next_steps),
            "",
            "> 边界：这是个人简报，只读取待办和复盘，不会自动新增、完成或修改待办。",
        ]
        return "\n".join(sections).strip()

    def personal_time_blocks_generate(
        todo_limit: int = 9,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        plan = personal_store.generate_time_blocks(
            user_scope=_scope(__runtime_context, user_scope),
            todo_limit=todo_limit,
        )
        return json.dumps(plan, ensure_ascii=False, indent=2)

    def format_personal_time_blocks(time_blocks_json: str) -> str:
        if not time_blocks_json.strip():
            return "Error: time_blocks_json is required"
        data = json.loads(time_blocks_json)
        if not isinstance(data, dict):
            return "Error: time_blocks_json must be a JSON object"
        if not {"blocks", "first_action", "source_todo_count"}.issubset(data):
            return "Error: time_blocks_json must be a personal_time_blocks_generate object"

        blocks = data.get("blocks") if isinstance(data.get("blocks"), list) else []
        block_lines = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "时间块").strip()
            focus = str(block.get("focus") or "留作缓冲").strip()
            items = block.get("items") if isinstance(block.get("items"), list) else []
            item_lines = []
            for item in items[:4]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                details = []
                if item.get("priority"):
                    details.append(f"优先级：{item.get('priority')}")
                if item.get("due_at"):
                    details.append(f"时间：{item.get('due_at')}")
                suffix = f"（{'；'.join(details)}）" if details else ""
                item_lines.append(f"  - {title}{suffix}")
            if item_lines:
                block_lines.append(f"- {name}：{focus}\n" + "\n".join(item_lines))
            else:
                block_lines.append(f"- {name}：{focus}")

        sections = [
            "## 时间块计划",
            f"- 待安排事项：{data.get('source_todo_count') or 0} 项",
            f"- 第一步：{data.get('first_action') or '先确认今天最重要的一件事。'}",
            "",
            "## 上午 / 下午 / 晚上",
            "\n".join(block_lines) if block_lines else "- 暂无时间块，请先补充待办。",
            "",
            f"> 边界：{data.get('note') or '这是时间块建议，不会自动修改待办状态。'}",
        ]
        return "\n".join(sections).strip()

    def personal_daily_workflow_generate(
        todo_limit: int = 9,
        review_limit: int = 3,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        workflow = personal_store.generate_daily_workflow(
            user_scope=_scope(__runtime_context, user_scope),
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        return json.dumps(workflow, ensure_ascii=False, indent=2)

    def format_personal_daily_workflow(workflow_json: str) -> str:
        if not workflow_json.strip():
            return "Error: workflow_json is required"
        data = json.loads(workflow_json)
        if not isinstance(data, dict):
            return "Error: workflow_json must be a JSON object"

        priorities = data.get("today_priorities") if isinstance(data.get("today_priorities"), list) else []
        priority_lines = []
        for index, item in enumerate(priorities, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            details = []
            if item.get("priority"):
                details.append(f"优先级：{item.get('priority')}")
            if item.get("due_at"):
                details.append(f"时间：{item.get('due_at')}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            priority_lines.append(f"{index}. {title}{suffix}")

        block_lines = []
        blocks = data.get("time_blocks") if isinstance(data.get("time_blocks"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "时间块").strip()
            items = block.get("items") if isinstance(block.get("items"), list) else []
            titles = [
                str(item.get("title") or "").strip()
                for item in items
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]
            block_lines.append(f"- {name}：" + ("、".join(titles) if titles else "留作缓冲"))

        needs_confirmation = data.get("needs_confirmation")
        if not isinstance(needs_confirmation, list):
            needs_confirmation = []
        sections = [
            "## 今日工作流",
            f"- 当前重点：{data.get('current_focus') or '待确认'}",
            f"- 第一步：{data.get('first_action') or '先确认今天最重要的一件事。'}",
            "",
            "## 今日优先级",
            "\n".join(priority_lines) if priority_lines else "暂无明确待办，请先确认今天最重要的一件事。",
            "",
            "## 时间块",
            "\n".join(block_lines) if block_lines else "- 暂无时间块，请先补充待办。",
            "",
            "## 复盘提醒",
            _markdown_bullets(data.get("review_reminders") if isinstance(data.get("review_reminders"), list) else []),
            "",
            "## 需要确认",
            _markdown_bullets(needs_confirmation),
            "",
            f"> 边界：{data.get('note') or '这是个人工作流建议，不会自动完成或修改待办。'}",
        ]
        return "\n".join(sections).strip()

    def format_personal_focus_card(focus_card_json: str) -> str:
        if not focus_card_json.strip():
            return "Error: focus_card_json is required"
        data = json.loads(focus_card_json)
        if not isinstance(data, dict):
            return "Error: focus_card_json must be a JSON object"
        if data.get("type") != "personal_focus_card":
            return "Error: focus_card_json type must be personal_focus_card"

        focus_todo = data.get("focus_todo") if isinstance(data.get("focus_todo"), dict) else {}
        focus = str(data.get("focus") or focus_todo.get("title") or "待确认").strip()
        priority = str(focus_todo.get("priority") or "").strip()
        due_at = str(focus_todo.get("due_at") or "").strip()
        details = []
        if priority:
            details.append(f"优先级：{priority}")
        if due_at:
            details.append(f"时间：{due_at}")
        focus_suffix = f"（{'；'.join(details)}）" if details else ""

        needs_confirmation = data.get("needs_confirmation")
        if not isinstance(needs_confirmation, list):
            needs_confirmation = []

        sections = [
            "## 当前聚焦",
            f"- 先做：{focus}{focus_suffix}",
            f"- 原因：{data.get('why_now') or '它是当前更值得先推进的事项。'}",
            f"- 第一步：{data.get('first_action') or '先用 25 分钟推进这个事项。'}",
            "",
            "## 暂时延后",
            _markdown_bullets(data.get("defer") if isinstance(data.get("defer"), list) else []),
            "",
            "## 卡点提醒",
            _markdown_bullets(data.get("blockers") if isinstance(data.get("blockers"), list) else []),
            "",
            "## 复盘线索",
            _markdown_bullets(
                data.get("review_next_steps")
                if isinstance(data.get("review_next_steps"), list)
                else []
            ),
            "",
            "## 需要确认",
            _markdown_bullets(needs_confirmation),
            "",
            f"> 边界：{data.get('note') or '这是个人聚焦建议，不会自动完成、修改或新增待办。'}",
        ]
        return "\n".join(sections).strip()

    def personal_focus_card_generate(
        todo_limit: int = 8,
        review_limit: int = 3,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        card = personal_store.generate_focus_card(
            user_scope=_scope(__runtime_context, user_scope),
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        return json.dumps(card, ensure_ascii=False, indent=2)

    def personal_day_review_plan_generate(
        today_summary: str = "",
        completed: list[str] | None = None,
        blockers: list[str] | None = None,
        tomorrow_focus: str = "",
        todo_limit: int = 6,
        review_limit: int = 3,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        plan = personal_store.generate_day_review_plan(
            user_scope=_scope(__runtime_context, user_scope),
            today_summary=today_summary,
            completed=completed or [],
            blockers=blockers or [],
            tomorrow_focus=tomorrow_focus,
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        return json.dumps(plan, ensure_ascii=False, indent=2)

    def format_personal_day_review_plan(plan_json: str) -> str:
        if not plan_json.strip():
            return "Error: plan_json is required"
        data = json.loads(plan_json)
        if not isinstance(data, dict):
            return "Error: plan_json must be a JSON object"
        if data.get("type") != "personal_day_review_plan":
            return "Error: plan_json type must be personal_day_review_plan"

        review = data.get("review_draft") if isinstance(data.get("review_draft"), dict) else {}
        tomorrow = data.get("tomorrow_plan") if isinstance(data.get("tomorrow_plan"), dict) else {}
        priority_todos = (
            tomorrow.get("priority_todos")
            if isinstance(tomorrow.get("priority_todos"), list)
            else []
        )
        todo_lines = []
        for index, todo in enumerate(priority_todos, start=1):
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            if not title:
                continue
            details = []
            if todo.get("priority"):
                details.append(f"优先级：{todo.get('priority')}")
            if todo.get("due_at"):
                details.append(f"时间：{todo.get('due_at')}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            todo_lines.append(f"{index}. {title}{suffix}")

        block_lines = []
        time_blocks = (
            tomorrow.get("time_blocks") if isinstance(tomorrow.get("time_blocks"), list) else []
        )
        for block in time_blocks:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "时间块").strip()
            items = block.get("items") if isinstance(block.get("items"), list) else []
            titles = [
                str(item.get("title") or "").strip()
                for item in items
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]
            block_lines.append(f"- {name}：" + ("、".join(titles) if titles else "留作缓冲"))

        confirmations = data.get("needs_confirmation")
        if not isinstance(confirmations, list):
            confirmations = []
        next_actions = data.get("next_actions")
        if not isinstance(next_actions, list):
            next_actions = []

        sections = [
            "## 今日复盘草稿",
            f"- 总结：{review.get('summary') or '待补充今日复盘摘要'}",
            "完成：",
            _markdown_bullets(review.get("completed") if isinstance(review.get("completed"), list) else []),
            "卡点：",
            _markdown_bullets(review.get("blockers") if isinstance(review.get("blockers"), list) else []),
            f"- 明天第一步：{review.get('next_step') or tomorrow.get('first_step') or '待确认'}",
            "",
            "## 明日计划",
            f"- 重点：{tomorrow.get('focus') or '待确认'}",
            f"- 第一步：{tomorrow.get('first_step') or review.get('next_step') or '待确认'}",
            "",
            "## 明日优先待办",
            "\n".join(todo_lines) if todo_lines else "暂无明确待办，请先确认明天第一步。",
            "",
            "## 明日时间块",
            "\n".join(block_lines) if block_lines else "- 暂无时间块，请先补充待办。",
            "",
            "## 需要确认",
            _markdown_bullets(confirmations),
            "",
            "## 可执行下一步",
            _markdown_bullets(next_actions),
            "",
            f"> 边界：{data.get('note') or '这是个人日复盘和明日计划草稿，不会自动写入复盘或待办。'}",
        ]
        return "\n".join(sections).strip()

    def personal_weekly_plan_generate(
        week_goal: str = "",
        focus_areas: list[str] | None = None,
        constraints: list[str] | None = None,
        todo_limit: int = 12,
        review_limit: int = 5,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        plan = personal_store.generate_weekly_plan(
            user_scope=_scope(__runtime_context, user_scope),
            week_goal=week_goal,
            focus_areas=focus_areas or [],
            constraints=constraints or [],
            todo_limit=todo_limit,
            review_limit=review_limit,
        )
        return json.dumps(plan, ensure_ascii=False, indent=2)

    def format_personal_weekly_plan(plan_json: str) -> str:
        if not plan_json.strip():
            return "Error: plan_json is required"
        data = json.loads(plan_json)
        if not isinstance(data, dict):
            return "Error: plan_json must be a JSON object"
        if data.get("type") != "personal_weekly_plan":
            return "Error: plan_json type must be personal_weekly_plan"

        priority_lines = []
        priorities = (
            data.get("weekly_priorities")
            if isinstance(data.get("weekly_priorities"), list)
            else []
        )
        for index, todo in enumerate(priorities, start=1):
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            if not title:
                continue
            details = []
            if todo.get("priority"):
                details.append(f"优先级：{todo.get('priority')}")
            if todo.get("due_at"):
                details.append(f"时间：{todo.get('due_at')}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            priority_lines.append(f"{index}. {title}{suffix}")

        milestone_lines = []
        milestones = data.get("milestones") if isinstance(data.get("milestones"), list) else []
        for milestone in milestones:
            if not isinstance(milestone, dict):
                continue
            name = str(milestone.get("name") or "里程碑").strip()
            focus = str(milestone.get("focus") or "").strip()
            done = str(milestone.get("done") or "").strip()
            if focus and done:
                milestone_lines.append(f"- {name}：{focus}；完成标准：{done}")
            elif focus:
                milestone_lines.append(f"- {name}：{focus}")

        confirmations = data.get("needs_confirmation")
        if not isinstance(confirmations, list):
            confirmations = []
        next_actions = data.get("next_actions")
        if not isinstance(next_actions, list):
            next_actions = []

        sections = [
            "## 本周计划草稿",
            f"- 本周目标：{data.get('week_goal') or '待确认'}",
            f"- 第一步：{data.get('first_action') or '先确认本周最重要目标。'}",
            "",
            "## 本周重点",
            _markdown_bullets(data.get("focus_areas") if isinstance(data.get("focus_areas"), list) else []),
            "",
            "## 优先待办",
            "\n".join(priority_lines) if priority_lines else "暂无明确待办，请先确认本周目标。",
            "",
            "## 里程碑",
            "\n".join(milestone_lines) if milestone_lines else "- 暂无明确里程碑。",
            "",
            "## 复盘线索",
            _markdown_bullets(
                data.get("review_signals")
                if isinstance(data.get("review_signals"), list)
                else []
            ),
            "",
            "## 约束",
            _markdown_bullets(data.get("constraints") if isinstance(data.get("constraints"), list) else []),
            "",
            "## 需要确认",
            _markdown_bullets(confirmations),
            "",
            "## 可执行下一步",
            _markdown_bullets(next_actions),
            "",
            f"> 边界：{data.get('note') or '这是个人周计划草稿，不会自动写入待办、复盘或长期记忆。'}",
        ]
        return "\n".join(sections).strip()

    def personal_inbox_triage(
        text: str,
        context: str = "",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        triage = personal_store.triage_inbox(
            text,
            user_scope=_scope(__runtime_context, user_scope),
            context=context,
        )
        return json.dumps(triage, ensure_ascii=False, indent=2)

    def format_personal_inbox_triage(triage_json: str) -> str:
        if not triage_json.strip():
            return "Error: triage_json is required"
        data = json.loads(triage_json)
        if not isinstance(data, dict):
            return "Error: triage_json must be a JSON object"
        if data.get("type") != "personal_inbox_triage":
            return "Error: triage_json type must be personal_inbox_triage"

        suggested_todos = (
            data.get("suggested_todos") if isinstance(data.get("suggested_todos"), list) else []
        )
        todo_lines = []
        for index, todo in enumerate(suggested_todos, start=1):
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            if not title:
                continue
            details = []
            if todo.get("priority"):
                details.append(f"优先级：{todo.get('priority')}")
            if todo.get("due_at"):
                details.append(f"时间：{todo.get('due_at')}")
            notes = str(todo.get("notes") or "").strip()
            if notes:
                details.append(f"备注：{notes}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            todo_lines.append(f"{index}. {title}{suffix}")

        review = data.get("suggested_review") if isinstance(data.get("suggested_review"), dict) else {}
        memory = data.get("suggested_memory") if isinstance(data.get("suggested_memory"), dict) else {}
        confirmations = data.get("needs_confirmation")
        if not isinstance(confirmations, list):
            confirmations = []
        next_actions = data.get("next_actions")
        if not isinstance(next_actions, list):
            next_actions = []

        memory_lines = []
        if memory:
            if memory.get("category"):
                memory_lines.append(f"- 类型：{memory.get('category')}")
            if memory.get("content"):
                memory_lines.append(f"- 内容：{memory.get('content')}")
            if memory.get("reason"):
                memory_lines.append(f"- 原因：{memory.get('reason')}")

        sections = [
            "## 收件箱整理",
            f"- 判断：{data.get('intent') or '待确认'}",
            f"- 原文：{data.get('source_text') or '无'}",
            "",
            "## 待办候选",
            "\n".join(todo_lines) if todo_lines else "暂无明确待办候选。",
            "",
            "## 复盘候选",
            f"- 总结：{review.get('summary') or '暂无明确复盘摘要'}",
            "完成：",
            _markdown_bullets(review.get("completed") if isinstance(review.get("completed"), list) else []),
            "卡点：",
            _markdown_bullets(review.get("blockers") if isinstance(review.get("blockers"), list) else []),
            f"- 下一步：{review.get('next_step') or '待确认'}",
            "",
            "## 长期记忆候选",
            "\n".join(memory_lines) if memory_lines else "暂无长期记忆候选。",
            "",
            "## 需要确认",
            _markdown_bullets(confirmations),
            "",
            "## 可执行下一步",
            _markdown_bullets(next_actions),
            "",
            f"> 边界：{data.get('note') or '这是个人秘书收件箱整理建议，不会自动写入待办、复盘或长期记忆。'}",
        ]
        return "\n".join(sections).strip()

    def personal_inbox_commit(
        triage_json: str,
        commit_todos: bool = True,
        commit_review: bool = True,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not triage_json.strip():
            return "Error: triage_json is required"
        data = json.loads(triage_json)
        if not isinstance(data, dict):
            return "Error: triage_json must be a JSON object"
        try:
            result = personal_store.commit_inbox_triage(
                data,
                user_scope=_scope(__runtime_context, user_scope),
                commit_todos=commit_todos,
                commit_review=commit_review,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    def format_personal_inbox_commit(commit_json: str) -> str:
        if not commit_json.strip():
            return "Error: commit_json is required"
        data = json.loads(commit_json)
        if not isinstance(data, dict):
            return "Error: commit_json must be a JSON object"
        if data.get("type") != "personal_inbox_commit":
            return "Error: commit_json type must be personal_inbox_commit"

        todos = data.get("written_todos") if isinstance(data.get("written_todos"), list) else []
        todo_lines = []
        for todo in todos:
            if not isinstance(todo, dict):
                continue
            title = str(todo.get("title") or "").strip()
            details = []
            priority = str(todo.get("priority") or "").strip()
            due_at = str(todo.get("due_at") or "").strip()
            notes = str(todo.get("notes") or "").strip()
            if priority:
                details.append(f"优先级：{priority}")
            if due_at:
                details.append(f"截止：{due_at}")
            if notes:
                details.append(f"备注：{notes}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            todo_lines.append(f"- {title or '未命名待办'}{suffix}")

        review = data.get("written_review") if isinstance(data.get("written_review"), dict) else None
        review_lines = []
        if review:
            review_lines.append(f"- 总结：{review.get('summary') or '暂无'}")
            completed = review.get("completed") if isinstance(review.get("completed"), list) else []
            blockers = review.get("blockers") if isinstance(review.get("blockers"), list) else []
            review_lines.append("完成：")
            review_lines.append(_markdown_bullets(completed))
            review_lines.append("卡点：")
            review_lines.append(_markdown_bullets(blockers))
            review_lines.append(f"- 下一步：{review.get('next_step') or '暂无'}")

        skipped = data.get("skipped") if isinstance(data.get("skipped"), list) else []
        skipped_lines = []
        for item in skipped:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "unknown").strip()
            reason = str(item.get("reason") or "需要单独确认").strip()
            candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
            content = str(candidate.get("content") or candidate.get("summary") or "").strip()
            label = f"{item_type}：{content}" if content else item_type
            skipped_lines.append(f"- {label}（{reason}）")

        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        sections = [
            "## 个人收件箱已批量写入",
            f"- 待办：{len(todo_lines)} 条",
            f"- 复盘：{'已写入' if review else '未写入'}",
            f"- 长期记忆候选：{'有，需单独确认' if source.get('has_memory_candidate') else '暂无'}",
            "",
            "## 已写入待办",
            "\n".join(todo_lines) if todo_lines else "暂无待办写入。",
            "",
            "## 已写入复盘",
            "\n".join(review_lines) if review_lines else "暂无复盘写入。",
            "",
            "## 暂未写入",
            "\n".join(skipped_lines) if skipped_lines else "- 暂无",
            "",
            "## 下一步",
            "- 如果长期目标、偏好或背景需要长期保存，请再次确认后再写入长期记忆。",
            "- 后续生成个人简报、时间块和复盘计划时，会读取这些结构化记录。",
            "",
            f"> 边界：{data.get('note') or '这是批量写入确认，不会自动写入长期记忆。'}",
        ]
        return "\n".join(sections).strip()

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
            name="format_personal_todo_entry",
            description=(
                "Format a personal_todo_add JSON object into a concise Chinese "
                "Markdown todo creation confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["todo_json"],
                "properties": {
                    "todo_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_add.",
                    },
                },
            },
            handler=format_personal_todo_entry,
            tags=("personal", "todo", "format", "user-facing"),
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
            name="personal_todo_search",
            description="Search structured personal todos by title, notes, result, or cancel reason.",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "done", "canceled", "all", ""],
                        "default": "open",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
            },
            handler=personal_todo_search,
            tags=("personal", "todo", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_todo_list",
            description=(
                "Format a personal_todo_list JSON object into a concise Chinese "
                "Markdown todo list for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["todo_list_json"],
                "properties": {
                    "todo_list_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_list.",
                    },
                },
            },
            handler=format_personal_todo_list,
            tags=("personal", "todo", "format", "user-facing"),
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
            name="personal_todo_complete_by_title",
            description=(
                "Mark one open personal todo as done by matching a title fragment. "
                "Returns an error when none or multiple open todos match."
            ),
            input_schema={
                "type": "object",
                "required": ["title_query"],
                "properties": {
                    "title_query": {"type": "string"},
                    "result": {"type": "string"},
                },
            },
            handler=personal_todo_complete_by_title,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_todo_update_by_title",
            description=(
                "Update one open personal todo by matching a title fragment. "
                "Can update title, priority, due_at, and notes; returns an error "
                "when none or multiple open todos match."
            ),
            input_schema={
                "type": "object",
                "required": ["title_query"],
                "properties": {
                    "title_query": {"type": "string"},
                    "new_title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent", ""]},
                    "due_at": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            handler=personal_todo_update_by_title,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_todo_cancel_by_title",
            description=(
                "Cancel one open personal todo by matching a title fragment. "
                "Returns an error when none or multiple open todos match."
            ),
            input_schema={
                "type": "object",
                "required": ["title_query"],
                "properties": {
                    "title_query": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
            handler=personal_todo_cancel_by_title,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_todo_reopen_by_title",
            description=(
                "Reopen one done or canceled personal todo by matching a title fragment. "
                "Returns an error when none or multiple inactive todos match."
            ),
            input_schema={
                "type": "object",
                "required": ["title_query"],
                "properties": {
                    "title_query": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
            handler=personal_todo_reopen_by_title,
            tags=("personal", "todo", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_todo_update",
            description=(
                "Format a personal_todo_update_by_title JSON object into a concise "
                "Chinese Markdown todo update confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["todo_json"],
                "properties": {
                    "todo_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_update_by_title.",
                    },
                },
            },
            handler=format_personal_todo_update,
            tags=("personal", "todo", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_todo_reopen",
            description=(
                "Format a personal_todo_reopen_by_title JSON object into a concise "
                "Chinese Markdown todo reopen confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["reopen_json"],
                "properties": {
                    "reopen_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_reopen_by_title.",
                    },
                },
            },
            handler=format_personal_todo_reopen,
            tags=("personal", "todo", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_todo_cancellation",
            description=(
                "Format a personal_todo_cancel_by_title JSON object into a concise "
                "Chinese Markdown todo cancellation confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["cancellation_json"],
                "properties": {
                    "cancellation_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_cancel_by_title.",
                    },
                },
            },
            handler=format_personal_todo_cancellation,
            tags=("personal", "todo", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_todo_completion",
            description=(
                "Format a personal_todo_complete JSON object into a concise "
                "Chinese Markdown completion confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["completion_json"],
                "properties": {
                    "completion_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_todo_complete.",
                    },
                },
            },
            handler=format_personal_todo_completion,
            tags=("personal", "todo", "format", "user-facing"),
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
            name="format_personal_review_entry",
            description=(
                "Format a personal_review_add JSON object into a concise Chinese "
                "Markdown review creation confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["review_json"],
                "properties": {
                    "review_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_review_add.",
                    },
                },
            },
            handler=format_personal_review_entry,
            tags=("personal", "review", "format", "user-facing"),
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
    registry.register(
        RegisteredTool(
            name="format_personal_review_recent",
            description=(
                "Format a personal_review_recent JSON object into a concise Chinese "
                "Markdown review summary for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["review_list_json"],
                "properties": {
                    "review_list_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_review_recent.",
                    },
                },
            },
            handler=format_personal_review_recent,
            tags=("personal", "review", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_briefing_generate",
            description=(
                "Generate a structured personal briefing from open todos and "
                "recent reviews for the current user."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "todo_limit": {"type": "integer"},
                    "review_limit": {"type": "integer"},
                },
            },
            handler=personal_briefing_generate,
            tags=("personal", "briefing", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_briefing",
            description=(
                "Format a personal_briefing_generate JSON object into a concise "
                "Chinese Markdown personal briefing for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["briefing_json"],
                "properties": {
                    "briefing_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_briefing_generate.",
                    },
                },
            },
            handler=format_personal_briefing,
            tags=("personal", "briefing", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_time_blocks_generate",
            description="Generate morning/afternoon/evening time blocks from open personal todos.",
            input_schema={
                "type": "object",
                "properties": {
                    "todo_limit": {"type": "integer"},
                },
            },
            handler=personal_time_blocks_generate,
            tags=("personal", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_time_blocks",
            description=(
                "Format a personal_time_blocks_generate JSON object into a concise "
                "Chinese Markdown time-block plan for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["time_blocks_json"],
                "properties": {
                    "time_blocks_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_time_blocks_generate.",
                    },
                },
            },
            handler=format_personal_time_blocks,
            tags=("personal", "planning", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_daily_workflow_generate",
            description=(
                "Generate a daily personal workflow by combining open todos, "
                "recent reviews, priorities, time blocks, first action, and confirmations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "todo_limit": {"type": "integer"},
                    "review_limit": {"type": "integer"},
                },
            },
            handler=personal_daily_workflow_generate,
            tags=("personal", "workflow", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_daily_workflow",
            description=(
                "Format a personal_daily_workflow_generate JSON object into a concise "
                "Chinese Markdown daily workflow for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["workflow_json"],
                "properties": {
                    "workflow_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_daily_workflow_generate.",
                    },
                },
            },
            handler=format_personal_daily_workflow,
            tags=("personal", "workflow", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_focus_card",
            description=(
                "Format a personal_focus_card_generate JSON object into a concise "
                "Chinese Markdown focus card for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["focus_card_json"],
                "properties": {
                    "focus_card_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_focus_card_generate.",
                    },
                },
            },
            handler=format_personal_focus_card,
            tags=("personal", "focus", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_focus_card_generate",
            description=(
                "Generate a concise current-focus card from open personal todos "
                "and recent reviews without writing data."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "todo_limit": {"type": "integer"},
                    "review_limit": {"type": "integer"},
                },
            },
            handler=personal_focus_card_generate,
            tags=("personal", "focus", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_day_review_plan",
            description=(
                "Format a personal_day_review_plan_generate JSON object into a concise "
                "Chinese Markdown day review and tomorrow plan for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["plan_json"],
                "properties": {
                    "plan_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_day_review_plan_generate.",
                    },
                },
            },
            handler=format_personal_day_review_plan,
            tags=("personal", "review", "planning", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_day_review_plan_generate",
            description=(
                "Generate a structured personal day review and tomorrow plan draft "
                "from optional user summary, open todos, and recent reviews without writing data."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "today_summary": {"type": "string"},
                    "completed": {"type": "array", "items": {"type": "string"}},
                    "blockers": {"type": "array", "items": {"type": "string"}},
                    "tomorrow_focus": {"type": "string"},
                    "todo_limit": {"type": "integer"},
                    "review_limit": {"type": "integer"},
                },
            },
            handler=personal_day_review_plan_generate,
            tags=("personal", "review", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_weekly_plan_generate",
            description=(
                "Generate a structured personal weekly plan draft from open todos, "
                "recent reviews, optional week goal, focus areas, and constraints without writing data."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "week_goal": {"type": "string"},
                    "focus_areas": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "todo_limit": {"type": "integer"},
                    "review_limit": {"type": "integer"},
                },
            },
            handler=personal_weekly_plan_generate,
            tags=("personal", "weekly", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_weekly_plan",
            description=(
                "Format a personal_weekly_plan_generate JSON object into a concise "
                "Chinese Markdown weekly plan for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["plan_json"],
                "properties": {
                    "plan_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_weekly_plan_generate.",
                    },
                },
            },
            handler=format_personal_weekly_plan,
            tags=("personal", "weekly", "planning", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_inbox_triage",
            description=(
                "Triage a messy personal message into suggested todos, review, memory, "
                "confirmation questions, and next actions without writing data."
            ),
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "context": {"type": "string"},
                },
            },
            handler=personal_inbox_triage,
            tags=("personal", "inbox", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_inbox_triage",
            description=(
                "Format a personal_inbox_triage JSON object into a concise Chinese "
                "Markdown inbox triage summary for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["triage_json"],
                "properties": {
                    "triage_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_inbox_triage.",
                    },
                },
            },
            handler=format_personal_inbox_triage,
            tags=("personal", "inbox", "planning", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="personal_inbox_commit",
            description=(
                "Commit a confirmed personal_inbox_triage JSON into structured todos "
                "and optional review. It does not write long-term memory candidates."
            ),
            input_schema={
                "type": "object",
                "required": ["triage_json"],
                "properties": {
                    "triage_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_inbox_triage.",
                    },
                    "commit_todos": {"type": "boolean"},
                    "commit_review": {"type": "boolean"},
                },
            },
            handler=personal_inbox_commit,
            tags=("personal", "inbox", "todo", "review", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_personal_inbox_commit",
            description=(
                "Format a personal_inbox_commit JSON object into a concise Chinese "
                "Markdown confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["commit_json"],
                "properties": {
                    "commit_json": {
                        "type": "string",
                        "description": "JSON string returned by personal_inbox_commit.",
                    },
                },
            },
            handler=format_personal_inbox_commit,
            tags=("personal", "inbox", "format", "user-facing"),
        )
    )
