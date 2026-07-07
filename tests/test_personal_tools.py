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
