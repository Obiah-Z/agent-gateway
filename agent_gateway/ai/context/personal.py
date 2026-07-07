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
