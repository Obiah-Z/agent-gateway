import json
from pathlib import Path

from agent_gateway.ai.context.personal import PersonalStore, register_personal_tools
from agent_gateway.ai.tools.registry import ToolRegistry


def test_personal_store_isolates_todos_by_user_scope(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path / "workspace")

    alice = store.add_todo("背诵项目难点", priority="high", user_scope="user:alice")
    store.add_todo("整理简历", priority="normal", user_scope="user:bob")

    assert store.list_todos(user_scope="user:alice")[0]["id"] == alice["id"]
    assert store.list_todos(user_scope="user:bob")[0]["title"] == "整理简历"


def test_personal_store_completes_todo(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path / "workspace")
    todo = store.add_todo("模拟面试", user_scope="user:alice")

    completed = store.complete_todo(todo["id"], result="已完成一轮", user_scope="user:alice")

    assert completed is not None
    assert completed["status"] == "done"
    assert completed["result"] == "已完成一轮"
    assert store.list_todos(status="open", user_scope="user:alice") == []
    assert store.list_todos(status="done", user_scope="user:alice")[0]["id"] == todo["id"]


def test_personal_tools_use_runtime_user_scope(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_personal_tools(registry, PersonalStore(tmp_path / "workspace"))

    added = json.loads(
        registry.dispatch(
            "personal_todo_add",
            {"title": "明天背八股", "priority": "urgent"},
            runtime_context={"memory_user_scope": "user:alice"},
        )
    )
    listed = json.loads(
        registry.dispatch(
            "personal_todo_list",
            {"status": "open"},
            runtime_context={"memory_user_scope": "user:alice"},
        )
    )

    assert listed["count"] == 1
    assert listed["items"][0]["id"] == added["id"]


def test_personal_review_tools_write_and_read_recent_reviews(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_personal_tools(registry, PersonalStore(tmp_path / "workspace"))

    saved = json.loads(
        registry.dispatch(
            "personal_review_add",
            {
                "summary": "今日完成项目复盘",
                "completed": ["背项目难点"],
                "blockers": ["场景题不熟"],
                "next_step": "明天练秒杀设计",
            },
            runtime_context={"memory_user_scope": "user:alice"},
        )
    )
    recent = json.loads(
        registry.dispatch(
            "personal_review_recent",
            {"limit": 3},
            runtime_context={"memory_user_scope": "user:alice"},
        )
    )

    assert saved["summary"] == "今日完成项目复盘"
    assert recent["items"][0]["next_step"] == "明天练秒杀设计"


def test_personal_briefing_generate_summarizes_user_scope(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)

    registry.dispatch(
        "personal_todo_add",
        {"title": "背项目难点", "priority": "urgent"},
        runtime_context={"memory_user_scope": "user:alice"},
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "整理简历", "priority": "normal"},
        runtime_context={"memory_user_scope": "user:bob"},
    )
    registry.dispatch(
        "personal_review_add",
        {
            "summary": "今天完成 Redis 复盘",
            "completed": ["整理 Redis 面试表达"],
            "next_step": "明天练 RabbitMQ 选型",
        },
        runtime_context={"memory_user_scope": "user:alice"},
    )

    briefing = json.loads(
        registry.dispatch(
            "personal_briefing_generate",
            {"todo_limit": 5, "review_limit": 2},
            runtime_context={"memory_user_scope": "user:alice"},
        )
    )

    assert briefing["user_scope"] == "user:alice"
    assert briefing["suggested_focus"] == "背项目难点"
    assert [row["title"] for row in briefing["open_todos"]] == ["背项目难点"]
    assert briefing["urgent_todos"][0]["priority"] == "urgent"
    assert briefing["recent_reviews"][0]["summary"] == "今天完成 Redis 复盘"
    assert briefing["next_steps"] == ["明天练 RabbitMQ 选型"]


def test_personal_time_blocks_generate_orders_open_todos_by_priority(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "整理简历", "priority": "normal", "due_at": "2026-07-10"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "准备面试项目", "priority": "urgent", "due_at": "2026-07-08"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "看八股", "priority": "high", "due_at": "2026-07-09"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "其他人的待办", "priority": "urgent"},
        runtime_context={"memory_user_scope": "user:bob"},
    )

    plan = json.loads(
        registry.dispatch(
            "personal_time_blocks_generate",
            {"todo_limit": 6},
            runtime_context=context,
        )
    )

    assert plan["user_scope"] == "user:alice"
    assert plan["source_todo_count"] == 3
    assert plan["blocks"][0]["name"] == "上午"
    assert plan["blocks"][0]["items"][0]["title"] == "准备面试项目"
    assert plan["blocks"][1]["items"][0]["title"] == "看八股"
    assert plan["blocks"][2]["items"][0]["title"] == "整理简历"
    assert plan["first_action"] == "先处理「准备面试项目」。"
    assert plan["note"] == "这是基于未完成待办生成的建议时间块，不会自动修改待办状态。"


