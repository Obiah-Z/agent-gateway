from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any
from uuid import uuid4

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


DIET_TABLES = {
    "user_profiles",
    "weight_logs",
    "meal_logs",
    "daily_nutrition_summaries",
    "diet_plans",
}


def _today() -> str:
    return datetime.now().date().isoformat()


def _now() -> float:
    return time.time()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _scope_slug(user_scope: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._=-]+", "_", user_scope.strip())
    slug = slug.strip("._-")[:160]
    return slug or "global"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _provided_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """只保留调用方明确提供的字段，避免默认空值覆盖已有档案。"""

    return {key: value for key, value in fields.items() if value is not None}


def _clean_strings(items: list[object] | None) -> list[str]:
    """清洗用于聊天回复的字符串列表。"""

    return [str(item).strip() for item in items or [] if str(item).strip()]


def _markdown_bullets(items: list[object] | None) -> str:
    """把列表渲染成 Markdown bullet list。"""

    cleaned = _clean_strings(items)
    if not cleaned:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in cleaned)


@dataclass(slots=True)
class DietStore:
    """个人饮食数据存储。

    PostgreSQL 可用时作为主存储；不可用时写入 workspace 下的 JSONL，保证自用场景
    仍能记录和回放。
    """

    workspace_root: Path
    read_backend: Any | None = None
    write_backend: Any | None = None

    def get_profile(self, user_scope: str) -> dict[str, Any] | None:
        scope = self._normalize_scope(user_scope)
        if not scope:
            return None
        row = self._backend_get("user_profiles", scope)
        if row:
            return row
        rows = self._local_rows("user_profiles", user_scope=scope)
        return rows[-1] if rows else None

    def update_profile(self, user_scope: str, **fields: Any) -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        now = _now()
        existing = self.get_profile(scope) or {}
        provided = _provided_fields(fields)
        profile = {
            "user_scope": scope,
            "display_name": str(provided.get("display_name", existing.get("display_name", "")) or ""),
            "gender": str(provided.get("gender", existing.get("gender", "")) or ""),
            "birth_year": _as_int(provided.get("birth_year", existing.get("birth_year", 0))),
            "height_cm": _as_float(provided.get("height_cm", existing.get("height_cm", 0.0))),
            "current_weight_kg": _as_float(
                provided.get("current_weight_kg", existing.get("current_weight_kg", 0.0))
            ),
            "target_weight_kg": _as_float(
                provided.get("target_weight_kg", existing.get("target_weight_kg", 0.0))
            ),
            "activity_level": str(provided.get("activity_level", existing.get("activity_level", "")) or ""),
            "timezone": str(provided.get("timezone", existing.get("timezone", "Asia/Shanghai")) or "Asia/Shanghai"),
            "diet_preferences": _as_list(provided.get("diet_preferences", existing.get("diet_preferences", []))),
            "allergies": _as_list(provided.get("allergies", existing.get("allergies", []))),
            "medical_notes": str(provided.get("medical_notes", existing.get("medical_notes", "")) or ""),
            "created_at": _as_float(existing.get("created_at", now), now),
            "updated_at": now,
            "metadata": dict(existing.get("metadata", {}) if isinstance(existing.get("metadata"), dict) else {}),
        }
        self._upsert("user_profiles", profile)
        return profile

    def add_weight_log(
        self,
        user_scope: str,
        weight_kg: float,
        *,
        recorded_at: float | None = None,
        source: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        current = _now() if recorded_at is None else float(recorded_at)
        row = {
            "id": f"weight_{uuid4().hex}",
            "user_scope": scope,
            "weight_kg": _as_float(weight_kg),
            "recorded_at": current,
            "source": source,
            "metadata": dict(metadata or {}),
        }
        self._upsert("weight_logs", row)
        self.update_profile(scope, current_weight_kg=row["weight_kg"])
        return row

    def add_meal_log(
        self,
        user_scope: str,
        *,
        meal_type: str,
        raw_text: str,
        meal_date: str = "",
        items: list[dict[str, Any]] | None = None,
        estimated_calories: float = 0.0,
        protein_g: float = 0.0,
        carbs_g: float = 0.0,
        fat_g: float = 0.0,
        confidence: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        row = {
            "id": f"meal_{uuid4().hex}",
            "user_scope": scope,
            "meal_date": meal_date or _today(),
            "meal_type": meal_type or "unknown",
            "raw_text": raw_text,
            "items": list(items or []),
            "estimated_calories": _as_float(estimated_calories),
            "protein_g": _as_float(protein_g),
            "carbs_g": _as_float(carbs_g),
            "fat_g": _as_float(fat_g),
            "confidence": _as_float(confidence, 0.5),
            "logged_at": _now(),
            "metadata": dict(metadata or {}),
        }
        self._upsert("meal_logs", row)
        return row

    def list_meal_logs(self, user_scope: str, *, meal_date: str = "", limit: int = 20) -> list[dict[str, Any]]:
        scope = self._require_scope(user_scope)
        target_date = meal_date or _today()
        rows = self._list("meal_logs", filters={"user_scope": scope, "meal_date": target_date}, limit=limit)
        rows.sort(key=lambda row: str(row.get("logged_at", "")))
        return rows

    def triage_inbox(
        self,
        user_scope: str,
        text: str,
        *,
        context: str = "",
    ) -> dict[str, Any]:
        """把混合饮食输入整理成候选记录，不直接写入数据。"""

        scope = self._require_scope(user_scope)
        normalized = " ".join(str(text or "").strip().split())
        context_text = " ".join(str(context or "").strip().split())
        fragments = self._split_diet_fragments(normalized)
        suggested_meals = self._suggest_meals_from_fragments(fragments)
        weight_candidate = self._suggest_weight_from_text(normalized)
        profile_updates = self._suggest_profile_updates_from_text(normalized)
        needs_confirmation = self._diet_inbox_confirmations(
            normalized,
            suggested_meals=suggested_meals,
            weight_candidate=weight_candidate,
            profile_updates=profile_updates,
        )
        return {
            "generated_at": _now(),
            "user_scope": scope,
            "type": "diet_inbox_triage",
            "source_text": normalized,
            "context": context_text,
            "intent": self._diet_inbox_intent(
                suggested_meals=suggested_meals,
                weight_candidate=weight_candidate,
                profile_updates=profile_updates,
            ),
            "suggested_meals": suggested_meals,
            "suggested_weight": weight_candidate,
            "suggested_profile_updates": profile_updates,
            "needs_confirmation": needs_confirmation,
            "next_actions": self._diet_inbox_next_actions(
                suggested_meals=suggested_meals,
                weight_candidate=weight_candidate,
                profile_updates=profile_updates,
                confirmations=needs_confirmation,
            ),
            "note": "这是饮食输入整理建议，不会自动写入餐食、体重、档案或长期记忆。",
        }

    def commit_inbox_triage(
        self,
        user_scope: str,
        triage: dict[str, Any],
        *,
        commit_meals: bool = True,
        commit_weight: bool = True,
        commit_profile: bool = True,
    ) -> dict[str, Any]:
        """把用户已确认的饮食整理结果写入结构化数据。"""

        if triage.get("type") != "diet_inbox_triage":
            raise ValueError("triage type must be diet_inbox_triage")
        scope = self._require_scope(user_scope or str(triage.get("user_scope", "")))
        suggested_meals = triage.get("suggested_meals") if isinstance(triage.get("suggested_meals"), list) else []
        suggested_weight = (
            triage.get("suggested_weight") if isinstance(triage.get("suggested_weight"), dict) else None
        )
        profile_updates = (
            triage.get("suggested_profile_updates")
            if isinstance(triage.get("suggested_profile_updates"), dict)
            else {}
        )

        written_meals = []
        if commit_meals:
            for meal in suggested_meals:
                if not isinstance(meal, dict):
                    continue
                raw_text = str(meal.get("raw_text", "")).strip()
                if not raw_text:
                    continue
                written_meals.append(
                    self.add_meal_log(
                        scope,
                        meal_type=str(meal.get("meal_type", "unknown")),
                        raw_text=raw_text,
                        meal_date=str(meal.get("meal_date", "")),
                        estimated_calories=_as_float(meal.get("estimated_calories")),
                        protein_g=_as_float(meal.get("protein_g")),
                        carbs_g=_as_float(meal.get("carbs_g")),
                        fat_g=_as_float(meal.get("fat_g")),
                        confidence=_as_float(meal.get("confidence"), 0.5),
                        metadata={"source": "diet_inbox_commit"},
                    )
                )

        written_weight = None
        if commit_weight and suggested_weight is not None:
            weight_kg = _as_float(suggested_weight.get("weight_kg"))
            if weight_kg > 0:
                written_weight = self.add_weight_log(
                    scope,
                    weight_kg=weight_kg,
                    source=str(suggested_weight.get("source", "user") or "user"),
                    metadata={"raw_text": str(suggested_weight.get("raw_text", "")), "source": "diet_inbox_commit"},
                )

        written_profile = None
        skipped = []
        safe_profile_updates = dict(profile_updates)
        memory_like_preferences = safe_profile_updates.pop("diet_preferences", None)
        if commit_profile and safe_profile_updates:
            written_profile = self.update_profile(scope, **safe_profile_updates)
        if memory_like_preferences:
            skipped.append(
                {
                    "type": "diet_preferences",
                    "reason": "长期饮食偏好需要用户单独确认后再调用 profile_update 或 memory_write。",
                    "candidate": memory_like_preferences,
                }
            )

        return {
            "generated_at": _now(),
            "user_scope": scope,
            "type": "diet_inbox_commit",
            "written_meals": written_meals,
            "written_weight": written_weight,
            "written_profile": written_profile,
            "skipped": skipped,
            "source": {
                "suggested_meal_count": len(suggested_meals),
                "committed_meal_count": len(written_meals),
                "has_weight": suggested_weight is not None,
                "committed_weight": written_weight is not None,
                "profile_update_keys": sorted(safe_profile_updates),
                "has_preference_candidate": bool(memory_like_preferences),
            },
            "note": "这是基于已确认饮食整理结果的批量写入；长期偏好和记忆不会自动写入。",
        }

    def summarize_day(self, user_scope: str, *, date: str = "") -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        target_date = date or _today()
        meals = self.list_meal_logs(scope, meal_date=target_date, limit=100)
        totals = {
            "actual_calories": sum(_as_float(row.get("estimated_calories")) for row in meals),
            "protein_g": sum(_as_float(row.get("protein_g")) for row in meals),
            "carbs_g": sum(_as_float(row.get("carbs_g")) for row in meals),
            "fat_g": sum(_as_float(row.get("fat_g")) for row in meals),
        }
        meal_types = {str(row.get("meal_type", "")) for row in meals}
        missing = [name for name in ("breakfast", "lunch", "dinner") if name not in meal_types]
        profile = self.get_profile(scope) or {}
        target_calories = self._target_calories(profile)
        summary_text = self._summary_text(target_date, totals, missing, target_calories)
        now = _now()
        row = {
            "id": f"{scope}:{target_date}",
            "user_scope": scope,
            "date": target_date,
            "target_calories": target_calories,
            **totals,
            "summary_text": summary_text,
            "risk_flags": ["missing_meals"] if missing else [],
            "created_at": now,
            "updated_at": now,
            "metadata": {"missing_meals": missing, "meal_count": len(meals)},
        }
        self._upsert("daily_nutrition_summaries", row)
        return row

    def get_day_summary(self, user_scope: str, *, date: str = "") -> dict[str, Any] | None:
        """读取某天已生成的饮食汇总，不存在时返回 None。"""

        scope = self._require_scope(user_scope)
        target_date = date or _today()
        rows = self._list(
            "daily_nutrition_summaries",
            filters={"user_scope": scope, "date": target_date},
            limit=1,
        )
        return rows[0] if rows else None

    def generate_plan(self, user_scope: str, *, plan_date: str = "") -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        target_date = plan_date or _today()
        profile = self.get_profile(scope) or {}
        target_calories = self._target_calories(profile)
        recent_meals = self._list("meal_logs", filters={"user_scope": scope}, limit=200)
        adjustment = self._plan_adjustment(recent_meals)
        meals = {
            "breakfast": [
                "无糖酸奶 + 鸡蛋 + 一份水果",
                "燕麦 + 牛奶/豆浆 + 鸡蛋",
            ],
            "lunch": [
                "一掌蛋白质 + 一拳主食 + 两拳蔬菜",
                "外卖优先选盖饭少饭、多蔬菜、少酱汁",
            ],
            "dinner": [
                "清淡蛋白质 + 大量蔬菜 + 半拳主食",
                "如果午餐偏油，晚餐减少油脂和甜饮",
            ],
            "snack": [
                "无糖咖啡、茶、低糖水果或一小把坚果",
            ],
        }
        if adjustment["breakfast_simple"]:
            meals["breakfast"] = [
                "固定早餐：无糖酸奶/豆浆 + 鸡蛋 + 一份水果",
                "赶时间时选择便利店鸡蛋 + 无糖豆浆，先保证不断档",
            ]
        if adjustment["protein_focus"]:
            meals["lunch"] = [
                "优先选双蛋白：鸡胸/牛肉/鱼虾/豆腐 + 两拳蔬菜 + 一拳主食",
                "外卖备注少油少酱，主菜优先蛋白质，不用靠少吃主食硬扛",
            ]
            meals["snack"] = [
                "无糖酸奶、牛奶、豆浆或茶叶蛋，优先补蛋白质",
            ]
        if adjustment["lighter_dinner"]:
            meals["dinner"] = [
                "清蒸/水煮蛋白质 + 两拳蔬菜 + 半拳粗粮主食",
                "外卖优先选轻食/汤粉少粉/盖饭半饭，避开油炸、奶茶和重酱汁",
            ]
        row = {
            "id": f"{scope}:{target_date}",
            "user_scope": scope,
            "plan_date": target_date,
            "target_calories": target_calories,
            "meals": meals,
            "shopping_tips": "优先准备鸡蛋、酸奶、豆浆、鸡胸/牛肉、绿叶菜和低糖水果。",
            "generated_reason": adjustment["reason"]
            or "基于当前档案和保守减脂原则生成。若档案不完整，请先补充身高、体重、目标和忌口。",
            "status": "active",
            "created_at": _now(),
            "metadata": {
                "profile_complete": self._profile_complete(profile),
                "adjustment": adjustment,
            },
        }
        self._upsert("diet_plans", row)
        return row

    def get_plan(self, user_scope: str, *, plan_date: str = "") -> dict[str, Any] | None:
        """读取某天已生成的饮食计划，不存在时返回 None。"""

        scope = self._require_scope(user_scope)
        target_date = plan_date or _today()
        rows = self._list(
            "diet_plans",
            filters={"user_scope": scope, "plan_date": target_date},
            limit=1,
        )
        return rows[0] if rows else None

    def progress_summary(self, user_scope: str, *, days: int = 7) -> dict[str, Any]:
        scope = self._require_scope(user_scope)
        safe_days = max(1, min(int(days or 7), 30))
        weights = self._list("weight_logs", filters={"user_scope": scope}, limit=200)
        meals = self._list("meal_logs", filters={"user_scope": scope}, limit=500)
        weights.sort(key=lambda row: _as_float(row.get("recorded_at")), reverse=True)
        meals.sort(key=lambda row: _as_float(row.get("logged_at")), reverse=True)
        daily = self._daily_progress(meals, days=safe_days)
        weight_change = 0.0
        if len(weights) >= 2:
            weight_change = _as_float(weights[0].get("weight_kg")) - _as_float(weights[min(safe_days - 1, len(weights) - 1)].get("weight_kg"))
        total_days = max(1, len(daily))
        average_calories = sum(_as_float(row.get("calories")) for row in daily) / total_days
        average_protein = sum(_as_float(row.get("protein_g")) for row in daily) / total_days
        missing_meal_days = sum(1 for row in daily if row.get("missing_meals"))
        return {
            "user_scope": scope,
            "days": safe_days,
            "weight_logs": weights[:safe_days],
            "weight_change_kg": round(weight_change, 2),
            "meal_count": len(meals),
            "recent_meals": meals[: min(10, len(meals))],
            "daily": daily,
            "average_calories": round(average_calories, 1),
            "average_protein_g": round(average_protein, 1),
            "missing_meal_days": missing_meal_days,
        }

    def coach_briefing(self, user_scope: str, *, days: int = 7) -> dict[str, Any]:
        """生成适合聊天回复使用的饮食趋势简报。"""

        scope = self._require_scope(user_scope)
        progress = self.progress_summary(scope, days=days)
        profile = self.get_profile(scope) or {}
        target_calories = self._target_calories(profile)
        risks = self._coach_risk_flags(progress, target_calories)
        highlights = self._coach_highlights(progress)
        actions = self._coach_actions(progress, risks, profile)
        return {
            "user_scope": scope,
            "days": progress["days"],
            "target_calories": target_calories,
            "weight_change_kg": progress["weight_change_kg"],
            "average_calories": progress["average_calories"],
            "average_protein_g": progress["average_protein_g"],
            "missing_meal_days": progress["missing_meal_days"],
            "meal_count": progress["meal_count"],
            "risk_flags": risks,
            "highlights": highlights,
            "suggested_actions": actions,
            "recent_daily": progress["daily"][: min(5, len(progress["daily"]))],
            "latest_weight": progress["weight_logs"][0] if progress["weight_logs"] else None,
        }

    def today_status(self, user_scope: str, *, date: str = "") -> dict[str, Any]:
        """汇总某个用户当天饮食状态，供 Dashboard 和个人状态卡使用。"""

        scope = self._require_scope(user_scope)
        target_date = date or _today()
        profile = self.get_profile(scope) or {}
        meals = self.list_meal_logs(scope, meal_date=target_date, limit=100)
        summary = self.get_day_summary(scope, date=target_date) or self._preview_day_summary(
            scope,
            target_date,
            meals,
            profile,
        )
        plan = self.get_plan(scope, plan_date=target_date)
        weights = self._list("weight_logs", filters={"user_scope": scope}, limit=200)
        weights.sort(key=lambda row: _as_float(row.get("recorded_at")), reverse=True)
        latest_weight = weights[0] if weights else None
        missing = list(summary.get("metadata", {}).get("missing_meals", [])) if isinstance(summary.get("metadata"), dict) else []
        return {
            "user_scope": scope,
            "date": target_date,
            "profile_complete": self._profile_complete(profile),
            "target_calories": summary.get("target_calories", self._target_calories(profile)),
            "actual_calories": summary.get("actual_calories", 0.0),
            "protein_g": summary.get("protein_g", 0.0),
            "carbs_g": summary.get("carbs_g", 0.0),
            "fat_g": summary.get("fat_g", 0.0),
            "missing_meals": missing,
            "meal_count": len(meals),
            "latest_weight": latest_weight,
            "plan": plan,
            "trend_7d": self.progress_summary(scope, days=7),
            "risk_flags": self._today_risk_flags(summary, missing, profile),
        }

    def daily_loop(self, user_scope: str, *, date: str = "", days: int = 7) -> dict[str, Any]:
        """生成面向聊天场景的每日饮食执行闭环。

        只聚合已有事实，不自动生成计划，避免用户查询状态时产生隐式写入。
        """

        scope = self._require_scope(user_scope)
        safe_days = max(1, min(int(days or 7), 30))
        status = self.today_status(scope, date=date)
        briefing = self.coach_briefing(scope, days=safe_days)
        plan = status.get("plan") if isinstance(status.get("plan"), dict) else None
        missing_meals = list(status.get("missing_meals", []))
        risk_flags = sorted(
            set(
                [
                    *[str(flag) for flag in status.get("risk_flags", [])],
                    *[str(flag) for flag in briefing.get("risk_flags", [])],
                ]
            )
        )
        return {
            "type": "diet_daily_loop",
            "user_scope": scope,
            "date": status["date"],
            "profile_complete": bool(status.get("profile_complete")),
            "target_calories": status.get("target_calories", 0.0),
            "actual_calories": status.get("actual_calories", 0.0),
            "protein_g": status.get("protein_g", 0.0),
            "carbs_g": status.get("carbs_g", 0.0),
            "fat_g": status.get("fat_g", 0.0),
            "meal_count": status.get("meal_count", 0),
            "missing_meals": missing_meals,
            "latest_weight": status.get("latest_weight"),
            "plan_status": "available" if plan else "missing",
            "plan": plan,
            "trend": {
                "days": briefing.get("days", safe_days),
                "weight_change_kg": briefing.get("weight_change_kg", 0.0),
                "average_calories": briefing.get("average_calories", 0.0),
                "average_protein_g": briefing.get("average_protein_g", 0.0),
                "missing_meal_days": briefing.get("missing_meal_days", 0),
            },
            "risk_flags": risk_flags,
            "highlights": briefing.get("highlights", []),
            "next_actions": self._daily_loop_actions(
                status=status,
                briefing=briefing,
                plan_available=plan is not None,
            ),
            "reminders": self._daily_loop_reminders(
                missing_meals,
                plan_available=plan is not None,
            ),
            "note": "每日闭环只汇总已记录的数据；如果缺少计划或餐食，请先补齐后再复盘。",
        }

    def generate_next_meal_card(
        self,
        user_scope: str,
        *,
        date: str = "",
        meal_type: str = "",
    ) -> dict[str, Any]:
        """生成下一餐建议卡片，不自动写入餐食或计划。"""

        scope = self._require_scope(user_scope)
        status = self.today_status(scope, date=date)
        target_date = str(status.get("date") or date or _today())
        missing_meals = [str(item) for item in status.get("missing_meals", [])]
        selected_meal = self._select_next_meal(meal_type, missing_meals)
        plan = status.get("plan") if isinstance(status.get("plan"), dict) else {}
        plan_meals = plan.get("meals") if isinstance(plan.get("meals"), dict) else {}
        planned_options = _as_list(plan_meals.get(selected_meal))
        target = _as_float(status.get("target_calories"))
        actual = _as_float(status.get("actual_calories"))
        protein = _as_float(status.get("protein_g"))
        remaining = round(target - actual, 1)
        risk_flags = [str(flag) for flag in status.get("risk_flags", [])]
        guardrails = self._next_meal_guardrails(
            selected_meal=selected_meal,
            remaining_calories=remaining,
            protein_g=protein,
            risk_flags=risk_flags,
            plan_available=bool(plan),
        )
        recommended_options = [str(item).strip() for item in planned_options if str(item).strip()]
        if not recommended_options:
            recommended_options = self._fallback_meal_options(selected_meal, guardrails)
        return {
            "type": "diet_next_meal_card",
            "user_scope": scope,
            "date": target_date,
            "next_meal": selected_meal,
            "next_meal_label": self._meal_label(selected_meal),
            "target_calories": target,
            "actual_calories": actual,
            "remaining_calories": remaining,
            "protein_g": protein,
            "plan_status": "available" if plan else "missing",
            "recommended_options": recommended_options[:3],
            "guardrails": guardrails,
            "first_action": self._next_meal_first_action(selected_meal, recommended_options),
            "reminders": self._next_meal_reminders(
                selected_meal=selected_meal,
                missing_meals=missing_meals,
                plan_available=bool(plan),
            ),
            "note": "这是下一餐建议卡片，只读取已有餐食、计划和体重信息，不会自动写入餐食或生成新计划。",
        }

    def generate_day_review_plan(self, user_scope: str, *, date: str = "", days: int = 7) -> dict[str, Any]:
        """生成饮食日总结和明日建议草稿，不自动写入计划或记录。"""

        scope = self._require_scope(user_scope)
        safe_days = max(1, min(int(days or 7), 30))
        status = self.today_status(scope, date=date)
        briefing = self.coach_briefing(scope, days=safe_days)
        target = _as_float(status.get("target_calories"))
        actual = _as_float(status.get("actual_calories"))
        protein = _as_float(status.get("protein_g"))
        delta = round(actual - target, 1)
        missing_meals = list(status.get("missing_meals", []))
        risk_flags = sorted(
            set(
                [
                    *[str(flag) for flag in status.get("risk_flags", [])],
                    *[str(flag) for flag in briefing.get("risk_flags", [])],
                ]
            )
        )
        tomorrow_strategy = self._tomorrow_strategy(
            risk_flags=risk_flags,
            missing_meals=missing_meals,
            briefing=briefing,
        )
        confirmations = []
        if missing_meals:
            confirmations.append(f"是否需要补记缺失餐次：{'、'.join(missing_meals)}？")
        if not status.get("latest_weight"):
            confirmations.append("是否补记一次今日或明早体重？")
        if not status.get("plan"):
            confirmations.append("是否生成明日饮食计划？")

        return {
            "type": "diet_day_review_plan",
            "user_scope": scope,
            "date": status["date"],
            "review": {
                "target_calories": target,
                "actual_calories": actual,
                "calorie_delta": delta,
                "protein_g": protein,
                "meal_count": status.get("meal_count", 0),
                "missing_meals": missing_meals,
                "summary": self._diet_day_review_summary(
                    actual=actual,
                    target=target,
                    protein=protein,
                    missing_meals=missing_meals,
                ),
            },
            "trend": {
                "days": briefing.get("days", safe_days),
                "weight_change_kg": briefing.get("weight_change_kg", 0.0),
                "average_calories": briefing.get("average_calories", 0.0),
                "average_protein_g": briefing.get("average_protein_g", 0.0),
                "missing_meal_days": briefing.get("missing_meal_days", 0),
            },
            "risk_flags": risk_flags,
            "tomorrow_strategy": tomorrow_strategy,
            "needs_confirmation": confirmations,
            "next_actions": [
                "确认后可调用 nutrition_day_summary 保存今日汇总。",
                "如需要明日计划，确认后调用 diet_plan_generate。",
                "如需要补体重，确认后调用 weight_log_add。",
            ],
            "note": "这是饮食日总结和明日建议草稿，不会自动生成计划、写入体重或补记餐食。",
        }

    def generate_weekly_plan(
        self,
        user_scope: str,
        *,
        week_goal: str = "",
        days: int = 7,
        focus_areas: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        """生成饮食周计划草稿，不自动写入每日计划或体重记录。"""

        scope = self._require_scope(user_scope)
        safe_days = max(1, min(int(days or 7), 30))
        profile = self.get_profile(scope) or {}
        briefing = self.coach_briefing(scope, days=safe_days)
        progress = self.progress_summary(scope, days=safe_days)
        target_calories = self._target_calories(profile)
        risk_flags = list(briefing.get("risk_flags", []))
        focus_items = [str(item).strip() for item in (focus_areas or []) if str(item).strip()]
        constraint_items = [str(item).strip() for item in (constraints or []) if str(item).strip()]

        if not focus_items:
            if "meal_logging_incomplete" in risk_flags:
                focus_items.append("先把三餐记录闭环做稳定。")
            if "protein_low" in risk_flags:
                focus_items.append("每餐优先保证一掌蛋白质。")
            if "calories_over_target" in risk_flags:
                focus_items.append("控制晚餐油脂、重酱汁和含糖饮料。")
            if "calories_too_low" in risk_flags:
                focus_items.append("避免极端低热量，先补足蛋白质和蔬菜。")
        if not focus_items:
            focus_items.append("保持当前记录节奏，稳定执行三餐。")

        weekly_actions = []
        weekly_actions.extend(str(item).strip() for item in briefing.get("suggested_actions", []) if str(item).strip())
        for item in focus_items:
            action = f"围绕「{item}」设置一个可执行动作。"
            if action not in weekly_actions:
                weekly_actions.append(action)
        if not weekly_actions:
            weekly_actions.append("每天晚餐后完成一次简短饮食记录闭环。")

        daily_guidelines = [
            f"每日目标热量参考约 {target_calories:.0f} kcal，按实际饥饿感和运动量微调。",
            "早餐固定一个容易执行的组合，降低漏记和漏吃概率。",
            "午餐保留主食，但优先选择清晰可估算的蛋白质来源。",
            "晚餐减少油炸、重酱汁和含糖饮料，优先清淡蛋白质和蔬菜。",
        ]
        if "protein_low" in risk_flags:
            daily_guidelines.insert(1, "每餐先确认蛋白质来源，再考虑主食和零食。")
        if "meal_logging_incomplete" in risk_flags:
            daily_guidelines.insert(0, "本周第一目标是连续记录早餐、午餐、晚餐。")

        confirmations = []
        if not self._profile_complete(profile):
            confirmations.append("是否补充身高、当前体重、目标体重和活动水平？")
        if not week_goal.strip():
            confirmations.append("本周饮食最重要目标是什么？")
        if constraint_items:
            confirmations.append("这些限制是否需要转成具体避坑规则？")
        if not progress.get("weight_logs"):
            confirmations.append("是否本周固定 2 到 3 次晨起体重记录？")

        return {
            "type": "diet_weekly_plan",
            "user_scope": scope,
            "days": safe_days,
            "week_goal": week_goal.strip() or "稳定记录三餐，按趋势小幅调整饮食。",
            "target_calories": target_calories,
            "focus_areas": focus_items[:5],
            "trend": {
                "weight_change_kg": briefing.get("weight_change_kg", 0.0),
                "average_calories": briefing.get("average_calories", 0.0),
                "average_protein_g": briefing.get("average_protein_g", 0.0),
                "missing_meal_days": briefing.get("missing_meal_days", 0),
                "meal_count": briefing.get("meal_count", 0),
            },
            "risk_flags": risk_flags,
            "weekly_actions": weekly_actions[:6],
            "daily_guidelines": daily_guidelines[:6],
            "constraints": constraint_items,
            "needs_confirmation": confirmations,
            "next_actions": [
                "确认后可按某一天调用 diet_plan_generate 生成具体日计划。",
                "执行中继续用 meal_log_add 记录餐食，用 weight_log_add 记录体重。",
                "周末可用 diet_coach_briefing 或 diet_day_review_plan_generate 做复盘。",
            ],
            "note": "这是饮食周计划草稿，不会自动生成每日计划、写入体重或补记餐食。",
        }

    def _target_calories(self, profile: dict[str, Any]) -> float:
        current = _as_float(profile.get("current_weight_kg"))
        target = _as_float(profile.get("target_weight_kg"))
        if current <= 0:
            return 1800.0
        base = current * 24
        if target and target < current:
            base -= 400
        return max(1200.0, min(2600.0, round(base / 50) * 50))

    @staticmethod
    def _profile_complete(profile: dict[str, Any]) -> bool:
        return bool(
            profile.get("height_cm")
            and profile.get("current_weight_kg")
            and profile.get("target_weight_kg")
        )

    @staticmethod
    def _summary_text(date: str, totals: dict[str, float], missing: list[str], target: float) -> str:
        delta = totals["actual_calories"] - target
        status = "低于目标" if delta < 0 else "高于目标"
        missing_text = "、".join(missing) if missing else "无"
        return (
            f"{date} 已记录摄入约 {totals['actual_calories']:.0f} kcal，"
            f"目标约 {target:.0f} kcal，{status} {abs(delta):.0f} kcal。"
            f"缺失餐次：{missing_text}。"
        )

    def _preview_day_summary(
        self,
        user_scope: str,
        target_date: str,
        meals: list[dict[str, Any]],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """不落库地计算当天汇总，用于 Dashboard 实时状态。"""

        totals = {
            "actual_calories": sum(_as_float(row.get("estimated_calories")) for row in meals),
            "protein_g": sum(_as_float(row.get("protein_g")) for row in meals),
            "carbs_g": sum(_as_float(row.get("carbs_g")) for row in meals),
            "fat_g": sum(_as_float(row.get("fat_g")) for row in meals),
        }
        meal_types = {str(row.get("meal_type", "")) for row in meals}
        missing = [name for name in ("breakfast", "lunch", "dinner") if name not in meal_types]
        target_calories = self._target_calories(profile)
        return {
            "user_scope": user_scope,
            "date": target_date,
            "target_calories": target_calories,
            **totals,
            "summary_text": self._summary_text(target_date, totals, missing, target_calories),
            "risk_flags": ["missing_meals"] if missing else [],
            "metadata": {"missing_meals": missing, "meal_count": len(meals), "preview": True},
        }

    @staticmethod
    def _daily_progress(meals: list[dict[str, Any]], *, days: int) -> list[dict[str, Any]]:
        """按日期聚合最近若干天餐食，用于 7/30 天趋势摘要。"""

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in meals:
            meal_date = str(row.get("meal_date", "")).strip()
            if not meal_date:
                continue
            grouped.setdefault(meal_date, []).append(row)
        result: list[dict[str, Any]] = []
        for meal_date in sorted(grouped.keys(), reverse=True)[:days]:
            rows = grouped[meal_date]
            meal_types = {str(row.get("meal_type", "")) for row in rows}
            missing = [name for name in ("breakfast", "lunch", "dinner") if name not in meal_types]
            result.append(
                {
                    "date": meal_date,
                    "calories": sum(_as_float(row.get("estimated_calories")) for row in rows),
                    "protein_g": sum(_as_float(row.get("protein_g")) for row in rows),
                    "carbs_g": sum(_as_float(row.get("carbs_g")) for row in rows),
                    "fat_g": sum(_as_float(row.get("fat_g")) for row in rows),
                    "meal_count": len(rows),
                    "missing_meals": missing,
                }
            )
        return result

    @staticmethod
    def _plan_adjustment(recent_meals: list[dict[str, Any]]) -> dict[str, Any]:
        """根据近期记录给第二天计划做保守调整。"""

        dinners = [
            row
            for row in recent_meals
            if str(row.get("meal_type", "")).strip() == "dinner"
        ]
        dinners.sort(key=lambda row: str(row.get("meal_date", "")), reverse=True)
        recent_dinners = dinners[:7]
        high_dinners = [
            row for row in recent_dinners if _as_float(row.get("estimated_calories")) >= 700
        ]
        breakfasts = [
            row
            for row in recent_meals
            if str(row.get("meal_type", "")).strip() == "breakfast"
        ]
        proteins = [_as_float(row.get("protein_g")) for row in recent_meals if _as_float(row.get("protein_g")) > 0]
        avg_protein = sum(proteins) / len(proteins) if proteins else 0.0
        lighter_dinner = len(high_dinners) >= 3
        protein_focus = bool(proteins and avg_protein < 25)
        breakfast_simple = len(breakfasts) <= 2 and len(recent_meals) >= 6
        reasons: list[str] = []
        if lighter_dinner:
            reasons.append(
                f"最近 {len(recent_dinners)} 次晚餐中有 {len(high_dinners)} 次估算热量偏高，"
                "今日计划已自动降低晚餐油脂和主食强度"
            )
        if protein_focus:
            reasons.append(f"近期单餐蛋白质均值约 {avg_protein:.0f}g，今日计划已提高蛋白质优先级")
        if breakfast_simple:
            reasons.append("近期早餐记录偏少，今日早餐建议改为更容易执行的固定组合")
        return {
            "lighter_dinner": lighter_dinner,
            "protein_focus": protein_focus,
            "breakfast_simple": breakfast_simple,
            "recent_dinner_count": len(recent_dinners),
            "high_dinner_count": len(high_dinners),
            "average_protein_g": round(avg_protein, 1),
            "breakfast_count": len(breakfasts),
            "reason": "；".join(reasons) + ("。" if reasons else ""),
        }

    @staticmethod
    def _today_risk_flags(
        summary: dict[str, Any],
        missing: list[str],
        profile: dict[str, Any],
    ) -> list[str]:
        flags = list(summary.get("risk_flags", []) if isinstance(summary.get("risk_flags"), list) else [])
        target = _as_float(summary.get("target_calories"))
        actual = _as_float(summary.get("actual_calories"))
        if target > 0 and actual > target + 300:
            flags.append("calories_over_target")
        current_weight = _as_float(profile.get("current_weight_kg"))
        protein = _as_float(summary.get("protein_g"))
        if current_weight > 0 and protein and protein < current_weight * 0.8:
            flags.append("protein_low")
        if missing:
            flags.append("missing_meals")
        return sorted(set(flags))

    @staticmethod
    def _coach_risk_flags(progress: dict[str, Any], target_calories: float) -> list[str]:
        flags: list[str] = []
        average_calories = _as_float(progress.get("average_calories"))
        average_protein = _as_float(progress.get("average_protein_g"))
        missing_meal_days = _as_int(progress.get("missing_meal_days"))
        days = _as_int(progress.get("days"), 1)
        if missing_meal_days >= max(2, days // 3):
            flags.append("meal_logging_incomplete")
        if target_calories and average_calories > target_calories + 300:
            flags.append("calories_over_target")
        if target_calories and 0 < average_calories < target_calories - 600:
            flags.append("calories_too_low")
        if average_protein and average_protein < 60:
            flags.append("protein_low")
        return flags

    @staticmethod
    def _coach_highlights(progress: dict[str, Any]) -> list[str]:
        highlights: list[str] = []
        weight_change = _as_float(progress.get("weight_change_kg"))
        meal_count = _as_int(progress.get("meal_count"))
        if weight_change < 0:
            highlights.append(f"体重较最近记录下降 {abs(weight_change):.1f} kg。")
        elif weight_change > 0:
            highlights.append(f"体重较最近记录上升 {weight_change:.1f} kg，需要观察是否是短期波动。")
        if meal_count:
            highlights.append(f"最近共记录 {meal_count} 餐，已有基础数据可用于调整。")
        if not highlights:
            highlights.append("当前记录偏少，先把餐食和体重记录稳定下来。")
        return highlights

    @staticmethod
    def _coach_actions(
        progress: dict[str, Any],
        risks: list[str],
        profile: dict[str, Any],
    ) -> list[str]:
        actions: list[str] = []
        if "meal_logging_incomplete" in risks:
            actions.append("先补齐早餐、午餐、晚餐三餐记录，至少连续记录 3 天。")
        if "protein_low" in risks:
            actions.append("每餐优先保证一掌蛋白质，例如鸡蛋、牛肉、鱼虾、鸡胸或豆制品。")
        if "calories_over_target" in risks:
            actions.append("下一餐减少油炸、重酱汁和含糖饮料，主食先减到半拳到一拳。")
        if "calories_too_low" in risks:
            actions.append("不要继续极端压低热量，优先补足蛋白质和蔬菜，避免反弹。")
        if not DietStore._profile_complete(profile):
            actions.append("补充身高、当前体重、目标体重和活动水平，让建议更准确。")
        if not actions:
            actions.append("保持当前记录节奏，下一步关注晚餐油脂和每日蛋白质是否稳定。")
        return actions[:5]

    @staticmethod
    def _daily_loop_actions(
        *,
        status: dict[str, Any],
        briefing: dict[str, Any],
        plan_available: bool,
    ) -> list[str]:
        actions: list[str] = []
        if not status.get("profile_complete"):
            actions.append("先补充身高、当前体重、目标体重和活动水平。")
        if not plan_available:
            actions.append("生成今日饮食计划，再按计划执行三餐。")
        missing_meals = list(status.get("missing_meals", []))
        if missing_meals:
            actions.append(f"补记缺失餐次：{'、'.join(missing_meals)}。")
        risks = set(str(flag) for flag in briefing.get("risk_flags", []))
        if "protein_low" in risks:
            actions.append("下一餐优先补足一掌蛋白质。")
        if "calories_over_target" in risks:
            actions.append("下一餐减少油炸、重酱汁和含糖饮料。")
        if "calories_too_low" in risks:
            actions.append("不要继续极端压低热量，先补足蛋白质和蔬菜。")
        if not actions:
            actions.append("按当前计划执行，晚间做一次营养总结。")
        return actions[:5]

    @staticmethod
    def _daily_loop_reminders(missing_meals: list[str], *, plan_available: bool) -> list[str]:
        reminders: list[str] = []
        if not plan_available:
            reminders.append("今日计划缺失")
        if missing_meals:
            reminders.append("餐食记录未闭环")
        if not reminders:
            reminders.append("今日记录闭环正常")
        return reminders

    @staticmethod
    def _select_next_meal(meal_type: str, missing_meals: list[str]) -> str:
        normalized = str(meal_type or "").strip().lower()
        aliases = {
            "早饭": "breakfast",
            "早餐": "breakfast",
            "午饭": "lunch",
            "午餐": "lunch",
            "晚饭": "dinner",
            "晚餐": "dinner",
            "加餐": "snack",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"breakfast", "lunch", "dinner", "snack"}:
            return normalized
        for candidate in ("breakfast", "lunch", "dinner"):
            if candidate in missing_meals:
                return candidate
        return "snack"

    @staticmethod
    def _meal_label(meal_type: str) -> str:
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "snack": "加餐",
        }.get(meal_type, meal_type or "下一餐")

    @staticmethod
    def _next_meal_guardrails(
        *,
        selected_meal: str,
        remaining_calories: float,
        protein_g: float,
        risk_flags: list[str],
        plan_available: bool,
    ) -> list[str]:
        guardrails: list[str] = []
        if not plan_available:
            guardrails.append("今日饮食计划缺失，建议先生成计划或按保守模板执行。")
        if remaining_calories < 300 and selected_meal != "snack":
            guardrails.append("今日剩余热量不多，下一餐优先清淡蛋白质和蔬菜，主食减半。")
        elif remaining_calories > 800:
            guardrails.append("今日摄入偏少，不要极端节食，下一餐补足蛋白质和基础主食。")
        if "protein_low" in risk_flags or protein_g < 50:
            guardrails.append("蛋白质偏低，下一餐优先补一掌蛋白质。")
        if "calories_over_target" in risk_flags:
            guardrails.append("今日热量已偏高，避开油炸、重酱汁和含糖饮料。")
        if not guardrails:
            guardrails.append("按计划执行，保持少油、足量蛋白质和蔬菜。")
        return guardrails[:4]

    @staticmethod
    def _fallback_meal_options(meal_type: str, guardrails: list[str]) -> list[str]:
        if meal_type == "breakfast":
            return ["无糖豆浆/酸奶 + 鸡蛋 + 一份水果"]
        if meal_type == "lunch":
            return ["一掌蛋白质 + 一拳主食 + 两拳蔬菜，少油少酱"]
        if meal_type == "dinner":
            return ["清淡蛋白质 + 两拳蔬菜 + 半拳主食"]
        if any("蛋白质" in item for item in guardrails):
            return ["无糖酸奶、牛奶、豆浆或茶叶蛋，优先补蛋白质"]
        return ["无糖饮品、低糖水果或一小把坚果"]

    @staticmethod
    def _next_meal_first_action(meal_type: str, options: list[str]) -> str:
        label = DietStore._meal_label(meal_type)
        option = str(options[0]).strip() if options else "按清淡高蛋白模板选择"
        return f"下一餐按「{label}」处理：{option}。"

    @staticmethod
    def _next_meal_reminders(
        *,
        selected_meal: str,
        missing_meals: list[str],
        plan_available: bool,
    ) -> list[str]:
        reminders: list[str] = []
        if selected_meal in missing_meals:
            reminders.append(f"{DietStore._meal_label(selected_meal)}还未记录，吃完后补记。")
        if not plan_available:
            reminders.append("今日计划缺失，建议确认是否需要生成 diet_plan。")
        if not reminders:
            reminders.append("吃完后记录餐食，晚间再做一次闭环总结。")
        return reminders

    @staticmethod
    def _diet_day_review_summary(
        *,
        actual: float,
        target: float,
        protein: float,
        missing_meals: list[str],
    ) -> str:
        if actual <= 0:
            return "今日餐食记录不足，无法判断摄入是否贴近目标。"
        delta = actual - target
        if abs(delta) <= 150:
            calorie_text = "热量基本贴近目标"
        elif delta > 0:
            calorie_text = f"热量高于目标约 {delta:.0f} kcal"
        else:
            calorie_text = f"热量低于目标约 {abs(delta):.0f} kcal"
        protein_text = f"蛋白质约 {protein:.0f}g" if protein > 0 else "蛋白质记录不足"
        missing_text = "，缺失餐次：" + "、".join(missing_meals) if missing_meals else "，三餐记录基本闭环"
        return f"{calorie_text}，{protein_text}{missing_text}。"

    @staticmethod
    def _tomorrow_strategy(
        *,
        risk_flags: list[str],
        missing_meals: list[str],
        briefing: dict[str, Any],
    ) -> dict[str, Any]:
        focus = "保持记录闭环，优先稳定三餐和蛋白质。"
        actions: list[str] = []
        if "missing_meals" in risk_flags or missing_meals:
            actions.append("明天先把早餐、午餐、晚餐都记录下来，不追求估算完美。")
        if "protein_low" in risk_flags:
            focus = "明天优先补足蛋白质。"
            actions.append("每餐安排一掌蛋白质：鸡蛋、牛肉、鱼虾、鸡胸、豆腐或无糖酸奶。")
        if "calories_over_target" in risk_flags:
            focus = "明天控制晚餐油脂和含糖饮料。"
            actions.append("晚餐减少油炸、重酱汁和奶茶，主食控制在半拳到一拳。")
        if "calories_too_low" in risk_flags:
            focus = "明天避免极端低热量。"
            actions.append("不要继续硬饿，优先补足蛋白质、蔬菜和适量主食。")
        for action in briefing.get("suggested_actions", []):
            text = str(action).strip()
            if text and text not in actions:
                actions.append(text)
        if not actions:
            actions.append("延续当前节奏，晚餐后做一次简短复盘。")
        return {
            "focus": focus,
            "actions": actions[:5],
            "breakfast_hint": "固定早餐：鸡蛋 + 无糖豆浆/酸奶 + 一份水果。",
            "dinner_hint": "晚餐优先清淡蛋白质 + 两拳蔬菜 + 半拳主食。",
        }

    def _backend_get(self, table: str, key: str) -> dict[str, Any] | None:
        backend = self.read_backend
        if backend is None or not hasattr(backend, "get"):
            return None
        try:
            row = backend.get(table, key)
        except Exception:
            return None
        return row if isinstance(row, dict) else None

    def _upsert(self, table: str, row: dict[str, Any]) -> None:
        if table not in DIET_TABLES:
            raise ValueError(f"unsupported diet table: {table}")
        backend = self.write_backend
        if backend is not None and getattr(backend, "enabled", False) and hasattr(backend, "upsert"):
            try:
                backend.upsert(table, row)
            except Exception:
                self._write_local(table, row)
            else:
                self._write_local(table, row)
            return
        self._write_local(table, row)

    def _list(self, table: str, *, filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        backend = self.read_backend
        if backend is not None and hasattr(backend, "list"):
            try:
                rows = backend.list(table, limit=limit, filters=filters)
                if rows:
                    return [row for row in rows if isinstance(row, dict)]
            except Exception:
                pass
        return self._local_rows(table, **filters)[:limit]

    def _write_local(self, table: str, row: dict[str, Any]) -> None:
        path = self._local_path(table, self._normalize_scope(str(row.get("user_scope", ""))))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _local_rows(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        user_scope = self._normalize_scope(str(filters.get("user_scope", "")))
        paths = [self._local_path(table, user_scope)] if user_scope else list(self._local_root(table).glob("*/data.jsonl"))
        rows: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if all(not value or str(row.get(key, "")) == str(value) for key, value in filters.items()):
                    rows.append(row)
        return rows

    def _local_root(self, table: str) -> Path:
        return self.workspace_root / "diet" / table

    def _local_path(self, table: str, user_scope: str) -> Path:
        return self._local_root(table) / _scope_slug(user_scope) / "data.jsonl"

    @staticmethod
    def _normalize_scope(user_scope: str) -> str:
        return " ".join(str(user_scope or "").strip().split())

    def _require_scope(self, user_scope: str) -> str:
        scope = self._normalize_scope(user_scope)
        if not scope:
            raise ValueError("diet tools require user_scope")
        return scope

    @staticmethod
    def _split_diet_fragments(text: str) -> list[str]:
        raw_parts: list[str] = []
        for line in text.replace("；", "\n").replace("。", "\n").replace("，", "\n").splitlines():
            raw_parts.extend(line.split(";"))
        return [part.strip(" -\t") for part in raw_parts if part.strip(" -\t")]

    @classmethod
    def _suggest_meal_from_fragment(cls, fragment: str) -> dict[str, Any] | None:
        if any(word in fragment for word in ["不吃", "忌口", "过敏", "喜欢", "偏好"]):
            return None
        meal_type = cls._infer_meal_type(fragment)
        if meal_type == "unknown" and not any(
            word in fragment
            for word in ["吃", "喝", "餐", "饭", "面", "肉", "鸡蛋", "牛奶", "酸奶", "豆浆", "沙拉", "水果", "零食"]
        ):
            return None
        calories = cls._extract_number_before_units(fragment, ["kcal", "千卡", "大卡"])
        protein = cls._extract_protein_g(fragment)
        return {
            "meal_type": meal_type,
            "raw_text": fragment,
            "meal_date": cls._infer_meal_date(fragment),
            "estimated_calories": calories,
            "protein_g": protein,
            "confidence": 0.7 if calories > 0 else 0.45,
            "needs_estimation": calories <= 0,
        }

    @classmethod
    def _suggest_meals_from_fragments(cls, fragments: list[str]) -> list[dict[str, Any]]:
        meals: list[dict[str, Any]] = []
        for fragment in fragments:
            meal = cls._suggest_meal_from_fragment(fragment)
            if meal is not None:
                meals.append(meal)
                continue
            if not meals:
                continue
            calories = cls._extract_number_before_units(fragment, ["kcal", "千卡", "大卡"])
            protein = cls._extract_protein_g(fragment)
            if calories > 0:
                meals[-1]["estimated_calories"] = calories
                meals[-1]["confidence"] = max(_as_float(meals[-1].get("confidence")), 0.7)
                meals[-1]["needs_estimation"] = False
            if protein > 0:
                meals[-1]["protein_g"] = protein
        return meals[:5]

    @staticmethod
    def _suggest_weight_from_text(text: str) -> dict[str, Any] | None:
        match = re.search(r"(?:体重|称重|今天|早上)?\s*(\d{2,3}(?:\.\d+)?)\s*(kg|公斤|斤)", text, re.I)
        if not match:
            return None
        value = _as_float(match.group(1))
        unit = match.group(2).lower()
        weight_kg = round(value / 2, 2) if unit == "斤" else value
        return {"weight_kg": weight_kg, "source": "user", "raw_text": match.group(0).strip()}

    @staticmethod
    def _suggest_profile_updates_from_text(text: str) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        height_match = re.search(r"(\d{3})\s*(?:cm|厘米)", text, re.I)
        if height_match:
            updates["height_cm"] = _as_float(height_match.group(1))
        target_match = re.search(r"目标(?:体重)?(?:是|到|降到)?\s*(\d{2,3}(?:\.\d+)?)\s*(kg|公斤|斤)?", text)
        if target_match:
            value = _as_float(target_match.group(1))
            updates["target_weight_kg"] = round(value / 2, 2) if target_match.group(2) == "斤" else value
        preferences = []
        for keyword in ["不吃", "忌口", "过敏", "喜欢", "偏好"]:
            if keyword in text:
                preferences.append(text[:120])
                break
        if preferences:
            updates["diet_preferences"] = preferences
        return updates

    @staticmethod
    def _infer_meal_type(fragment: str) -> str:
        if any(word in fragment for word in ["早餐", "早饭", "早上"]):
            return "breakfast"
        if any(word in fragment for word in ["午餐", "午饭", "中午"]):
            return "lunch"
        if any(word in fragment for word in ["晚餐", "晚饭", "晚上"]):
            return "dinner"
        if any(word in fragment for word in ["加餐", "零食", "夜宵", "下午茶"]):
            return "snack"
        return "unknown"

    @staticmethod
    def _infer_meal_date(fragment: str) -> str:
        if any(word in fragment for word in ["今天", "早上", "中午", "晚上"]):
            return _today()
        return ""

    @staticmethod
    def _extract_number_before_units(text: str, units: list[str]) -> float:
        unit_pattern = "|".join(re.escape(unit) for unit in units)
        match = re.search(rf"(\d{{1,4}}(?:\.\d+)?)\s*(?:{unit_pattern})", text, re.I)
        return _as_float(match.group(1)) if match else 0.0

    @staticmethod
    def _extract_protein_g(text: str) -> float:
        after_label = re.search(r"蛋白(?:质)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:g|克)?", text, re.I)
        if after_label:
            return _as_float(after_label.group(1))
        return DietStore._extract_number_before_units(text, ["g蛋白", "克蛋白", "蛋白"])

    @staticmethod
    def _diet_inbox_confirmations(
        text: str,
        *,
        suggested_meals: list[dict[str, Any]],
        weight_candidate: dict[str, Any] | None,
        profile_updates: dict[str, Any],
    ) -> list[str]:
        confirmations: list[str] = []
        if not text:
            confirmations.append("需要补充要整理的饮食内容。")
        if not suggested_meals and weight_candidate is None and not profile_updates:
            confirmations.append("这段内容未识别出明确餐食、体重或档案信息。")
        if any(meal.get("meal_type") == "unknown" for meal in suggested_meals):
            confirmations.append("部分餐食未识别出餐次，需要确认是早餐、午餐、晚餐还是加餐。")
        if any(meal.get("needs_estimation") for meal in suggested_meals):
            confirmations.append("部分餐食缺少热量估算，需要确认是否由模型估算后再写入。")
        if profile_updates:
            confirmations.append("档案或偏好更新需要确认后再调用 profile_update 或 memory_write。")
        return confirmations

    @staticmethod
    def _diet_inbox_next_actions(
        *,
        suggested_meals: list[dict[str, Any]],
        weight_candidate: dict[str, Any] | None,
        profile_updates: dict[str, Any],
        confirmations: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if suggested_meals:
            actions.append("确认餐次和估算后调用 meal_log_add 写入餐食。")
        if weight_candidate is not None:
            actions.append("确认体重数值后调用 weight_log_add 写入体重。")
        if profile_updates:
            actions.append("确认档案字段后调用 profile_update；长期偏好可再确认后写入 memory。")
        if confirmations:
            actions.append("先向用户确认不确定项，再写入结构化数据。")
        if not actions:
            actions.append("直接简短回复，不需要写入饮食结构化数据。")
        return actions

    @staticmethod
    def _diet_inbox_intent(
        *,
        suggested_meals: list[dict[str, Any]],
        weight_candidate: dict[str, Any] | None,
        profile_updates: dict[str, Any],
    ) -> str:
        hits = sum(bool(item) for item in [suggested_meals, weight_candidate, profile_updates])
        if hits >= 2:
            return "mixed"
        if suggested_meals:
            return "meal"
        if weight_candidate is not None:
            return "weight"
        if profile_updates:
            return "profile"
        return "chat"


def _runtime_scope(runtime_context: dict[str, Any] | None, explicit: str = "") -> str:
    return str(explicit or (runtime_context or {}).get("memory_user_scope", "")).strip()


def register_diet_tools(registry: ToolRegistry, diet_store: DietStore) -> None:
    """注册个人饮食与体重管理工具。"""

    def profile_get(*, user_scope: str = "", __runtime_context: dict[str, Any] | None = None) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        profile = diet_store.get_profile(scope)
        if not profile:
            return _json(
                {
                    "status": "missing",
                    "user_scope": scope,
                    "missing_fields": ["height_cm", "current_weight_kg", "target_weight_kg", "activity_level"],
                }
            )
        return _json({"status": "ok", "profile": profile})

    def profile_update(
        *,
        display_name: str | None = None,
        gender: str | None = None,
        birth_year: int | None = None,
        height_cm: float | None = None,
        current_weight_kg: float | None = None,
        target_weight_kg: float | None = None,
        activity_level: str | None = None,
        timezone: str | None = None,
        diet_preferences: list[Any] | None = None,
        allergies: list[Any] | None = None,
        medical_notes: str | None = None,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        updates = _provided_fields(
            {
                "display_name": display_name,
                "gender": gender,
                "birth_year": birth_year,
                "height_cm": height_cm,
                "current_weight_kg": current_weight_kg,
                "target_weight_kg": target_weight_kg,
                "activity_level": activity_level,
                "timezone": timezone,
                "diet_preferences": diet_preferences,
                "allergies": allergies,
                "medical_notes": medical_notes,
            }
        )
        profile = diet_store.update_profile(scope, **updates)
        return _json({"status": "saved", "profile": profile})

    def meal_log_add(
        *,
        meal_type: str,
        raw_text: str,
        meal_date: str = "",
        items: list[dict[str, Any]] | None = None,
        estimated_calories: float = 0.0,
        protein_g: float = 0.0,
        carbs_g: float = 0.0,
        fat_g: float = 0.0,
        confidence: float = 0.5,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        row = diet_store.add_meal_log(
            scope,
            meal_type=meal_type,
            raw_text=raw_text,
            meal_date=meal_date,
            items=items or [],
            estimated_calories=estimated_calories,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            confidence=confidence,
        )
        return _json({"status": "saved", "meal": row})

    def meal_log_list(
        *,
        meal_date: str = "",
        limit: int = 20,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        rows = diet_store.list_meal_logs(scope, meal_date=meal_date, limit=limit)
        return _json({"status": "ok", "meals": rows})

    def nutrition_day_summary(
        *,
        date: str = "",
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        row = diet_store.summarize_day(scope, date=date)
        return _json({"status": "ok", "summary": row})

    def diet_plan_generate(
        *,
        plan_date: str = "",
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        row = diet_store.generate_plan(scope, plan_date=plan_date)
        return _json({"status": "ok", "plan": row})

    def weight_log_add(
        *,
        weight_kg: float,
        source: str = "user",
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        row = diet_store.add_weight_log(scope, weight_kg=weight_kg, source=source)
        return _json({"status": "saved", "weight": row})

    def progress_summary(
        *,
        days: int = 7,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json({"status": "ok", "progress": diet_store.progress_summary(scope, days=days)})

    def diet_coach_briefing(
        *,
        days: int = 7,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json({"status": "ok", "briefing": diet_store.coach_briefing(scope, days=days)})

    def diet_daily_loop_generate(
        *,
        date: str = "",
        days: int = 7,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json({"status": "ok", "loop": diet_store.daily_loop(scope, date=date, days=days)})

    def diet_next_meal_card_generate(
        *,
        date: str = "",
        meal_type: str = "",
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json(
            {
                "status": "ok",
                "next_meal_card": diet_store.generate_next_meal_card(
                    scope,
                    date=date,
                    meal_type=meal_type,
                ),
            }
        )

    def format_diet_next_meal_card(card_json: str) -> str:
        if not card_json.strip():
            return "Error: card_json is required"
        data = json.loads(card_json)
        if not isinstance(data, dict):
            return "Error: card_json must be a JSON object"
        card = data.get("next_meal_card") if isinstance(data.get("next_meal_card"), dict) else data
        if not isinstance(card, dict):
            return "Error: card_json must contain a next_meal_card object"
        if card.get("type") != "diet_next_meal_card":
            return "Error: card_json type must be diet_next_meal_card"

        remaining = _as_float(card.get("remaining_calories"))
        actual = _as_float(card.get("actual_calories"))
        target = _as_float(card.get("target_calories"))
        protein = _as_float(card.get("protein_g"))
        sections = [
            "## 下一餐建议",
            f"- 餐次：{card.get('next_meal_label') or '下一餐'}",
            f"- 第一步：{card.get('first_action') or '按清淡高蛋白模板选择。'}",
            f"- 今日摄入：约 {actual:.0f} / {target:.0f} kcal",
            f"- 剩余热量：约 {remaining:.0f} kcal",
            f"- 已记录蛋白质：约 {protein:.0f}g",
            f"- 计划状态：{card.get('plan_status') or 'unknown'}",
            "",
            "## 推荐选择",
            _markdown_bullets(
                card.get("recommended_options")
                if isinstance(card.get("recommended_options"), list)
                else []
            ),
            "",
            "## 边界",
            _markdown_bullets(card.get("guardrails") if isinstance(card.get("guardrails"), list) else []),
            "",
            "## 吃完后",
            _markdown_bullets(card.get("reminders") if isinstance(card.get("reminders"), list) else []),
            "",
            f"> 边界：{card.get('note') or '这是下一餐建议卡片，不会自动写入餐食或生成新计划。'}",
        ]
        return "\n".join(sections).strip()

    def diet_day_review_plan_generate(
        *,
        date: str = "",
        days: int = 7,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json(
            {
                "status": "ok",
                "review_plan": diet_store.generate_day_review_plan(scope, date=date, days=days),
            }
        )

    def diet_weekly_plan_generate(
        *,
        week_goal: str = "",
        days: int = 7,
        focus_areas: list[str] | None = None,
        constraints: list[str] | None = None,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json(
            {
                "status": "ok",
                "weekly_plan": diet_store.generate_weekly_plan(
                    scope,
                    week_goal=week_goal,
                    days=days,
                    focus_areas=focus_areas or [],
                    constraints=constraints or [],
                ),
            }
        )

    def diet_inbox_triage(
        *,
        text: str,
        context: str = "",
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = _runtime_scope(__runtime_context, user_scope)
        return _json(
            {
                "status": "ok",
                "triage": diet_store.triage_inbox(
                    scope,
                    text,
                    context=context,
                ),
            }
        )

    def diet_inbox_commit(
        *,
        triage_json: str,
        commit_meals: bool = True,
        commit_weight: bool = True,
        commit_profile: bool = True,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if not triage_json.strip():
            return "Error: triage_json is required"
        data = json.loads(triage_json)
        if not isinstance(data, dict):
            return "Error: triage_json must be a JSON object"
        if data.get("status") == "ok" and isinstance(data.get("triage"), dict):
            data = data["triage"]
        scope = _runtime_scope(__runtime_context, user_scope)
        try:
            result = diet_store.commit_inbox_triage(
                scope,
                data,
                commit_meals=commit_meals,
                commit_weight=commit_weight,
                commit_profile=commit_profile,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return _json({"status": "saved", "commit": result})

    registry.register(
        RegisteredTool(
            name="profile_get",
            description="Get the current user's diet and weight profile.",
            input_schema={"type": "object", "properties": {"user_scope": {"type": "string"}}},
            handler=profile_get,
            tags=("diet", "profile", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="profile_update",
            description="Update the current user's diet and weight profile.",
            input_schema={
                "type": "object",
                "properties": {
                    "display_name": {"type": "string", "description": "用户昵称或展示名。"},
                    "gender": {
                        "type": "string",
                        "enum": ["male", "female", "other", "unknown"],
                        "description": (
                            "用户性别。用户说“男/男性/成年男性/男生/我是男的”时写 male；"
                            "说“女/女性/成年女性/女生/我是女的”时写 female。"
                        ),
                    },
                    "birth_year": {
                        "type": "integer",
                        "description": "出生年份；如果用户只说年龄，按当前年份推算。",
                    },
                    "height_cm": {"type": "number", "description": "身高，单位 cm。"},
                    "current_weight_kg": {"type": "number", "description": "当前体重，单位 kg。"},
                    "target_weight_kg": {"type": "number", "description": "目标体重，单位 kg。"},
                    "activity_level": {
                        "type": "string",
                        "description": "活动水平，例如 sedentary/light/moderate/high。",
                    },
                    "timezone": {"type": "string", "description": "用户所在时区。"},
                    "diet_preferences": {"type": "array", "description": "长期饮食偏好。"},
                    "allergies": {"type": "array", "description": "过敏或忌口。"},
                    "medical_notes": {"type": "string", "description": "医疗相关备注。"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=profile_update,
            tags=("diet", "profile", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="meal_log_add",
            description="Save one meal record with estimated nutrition.",
            input_schema={
                "type": "object",
                "required": ["meal_type", "raw_text"],
                "properties": {
                    "meal_type": {"type": "string"},
                    "raw_text": {"type": "string"},
                    "meal_date": {"type": "string"},
                    "items": {"type": "array"},
                    "estimated_calories": {"type": "number"},
                    "protein_g": {"type": "number"},
                    "carbs_g": {"type": "number"},
                    "fat_g": {"type": "number"},
                    "confidence": {"type": "number"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=meal_log_add,
            tags=("diet", "meal", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="meal_log_list",
            description="List meal records for a date.",
            input_schema={
                "type": "object",
                "properties": {
                    "meal_date": {"type": "string"},
                    "limit": {"type": "integer"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=meal_log_list,
            tags=("diet", "meal", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="nutrition_day_summary",
            description="Summarize one day's calorie and macro intake.",
            input_schema={
                "type": "object",
                "properties": {"date": {"type": "string"}, "user_scope": {"type": "string"}},
            },
            handler=nutrition_day_summary,
            tags=("diet", "summary", "read", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_plan_generate",
            description="Generate and save a practical daily diet plan.",
            input_schema={
                "type": "object",
                "properties": {"plan_date": {"type": "string"}, "user_scope": {"type": "string"}},
            },
            handler=diet_plan_generate,
            tags=("diet", "plan", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="weight_log_add",
            description="Save a body weight record.",
            input_schema={
                "type": "object",
                "required": ["weight_kg"],
                "properties": {
                    "weight_kg": {"type": "number"},
                    "source": {"type": "string"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=weight_log_add,
            tags=("diet", "weight", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="progress_summary",
            description="Summarize recent diet and weight progress.",
            input_schema={
                "type": "object",
                "properties": {"days": {"type": "integer"}, "user_scope": {"type": "string"}},
            },
            handler=progress_summary,
            tags=("diet", "summary", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_coach_briefing",
            description="Generate a user-facing diet coach briefing from recent meals and weight trends.",
            input_schema={
                "type": "object",
                "properties": {"days": {"type": "integer"}, "user_scope": {"type": "string"}},
            },
            handler=diet_coach_briefing,
            tags=("diet", "briefing", "summary", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_daily_loop_generate",
            description="Generate a user-facing daily diet execution loop from today's records, plan, weight, risks, and next actions.",
            input_schema={
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "days": {"type": "integer"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_daily_loop_generate,
            tags=("diet", "daily", "summary", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_next_meal_card_generate",
            description=(
                "Generate a next-meal decision card from today's recorded meals, "
                "daily plan, calorie gap, protein intake, and risk flags without writing data."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "meal_type": {
                        "type": "string",
                        "description": "Optional target meal: breakfast/lunch/dinner/snack or Chinese aliases.",
                    },
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_next_meal_card_generate,
            tags=("diet", "meal", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_diet_next_meal_card",
            description=(
                "Format a diet_next_meal_card JSON object into a concise Chinese "
                "Markdown next-meal recommendation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["card_json"],
                "properties": {
                    "card_json": {
                        "type": "string",
                        "description": "JSON string returned by diet_next_meal_card_generate.",
                    },
                },
            },
            handler=format_diet_next_meal_card,
            tags=("diet", "meal", "format", "user-facing"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_day_review_plan_generate",
            description=(
                "Generate a diet day review and tomorrow strategy draft from today's records "
                "and recent trends without writing new meals, weight, or plans."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "days": {"type": "integer"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_day_review_plan_generate,
            tags=("diet", "review", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_weekly_plan_generate",
            description=(
                "Generate a diet weekly plan draft from recent meals, weight trend, "
                "optional week goal, focus areas, and constraints without writing meals, weight, or daily plans."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "week_goal": {"type": "string"},
                    "days": {"type": "integer"},
                    "focus_areas": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_weekly_plan_generate,
            tags=("diet", "weekly", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_inbox_triage",
            description=(
                "Triage a messy diet message into candidate meals, weight, profile updates, "
                "confirmation questions, and next actions without writing data."
            ),
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "context": {"type": "string"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_inbox_triage,
            tags=("diet", "inbox", "planning", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="diet_inbox_commit",
            description=(
                "Commit a confirmed diet_inbox_triage JSON into structured meals, weight, "
                "and safe profile fields. It does not write long-term memory candidates."
            ),
            input_schema={
                "type": "object",
                "required": ["triage_json"],
                "properties": {
                    "triage_json": {
                        "type": "string",
                        "description": "JSON string returned by diet_inbox_triage.",
                    },
                    "commit_meals": {"type": "boolean"},
                    "commit_weight": {"type": "boolean"},
                    "commit_profile": {"type": "boolean"},
                    "user_scope": {"type": "string"},
                },
            },
            handler=diet_inbox_commit,
            tags=("diet", "inbox", "meal", "weight", "profile", "write"),
        )
    )
