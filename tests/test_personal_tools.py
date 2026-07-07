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