def test_personal_daily_workflow_combines_todos_reviews_and_time_blocks(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "复习项目难点", "priority": "urgent", "due_at": "2026-07-08"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "整理自我介绍", "priority": "normal"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_review_add",
        {
            "summary": "昨天完成 Redis 复盘",
            "completed": ["Redis 面试表达"],
            "next_step": "今天练 RabbitMQ 选型",
        },
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "其他人的待办", "priority": "urgent"},
        runtime_context={"memory_user_scope": "user:bob"},
    )

    workflow = json.loads(
        registry.dispatch(
            "personal_daily_workflow_generate",
            {"todo_limit": 6, "review_limit": 2},
            runtime_context=context,
        )
    )

    assert workflow["user_scope"] == "user:alice"
    assert workflow["current_focus"] == "复习项目难点"
    assert workflow["today_priorities"][0]["title"] == "复习项目难点"
    assert workflow["time_blocks"][0]["items"][0]["title"] == "复习项目难点"
    assert workflow["first_action"] == "先处理「复习项目难点」。"
    assert workflow["review_reminders"] == ["今天练 RabbitMQ 选型"]
    assert workflow["source"] == {
        "open_todo_count": 2,
        "urgent_todo_count": 1,
        "recent_review_count": 1,
    }
    assert workflow["note"] == "这是基于个人待办和近期复盘生成的每日工作流，不会自动完成或修改待办。"


def test_format_personal_daily_workflow_outputs_user_facing_summary(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "复习项目难点", "priority": "urgent", "due_at": "2026-07-08"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_review_add",
        {
            "summary": "昨天完成 Redis 复盘",
            "next_step": "今天练 RabbitMQ 选型",
        },
        runtime_context=context,
    )
    workflow = registry.dispatch(
        "personal_daily_workflow_generate",
        {"todo_limit": 6, "review_limit": 2},
        runtime_context=context,
    )

    formatted = registry.dispatch("format_personal_daily_workflow", {"workflow_json": workflow})

    assert "## 今日工作流" in formatted
    assert "- 当前重点：复习项目难点" in formatted
    assert "- 第一步：先处理「复习项目难点」。" in formatted
    assert "1. 复习项目难点（优先级：urgent；时间：2026-07-08）" in formatted
    assert "- 上午：复习项目难点" in formatted
    assert "- 今天练 RabbitMQ 选型" in formatted
    assert "不会自动完成或修改待办" in formatted


def test_personal_focus_card_selects_one_current_action_without_writing(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "整理简历", "priority": "normal", "due_at": "2026-07-10"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "准备 RabbitMQ 面试表达", "priority": "urgent", "due_at": "2026-07-08"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_review_add",
        {
            "summary": "昨天场景题推进慢",
            "blockers": ["系统设计题容易发散"],
            "next_step": "先把 RabbitMQ 选型讲顺",
        },
        runtime_context=context,
    )

    card = json.loads(
        registry.dispatch(
            "personal_focus_card_generate",
            {"todo_limit": 6, "review_limit": 2},
            runtime_context=context,
        )
    )

    assert card["type"] == "personal_focus_card"
    assert card["user_scope"] == "user:alice"
    assert card["focus"] == "准备 RabbitMQ 面试表达"
    assert card["focus_todo"]["priority"] == "urgent"
    assert card["why_now"] == "它是当前最高优先级事项，适合先处理，避免继续积压。"
    assert card["first_action"] == "先用 25 分钟推进「准备 RabbitMQ 面试表达」。"
    assert card["blockers"] == ["系统设计题容易发散"]
    assert card["review_next_steps"] == ["先把 RabbitMQ 选型讲顺"]
    assert card["defer"] == ["整理简历"]
    assert card["source"] == {
        "open_todo_count": 2,
        "urgent_todo_count": 1,
        "recent_review_count": 1,
    }
    assert [todo["status"] for todo in store.list_todos(user_scope="user:alice")] == ["open", "open"]


def test_personal_day_review_plan_generates_draft_without_writing(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "练习 RabbitMQ 选型", "priority": "high", "due_at": "tomorrow"},
        runtime_context=context,
    )

    plan = json.loads(
        registry.dispatch(
            "personal_day_review_plan_generate",
            {
                "today_summary": "今天完成 Redis 项目表达复盘。",
                "completed": ["整理 Redis 在 Gateway 中的作用"],
                "blockers": ["场景题还有点不稳"],
                "tomorrow_focus": "先练 RabbitMQ 选型表达",
            },
            runtime_context=context,
        )
    )

    assert plan["type"] == "personal_day_review_plan"
    assert plan["user_scope"] == "user:alice"
    assert plan["review_draft"]["summary"] == "今天完成 Redis 项目表达复盘。"
    assert plan["review_draft"]["next_step"] == "先练 RabbitMQ 选型表达"
    assert plan["tomorrow_plan"]["focus"] == "先练 RabbitMQ 选型表达"
    assert plan["tomorrow_plan"]["priority_todos"][0]["title"] == "练习 RabbitMQ 选型"
    assert "这些卡点是否需要拆成待办或求助事项？" in plan["needs_confirmation"]
    assert store.recent_reviews(user_scope="user:alice") == []
    assert store.list_todos(user_scope="user:alice")[0]["status"] == "open"


