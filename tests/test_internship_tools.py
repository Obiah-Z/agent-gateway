import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_gateway.ai.context.internship import InternshipStore, register_internship_tools
from agent_gateway.ai.tools.registry import ToolRegistry


def test_internship_store_isolates_logs_by_user_scope(tmp_path: Path) -> None:
    store = InternshipStore(tmp_path / "workspace")

    alice = store.add_log(
        "完成接口联调",
        "和后端确认字段映射，跑通 happy path",
        log_date="2026-07-13",
        project="Gateway",
        user_scope="user:alice",
    )
    store.add_log("整理日报", "补充当天学习记录", user_scope="user:bob")

    assert store.list_logs(user_scope="user:alice")[0]["id"] == alice["id"]
    assert store.list_logs(user_scope="user:bob")[0]["title"] == "整理日报"


def test_internship_log_add_uses_runtime_user_scope(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = InternshipStore(tmp_path / "workspace")
    register_internship_tools(registry, store)

    result = json.loads(
        registry.dispatch(
            "internship_log_add",
            {
                "title": "排查企微回调重复发送",
                "content": "定位到同一消息被多个回复路径处理",
                "category": "task",
                "project": "Agent Gateway",
                "tags": ["wework", "debug"],
            },
            runtime_context={"memory_user_scope": "user:wework:wework-main:direct:zhanghaibo"},
        )
    )

    rows = store.list_logs(user_scope="user:wework:wework-main:direct:zhanghaibo")
    assert result["title"] == "排查企微回调重复发送"
    assert rows[0]["id"] == result["id"]
    assert rows[0]["tags"] == ["wework", "debug"]


def test_internship_default_log_date_uses_shanghai_date(tmp_path: Path) -> None:
    store = InternshipStore(tmp_path / "workspace")

    row = store.add_log("记录默认日期", "用于验证企业微信日粒度记录", user_scope="user:zhanghaibo")

    assert row["log_date"] == datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def test_internship_log_list_and_search_are_scoped(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = InternshipStore(tmp_path / "workspace")
    register_internship_tools(registry, store)
    context = {"memory_user_scope": "user:zhanghaibo"}

    registry.dispatch(
        "internship_log_add",
        {
            "title": "RabbitMQ 消费确认学习",
            "content": "理解 ack、nack 和重试边界",
            "category": "learning",
            "log_date": "2026-07-13",
        },
        runtime_context=context,
    )
    store.add_log("RabbitMQ 其他用户记录", "不应被搜到", user_scope="user:other")

    listed = json.loads(
        registry.dispatch(
            "internship_log_list",
            {"log_date": "2026-07-13"},
            runtime_context=context,
        )
    )
    searched = json.loads(
        registry.dispatch(
            "internship_log_search",
            {"query": "ack"},
            runtime_context=context,
        )
    )

    assert listed["count"] == 1
    assert listed["items"][0]["title"] == "RabbitMQ 消费确认学习"
    assert searched["count"] == 1
    assert searched["items"][0]["content"] == "理解 ack、nack 和重试边界"


def test_internship_log_search_scans_history_beyond_newest_100(tmp_path: Path) -> None:
    store = InternshipStore(tmp_path / "workspace")

    store.add_log(
        "早期唯一匹配记录",
        "这个历史记录包含 only-old-match",
        log_date="2026-01-01",
        user_scope="user:zhanghaibo",
    )
    for index in range(120):
        store.add_log(
            f"近期普通记录 {index}",
            "不包含目标关键词",
            log_date="2026-07-13",
            user_scope="user:zhanghaibo",
        )

    results = store.search_logs("only-old-match", user_scope="user:zhanghaibo")

    assert len(results) == 1
    assert results[0]["title"] == "早期唯一匹配记录"


def test_internship_daily_report_groups_records(tmp_path: Path) -> None:
    registry = ToolRegistry()
    store = InternshipStore(tmp_path / "workspace")
    register_internship_tools(registry, store)
    context = {"memory_user_scope": "user:zhanghaibo"}

    for payload in [
        {
            "title": "完成接口联调",
            "content": "跑通创建记录流程",
            "category": "task",
            "project": "Gateway",
            "next_actions": ["补充异常用例"],
        },
        {
            "title": "学习企业微信文件上传",
            "content": "理解 media_id 和消息发送链路",
            "category": "learning",
            "project": "Gateway",
        },
        {
            "title": "卡在重复回复",
            "content": "需要确认事件去重边界",
            "category": "blocker",
            "project": "Gateway",
        },
    ]:
        registry.dispatch(
            "internship_log_add",
            {**payload, "log_date": "2026-07-13"},
            runtime_context=context,
        )

    report = json.loads(
        registry.dispatch(
            "internship_daily_report_generate",
            {"log_date": "2026-07-13"},
            runtime_context=context,
        )
    )
    formatted = registry.dispatch(
        "format_internship_daily_report",
        {"report_json": json.dumps(report, ensure_ascii=False)},
    )

    assert report["record_count"] == 3
    assert any("完成接口联调" in item for item in report["completed"])
    assert any("企业微信文件上传" in item for item in report["learnings"])
    assert any("重复回复" in item for item in report["blockers"])
    assert report["next_actions"] == ["补充异常用例"]
    assert "## 实习日报草稿｜2026-07-13" in formatted
