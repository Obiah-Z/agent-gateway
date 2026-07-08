import json
from pathlib import Path

from agent_gateway.ai.context.diet import DietStore, register_diet_tools
from agent_gateway.ai.tools.registry import ToolRegistry


def test_diet_store_isolates_meal_logs_by_user_scope(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")

    store.add_meal_log(
        "user:alice",
        meal_type="breakfast",
        raw_text="燕麦和鸡蛋",
        estimated_calories=350,
        protein_g=20,
    )
    store.add_meal_log(
        "user:bob",
        meal_type="breakfast",
        raw_text="牛肉饭",
        estimated_calories=750,
        protein_g=35,
    )

    alice_meals = store.list_meal_logs("user:alice")
    bob_summary = store.summarize_day("user:bob")

    assert len(alice_meals) == 1
    assert alice_meals[0]["raw_text"] == "燕麦和鸡蛋"
    assert bob_summary["actual_calories"] == 750
    assert "燕麦" not in json.dumps(bob_summary, ensure_ascii=False)


def test_diet_tools_use_runtime_context_scope(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)

    profile_raw = registry.dispatch(
        "profile_update",
        {
            "height_cm": 178,
            "current_weight_kg": 82,
            "target_weight_kg": 75,
            "diet_preferences": ["中餐", "外卖"],
        },
        runtime_context={"memory_user_scope": "user:wework:test"},
    )
    meal_raw = registry.dispatch(
        "meal_log_add",
        {
            "meal_type": "lunch",
            "raw_text": "牛肉饭一份，无糖可乐",
            "estimated_calories": 780,
            "protein_g": 38,
            "confidence": 0.7,
        },
        runtime_context={"memory_user_scope": "user:wework:test"},
    )
    summary_raw = registry.dispatch(
        "nutrition_day_summary",
        {},
        runtime_context={"memory_user_scope": "user:wework:test"},
    )
    plan_raw = registry.dispatch(
        "diet_plan_generate",
        {},
        runtime_context={"memory_user_scope": "user:wework:test"},
    )

    profile = json.loads(profile_raw)
    meal = json.loads(meal_raw)
    summary = json.loads(summary_raw)
    plan = json.loads(plan_raw)

    assert profile["status"] == "saved"
    assert profile["profile"]["user_scope"] == "user:wework:test"
    assert meal["meal"]["user_scope"] == "user:wework:test"
    assert summary["summary"]["actual_calories"] == 780
    assert plan["plan"]["user_scope"] == "user:wework:test"
    assert plan["plan"]["meals"]["breakfast"]


def test_profile_update_preserves_existing_fields_when_partially_updating(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:test"}

    registry.dispatch(
        "profile_update",
        {
            "height_cm": 166,
            "current_weight_kg": 60,
            "target_weight_kg": 55,
            "activity_level": "sedentary",
            "timezone": "Asia/Shanghai",
        },
        runtime_context=context,
    )

    raw = registry.dispatch(
        "profile_update",
        {"birth_year": 2003},
        runtime_context=context,
    )

    profile = json.loads(raw)["profile"]
    assert profile["birth_year"] == 2003
    assert profile["height_cm"] == 166
    assert profile["current_weight_kg"] == 60
    assert profile["target_weight_kg"] == 55
    assert profile["activity_level"] == "sedentary"


def test_profile_update_schema_guides_gender_inference(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)

    schema = registry.get("profile_update").schema()["input_schema"]
    gender = schema["properties"]["gender"]

    assert gender["enum"] == ["male", "female", "other", "unknown"]
    assert "成年男性" in gender["description"]
    assert "male" in gender["description"]


def test_diet_plan_adjusts_dinner_after_repeated_high_dinners(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    scope = "user:wework:diet"
    for day in range(1, 4):
        store.add_meal_log(
            scope,
            meal_date=f"2026-07-0{day}",
            meal_type="dinner",
            raw_text="重油外卖晚餐",
            estimated_calories=850,
        )

    plan = store.generate_plan(scope, plan_date="2026-07-04")

    adjustment = plan["metadata"]["adjustment"]
    assert adjustment["lighter_dinner"] is True
    assert adjustment["high_dinner_count"] == 3
    assert "自动降低晚餐油脂" in plan["generated_reason"]
    assert "半拳粗粮主食" in plan["meals"]["dinner"][0]


def test_diet_plan_adjusts_for_low_protein_and_missing_breakfast(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    scope = "user:wework:diet"
    for day in range(1, 7):
        store.add_meal_log(
            scope,
            meal_date=f"2026-07-0{day}",
            meal_type="lunch" if day % 2 else "dinner",
            raw_text="普通简餐",
            estimated_calories=520,
            protein_g=12,
        )

    plan = store.generate_plan(scope, plan_date="2026-07-07")

    adjustment = plan["metadata"]["adjustment"]
    assert adjustment["protein_focus"] is True
    assert adjustment["breakfast_simple"] is True
    assert "提高蛋白质优先级" in plan["generated_reason"]
    assert "固定早餐" in plan["meals"]["breakfast"][0]
    assert "双蛋白" in plan["meals"]["lunch"][0]


def test_diet_today_status_reports_meals_plan_weight_and_flags(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    scope = "user:wework:diet"
    store.update_profile(scope, height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log(scope, 81.5, recorded_at=1783290000.0)
    store.generate_plan(scope, plan_date="2026-07-06")
    store.add_meal_log(
        scope,
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="牛肉饭一份",
        estimated_calories=780,
        protein_g=30,
    )

    status = store.today_status(scope, date="2026-07-06")

    assert status["date"] == "2026-07-06"
    assert status["meal_count"] == 1
    assert status["actual_calories"] == 780
    assert status["latest_weight"]["weight_kg"] == 81.5
    assert status["plan"]["plan_date"] == "2026-07-06"
    assert status["missing_meals"] == ["breakfast", "dinner"]
    assert status["trend_7d"]["average_calories"] == 780.0
    assert "missing_meals" in status["risk_flags"]


def test_diet_progress_summary_reports_daily_trends(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    scope = "user:wework:diet"
    store.add_weight_log(scope, 82.0, recorded_at=100.0)
    store.add_weight_log(scope, 81.2, recorded_at=200.0)
    store.add_meal_log(
        scope,
        meal_date="2026-07-05",
        meal_type="breakfast",
        raw_text="鸡蛋豆浆",
        estimated_calories=320,
        protein_g=22,
    )
    store.add_meal_log(
        scope,
        meal_date="2026-07-05",
        meal_type="lunch",
        raw_text="牛肉饭",
        estimated_calories=720,
        protein_g=35,
    )
    store.add_meal_log(
        scope,
        meal_date="2026-07-06",
        meal_type="dinner",
        raw_text="鸡胸沙拉",
        estimated_calories=460,
        protein_g=42,
    )

    progress = store.progress_summary(scope, days=7)

    assert progress["meal_count"] == 3
    assert progress["weight_change_kg"] == -0.8
    assert progress["average_calories"] == 750.0
    assert progress["average_protein_g"] == 49.5
    assert progress["missing_meal_days"] == 2
    assert progress["daily"][0]["date"] == "2026-07-06"
    assert progress["daily"][0]["missing_meals"] == ["breakfast", "lunch"]


def test_diet_coach_briefing_reports_risks_and_actions(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    registry.dispatch(
        "profile_update",
        {"height_cm": 178, "current_weight_kg": 82, "target_weight_kg": 75},
        runtime_context=context,
    )
    registry.dispatch(
        "weight_log_add",
        {"weight_kg": 82.0},
        runtime_context=context,
    )
    registry.dispatch(
        "meal_log_add",
        {
            "meal_date": "2026-07-05",
            "meal_type": "lunch",
            "raw_text": "炸鸡饭和奶茶",
            "estimated_calories": 1200,
            "protein_g": 20,
        },
        runtime_context=context,
    )
    registry.dispatch(
        "meal_log_add",
        {
            "meal_date": "2026-07-06",
            "meal_type": "dinner",
            "raw_text": "炒饭",
            "estimated_calories": 900,
            "protein_g": 15,
        },
        runtime_context=context,
    )

    briefing = json.loads(
        registry.dispatch(
            "diet_coach_briefing",
            {"days": 7},
            runtime_context=context,
        )
    )["briefing"]

    assert briefing["meal_count"] == 2
    assert "meal_logging_incomplete" in briefing["risk_flags"]
    assert "protein_low" in briefing["risk_flags"]
    assert any("补齐早餐、午餐、晚餐" in action for action in briefing["suggested_actions"])
    assert any("一掌蛋白质" in action for action in briefing["suggested_actions"])
    assert briefing["recent_daily"][0]["date"] == "2026-07-06"


def test_format_diet_coach_briefing_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    registry.dispatch(
        "profile_update",
        {"height_cm": 178, "current_weight_kg": 82, "target_weight_kg": 75},
        runtime_context=context,
    )
    registry.dispatch(
        "weight_log_add",
        {"weight_kg": 82.0},
        runtime_context=context,
    )
    registry.dispatch(
        "meal_log_add",
        {
            "meal_date": "2026-07-05",
            "meal_type": "lunch",
            "raw_text": "炸鸡饭和奶茶",
            "estimated_calories": 1200,
            "protein_g": 20,
        },
        runtime_context=context,
    )
    registry.dispatch(
        "meal_log_add",
        {
            "meal_date": "2026-07-06",
            "meal_type": "dinner",
            "raw_text": "炒饭",
            "estimated_calories": 900,
            "protein_g": 15,
        },
        runtime_context=context,
    )
    briefing_json = registry.dispatch(
        "diet_coach_briefing",
        {"days": 7},
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_coach_briefing", {"briefing_json": briefing_json})

    assert "## 饮食趋势简报" in formatted
    assert "- 观察窗口：近 7 天" in formatted
    assert "- 已记录餐次：2 餐" in formatted
    assert "- 平均蛋白质：约" in formatted
    assert "## 亮点" in formatted
    assert "最近共记录 2 餐" in formatted
    assert "- meal_logging_incomplete" in formatted
    assert "- protein_low" in formatted
    assert "补齐早餐、午餐、晚餐" in formatted
    assert "一掌蛋白质" in formatted
    assert "- 2026-07-06：约 900 kcal" in formatted
    assert "不会自动写入餐食、体重或生成计划" in formatted


def test_diet_daily_loop_combines_today_plan_weight_and_actions(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=1783290000.0)
    store.generate_plan("user:wework:diet", plan_date="2026-07-06")
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="牛肉饭一份",
        estimated_calories=780,
        protein_g=30,
    )
    store.add_meal_log(
        "user:other",
        meal_date="2026-07-06",
        meal_type="breakfast",
        raw_text="燕麦",
        estimated_calories=300,
        protein_g=12,
    )

    loop = json.loads(
        registry.dispatch(
            "diet_daily_loop_generate",
            {"date": "2026-07-06", "days": 7},
            runtime_context=context,
        )
    )["loop"]

    assert loop["type"] == "diet_daily_loop"
    assert loop["user_scope"] == "user:wework:diet"
    assert loop["date"] == "2026-07-06"
    assert loop["profile_complete"] is True
    assert loop["actual_calories"] == 780
    assert loop["latest_weight"]["weight_kg"] == 81.5
    assert loop["plan_status"] == "available"
    assert loop["plan"]["plan_date"] == "2026-07-06"
    assert loop["missing_meals"] == ["breakfast", "dinner"]
    assert "missing_meals" in loop["risk_flags"]
    assert any("补记缺失餐次" in action for action in loop["next_actions"])
    serialized = json.dumps(loop, ensure_ascii=False)
    assert "user:other" not in serialized
    assert "recent_meals" not in loop


def test_format_diet_daily_loop_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=1783290000.0)
    store.generate_plan("user:wework:diet", plan_date="2026-07-06")
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="牛肉饭一份",
        estimated_calories=780,
        protein_g=30,
    )
    loop_json = registry.dispatch(
        "diet_daily_loop_generate",
        {"date": "2026-07-06", "days": 7},
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_daily_loop", {"loop_json": loop_json})

    assert "## 今日饮食闭环" in formatted
    assert "- 日期：2026-07-06" in formatted
    assert "- 今日摄入：约 780 / 1550 kcal" in formatted
    assert "- 蛋白质：约 30g" in formatted
    assert "- 当前体重：81.5 kg" in formatted
    assert "- 计划状态：available" in formatted
    assert "- 档案状态：已完整" in formatted
    assert "- breakfast" in formatted
    assert "- dinner" in formatted
    assert "- 早餐：" in formatted
    assert "- missing_meals" in formatted
    assert "补记缺失餐次" in formatted
    assert "餐食记录未闭环" in formatted
    assert "不会自动写入新记录或生成新计划" in formatted


def test_diet_daily_loop_reports_missing_plan_without_auto_generating(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    loop = store.daily_loop("user:wework:diet", date="2026-07-06")

    assert loop["plan_status"] == "missing"
    assert loop["plan"] is None
    assert store.get_plan("user:wework:diet", plan_date="2026-07-06") is None
    assert "今日计划缺失" in loop["reminders"]
    assert any("生成今日饮食计划" in action for action in loop["next_actions"])


def test_diet_next_meal_card_uses_today_plan_without_writing(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.generate_plan("user:wework:diet", plan_date="2026-07-06")
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="breakfast",
        raw_text="鸡蛋和豆浆",
        estimated_calories=320,
        protein_g=22,
    )
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="牛肉饭",
        estimated_calories=760,
        protein_g=30,
    )

    card = json.loads(
        registry.dispatch(
            "diet_next_meal_card_generate",
            {"date": "2026-07-06"},
            runtime_context=context,
        )
    )["next_meal_card"]

    assert card["type"] == "diet_next_meal_card"
    assert card["user_scope"] == "user:wework:diet"
    assert card["date"] == "2026-07-06"
    assert card["next_meal"] == "dinner"
    assert card["next_meal_label"] == "晚餐"
    assert card["plan_status"] == "available"
    assert card["actual_calories"] == 1080
    assert card["remaining_calories"] == 470.0
    assert card["recommended_options"][0] == "清淡蛋白质 + 大量蔬菜 + 半拳主食"
    assert card["first_action"].startswith("下一餐按「晚餐」处理")
    assert "晚餐还未记录，吃完后补记。" in card["reminders"]
    assert store.get_day_summary("user:wework:diet", date="2026-07-06") is None


def test_format_diet_next_meal_card_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.generate_plan("user:wework:diet", plan_date="2026-07-06")
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="breakfast",
        raw_text="鸡蛋和豆浆",
        estimated_calories=320,
        protein_g=22,
    )
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="牛肉饭",
        estimated_calories=760,
        protein_g=30,
    )
    card_json = registry.dispatch(
        "diet_next_meal_card_generate",
        {"date": "2026-07-06"},
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_next_meal_card", {"card_json": card_json})

    assert "## 下一餐建议" in formatted
    assert "- 餐次：晚餐" in formatted
    assert "- 今日摄入：约 1080 / 1550 kcal" in formatted
    assert "- 剩余热量：约 470 kcal" in formatted
    assert "- 已记录蛋白质：约 52g" in formatted
    assert "- 清淡蛋白质 + 大量蔬菜 + 半拳主食" in formatted
    assert "- 蛋白质偏低，下一餐优先补一掌蛋白质。" in formatted
    assert "- 晚餐还未记录，吃完后补记。" in formatted
    assert "不会自动写入餐食或生成新计划" in formatted


def test_diet_day_review_plan_generates_tomorrow_strategy_without_writing(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=1783290000.0)
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="炸鸡饭和奶茶",
        estimated_calories=1200,
        protein_g=20,
    )

    review_plan = json.loads(
        registry.dispatch(
            "diet_day_review_plan_generate",
            {"date": "2026-07-06", "days": 7},
            runtime_context=context,
        )
    )["review_plan"]

    assert review_plan["type"] == "diet_day_review_plan"
    assert review_plan["user_scope"] == "user:wework:diet"
    assert review_plan["date"] == "2026-07-06"
    assert review_plan["review"]["actual_calories"] == 1200
    assert review_plan["review"]["missing_meals"] == ["breakfast", "dinner"]
    assert "missing_meals" in review_plan["risk_flags"]
    assert "明天先把早餐、午餐、晚餐都记录下来" in review_plan["tomorrow_strategy"]["actions"][0]
    assert "是否生成明日饮食计划？" in review_plan["needs_confirmation"]
    assert store.get_plan("user:wework:diet", plan_date="2026-07-06") is None
    assert store.get_day_summary("user:wework:diet", date="2026-07-06") is None


def test_format_diet_day_review_plan_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=1783290000.0)
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="lunch",
        raw_text="炸鸡饭和奶茶",
        estimated_calories=1200,
        protein_g=20,
    )
    plan_json = registry.dispatch(
        "diet_day_review_plan_generate",
        {"date": "2026-07-06", "days": 7},
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_day_review_plan", {"plan_json": plan_json})

    assert "## 今日饮食总结" in formatted
    assert "- 日期：2026-07-06" in formatted
    assert "- 摄入：约 1200 / 1550 kcal" in formatted
    assert "- 热量差：-350 kcal" in formatted
    assert "- 蛋白质：约 20g" in formatted
    assert "- breakfast" in formatted
    assert "- dinner" in formatted
    assert "## 近期趋势" in formatted
    assert "- missing_meals" in formatted
    assert "- 重点：" in formatted
    assert "- 明天先把早餐、午餐、晚餐都记录下来" in formatted
    assert "- 是否生成明日饮食计划？" in formatted
    assert "不会自动生成计划、写入体重或补记餐食" in formatted


def test_diet_weekly_plan_generates_draft_without_writing(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 82.0, recorded_at=100.0)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=200.0)
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-05",
        meal_type="lunch",
        raw_text="炸鸡饭和奶茶",
        estimated_calories=1200,
        protein_g=20,
    )
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="dinner",
        raw_text="炒饭",
        estimated_calories=900,
        protein_g=15,
    )

    weekly = json.loads(
        registry.dispatch(
            "diet_weekly_plan_generate",
            {
                "week_goal": "本周把三餐记录稳定下来",
                "focus_areas": ["补早餐记录", "提高蛋白质"],
                "constraints": ["工作日只能吃外卖"],
                "days": 7,
            },
            runtime_context=context,
        )
    )["weekly_plan"]

    assert weekly["type"] == "diet_weekly_plan"
    assert weekly["user_scope"] == "user:wework:diet"
    assert weekly["week_goal"] == "本周把三餐记录稳定下来"
    assert weekly["target_calories"] == 1550
    assert weekly["focus_areas"] == ["补早餐记录", "提高蛋白质"]
    assert weekly["trend"]["meal_count"] == 2
    assert "meal_logging_incomplete" in weekly["risk_flags"]
    assert "protein_low" in weekly["risk_flags"]
    assert any("补齐早餐、午餐、晚餐" in action for action in weekly["weekly_actions"])
    assert any("每日目标热量参考约 1550 kcal" in item for item in weekly["daily_guidelines"])
    assert weekly["constraints"] == ["工作日只能吃外卖"]
    assert "这些限制是否需要转成具体避坑规则？" in weekly["needs_confirmation"]
    assert store.get_plan("user:wework:diet", plan_date="2026-07-06") is None
    assert store.get_day_summary("user:wework:diet", date="2026-07-06") is None


def test_format_diet_weekly_plan_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    store.update_profile("user:wework:diet", height_cm=178, current_weight_kg=82, target_weight_kg=75)
    store.add_weight_log("user:wework:diet", 82.0, recorded_at=100.0)
    store.add_weight_log("user:wework:diet", 81.5, recorded_at=200.0)
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-05",
        meal_type="lunch",
        raw_text="炸鸡饭和奶茶",
        estimated_calories=1200,
        protein_g=20,
    )
    store.add_meal_log(
        "user:wework:diet",
        meal_date="2026-07-06",
        meal_type="dinner",
        raw_text="炒饭",
        estimated_calories=900,
        protein_g=15,
    )
    plan_json = registry.dispatch(
        "diet_weekly_plan_generate",
        {
            "week_goal": "本周把三餐记录稳定下来",
            "focus_areas": ["补早餐记录", "提高蛋白质"],
            "constraints": ["工作日只能吃外卖"],
            "days": 7,
        },
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_weekly_plan", {"plan_json": plan_json})

    assert "## 本周饮食计划草稿" in formatted
    assert "- 本周目标：本周把三餐记录稳定下来" in formatted
    assert "- 参考热量：约 1550 kcal/天" in formatted
    assert "- 补早餐记录" in formatted
    assert "- 提高蛋白质" in formatted
    assert "- meal_logging_incomplete" in formatted
    assert "- protein_low" in formatted
    assert "- 工作日只能吃外卖" in formatted
    assert "- 这些限制是否需要转成具体避坑规则？" in formatted
    assert "每日目标热量参考约 1550 kcal" in formatted
    assert "不会自动生成每日计划、写入体重或补记餐食" in formatted


def test_diet_inbox_triage_suggests_records_without_writing(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    triage = json.loads(
        registry.dispatch(
            "diet_inbox_triage",
            {
                "text": (
                    "今天早餐吃了鸡蛋豆浆约 320 kcal，蛋白 22g；"
                    "体重 81.5kg；目标降到 75kg；我不吃香菜。"
                )
            },
            runtime_context=context,
        )
    )["triage"]

    assert triage["type"] == "diet_inbox_triage"
    assert triage["user_scope"] == "user:wework:diet"
    assert triage["intent"] == "mixed"
    assert triage["suggested_meals"][0]["meal_type"] == "breakfast"
    assert triage["suggested_meals"][0]["estimated_calories"] == 320
    assert triage["suggested_meals"][0]["protein_g"] == 22
    assert triage["suggested_weight"]["weight_kg"] == 81.5
    assert triage["suggested_profile_updates"]["target_weight_kg"] == 75
    assert triage["suggested_profile_updates"]["diet_preferences"]
    assert any("profile_update" in action for action in triage["next_actions"])
    assert store.list_meal_logs("user:wework:diet") == []
    assert store.get_profile("user:wework:diet") is None


def test_format_diet_inbox_triage_outputs_user_facing_summary(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}

    triage_json = registry.dispatch(
        "diet_inbox_triage",
        {
            "text": (
                "今天早餐吃了鸡蛋豆浆约 320 kcal，蛋白 22g；"
                "体重 81.5kg；目标降到 75kg；我不吃香菜。"
            )
        },
        runtime_context=context,
    )

    formatted = registry.dispatch("format_diet_inbox_triage", {"triage_json": triage_json})

    assert "## 饮食收件箱整理" in formatted
    assert "- 判断：mixed" in formatted
    assert "## 餐食候选" in formatted
    assert "1. breakfast：今天早餐吃了鸡蛋豆浆约 320 kcal" in formatted
    assert "热量：约 320 kcal" in formatted
    assert "蛋白质：约 22g" in formatted
    assert "## 体重候选" in formatted
    assert "- 体重：81.5 kg" in formatted
    assert "## 档案/偏好候选" in formatted
    assert "- diet_preferences：" in formatted
    assert "不吃香菜" in formatted
    assert "- target_weight_kg：75" in formatted
    assert "- 档案或偏好更新需要确认后再调用 profile_update 或 memory_write。" in formatted
    assert "meal_log_add" in formatted
    assert "weight_log_add" in formatted
    assert "profile_update" in formatted
    assert "不会自动写入餐食、体重、档案或长期记忆" in formatted


def test_diet_inbox_commit_writes_confirmed_records_but_skips_preferences(tmp_path: Path) -> None:
    store = DietStore(tmp_path / "workspace")
    registry = ToolRegistry()
    register_diet_tools(registry, store)
    context = {"memory_user_scope": "user:wework:diet"}
    triage_json = registry.dispatch(
        "diet_inbox_triage",
        {
            "text": (
                "今天早餐吃了鸡蛋豆浆约 320 kcal，蛋白 22g；"
                "体重 81.5kg；目标降到 75kg；我不吃香菜。"
            )
        },
        runtime_context=context,
    )

    result = json.loads(
        registry.dispatch(
            "diet_inbox_commit",
            {"triage_json": triage_json},
            runtime_context=context,
        )
    )["commit"]

    assert result["type"] == "diet_inbox_commit"
    assert result["user_scope"] == "user:wework:diet"
    assert result["source"]["committed_meal_count"] == 1
    assert result["source"]["committed_weight"] is True
    assert result["source"]["profile_update_keys"] == ["target_weight_kg"]
    assert result["source"]["has_preference_candidate"] is True
    assert result["written_meals"][0]["meal_type"] == "breakfast"
    assert result["written_weight"]["weight_kg"] == 81.5
    assert result["written_profile"]["target_weight_kg"] == 75
    assert result["skipped"][0]["type"] == "diet_preferences"
    assert store.list_meal_logs("user:wework:diet")[0]["estimated_calories"] == 320
    assert store.get_profile("user:wework:diet")["target_weight_kg"] == 75