def test_personal_weekly_plan_generates_draft_without_writing(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    registry.dispatch(
        "personal_todo_add",
        {"title": "完成项目难点表达", "priority": "urgent", "due_at": "this_week"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_todo_add",
        {"title": "练三道场景题", "priority": "high", "due_at": "this_week"},
        runtime_context=context,
    )
    registry.dispatch(
        "personal_review_add",
        {
            "summary": "上周 Redis 表达更清楚了",
            "completed": ["Redis 技术栈复盘"],
            "next_step": "本周补 RabbitMQ 和系统设计表达",
        },
        runtime_context=context,
    )

    plan = json.loads(
        registry.dispatch(
            "personal_weekly_plan_generate",
            {
                "week_goal": "本周完成面试项目表达闭环",
                "focus_areas": ["项目难点", "RabbitMQ 选型", "系统设计题"],
                "constraints": ["每天晚上最多 2 小时"],
            },
            runtime_context=context,
        )
    )

    assert plan["type"] == "personal_weekly_plan"
    assert plan["user_scope"] == "user:alice"
    assert plan["week_goal"] == "本周完成面试项目表达闭环"
    assert plan["focus_areas"] == ["项目难点", "RabbitMQ 选型", "系统设计题"]
    assert plan["weekly_priorities"][0]["title"] == "完成项目难点表达"
    assert plan["milestones"][0]["done"] == "围绕「项目难点」完成至少一个可验证产出。"
    assert plan["review_signals"] == ["本周补 RabbitMQ 和系统设计表达"]
    assert plan["constraints"] == ["每天晚上最多 2 小时"]
    assert plan["first_action"] == "先推进「完成项目难点表达」。"
    assert "这些限制是否需要拆成避坑动作或求助事项？" in plan["needs_confirmation"]
    assert plan["source"] == {
        "open_todo_count": 2,
        "urgent_todo_count": 2,
        "recent_review_count": 1,
    }
    assert store.recent_reviews(user_scope="user:alice")[0]["summary"] == "上周 Redis 表达更清楚了"
    assert [todo["status"] for todo in store.list_todos(user_scope="user:alice")] == ["open", "open"]


def test_personal_inbox_triage_suggests_actions_without_writing(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}

    triage = json.loads(
        registry.dispatch(
            "personal_inbox_triage",
            {
                "text": (
                    "今天完成 Redis 复盘，但场景题还是卡点。"
                    "明天要练 RabbitMQ 选型，记一下长期目标是月底前完成面试项目表达。"
                )
            },
            runtime_context=context,
        )
    )

    assert triage["type"] == "personal_inbox_triage"
    assert triage["user_scope"] == "user:alice"
    assert triage["intent"] == "mixed"
    assert triage["suggested_todos"][0]["title"] == "要练 RabbitMQ 选型"
    assert triage["suggested_todos"][0]["due_at"] == "tomorrow"
    assert triage["suggested_review"]["blockers"] == ["但场景题还是卡点"]
    assert triage["suggested_memory"]["category"] == "personal_preference"
    assert "是否确认写入长期记忆？" in triage["needs_confirmation"]
    assert any("personal_todo_add" in action for action in triage["next_actions"])
    assert store.list_todos(user_scope="user:alice") == []
    assert store.recent_reviews(user_scope="user:alice") == []


def test_personal_inbox_triage_handles_plain_chat(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path / "workspace")
    triage = store.triage_inbox("今天有点累", user_scope="user:alice")

    assert triage["intent"] == "chat"
    assert triage["suggested_todos"] == []
    assert any("这段内容更像普通对话" in item for item in triage["needs_confirmation"])


def test_personal_inbox_commit_writes_confirmed_todos_and_review(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = PersonalStore(tmp_path / "workspace")
    register_personal_tools(registry, store)
    context = {"memory_user_scope": "user:alice"}
    triage_json = registry.dispatch(
        "personal_inbox_triage",
        {
            "text": (
                "今天完成 Redis 复盘，但场景题还是卡点。"
                "明天要练 RabbitMQ 选型，记一下长期目标是月底前完成面试项目表达。"
            )
        },
        runtime_context=context,
    )

    result = json.loads(
        registry.dispatch(
            "personal_inbox_commit",
            {"triage_json": triage_json},
            runtime_context=context,
        )
    )

    assert result["type"] == "personal_inbox_commit"
    assert result["user_scope"] == "user:alice"
    assert result["source"]["committed_todo_count"] == 1
    assert result["source"]["committed_review"] is True
    assert result["source"]["has_memory_candidate"] is True
    assert result["written_todos"][0]["title"] == "要练 RabbitMQ 选型"
    assert result["written_review"]["blockers"] == ["但场景题还是卡点"]
    assert result["skipped"][0]["type"] == "memory"
    assert "不会自动写入" in result["note"]
    assert store.list_todos(user_scope="user:alice")[0]["title"] == "要练 RabbitMQ 选型"
    assert store.recent_reviews(user_scope="user:alice")[0]["summary"].startswith("今天完成 Redis")
