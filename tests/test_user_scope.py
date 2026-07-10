from pathlib import Path

from agent_gateway.ai.context.diet import DietStore
from agent_gateway.ai.context.memory import MemoryStore
from agent_gateway.ai.context.personal import PersonalStore
from agent_gateway.runtime.execution.loop import memory_scope_from_session_key
from agent_gateway.runtime.user_scope import canonicalize_user_scope


CANONICAL_ZHANGHAIBO = "user:wework:wework-main:direct:zhanghaibo"


def test_memory_scope_canonicalizes_wework_session_key() -> None:
    assert (
        memory_scope_from_session_key(
            "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:ZhangHaiBo"
        )
        == CANONICAL_ZHANGHAIBO
    )


def test_memory_scope_aliases_legacy_direct_session_key() -> None:
    assert memory_scope_from_session_key("agent:research:direct:zhanghaibo") == CANONICAL_ZHANGHAIBO
    assert canonicalize_user_scope("user:direct:zhanghaibo") == CANONICAL_ZHANGHAIBO


def test_system_sessions_do_not_create_user_scope() -> None:
    assert memory_scope_from_session_key("system:cron:health-check") == ""
    assert memory_scope_from_session_key("system:heartbeat:research") == ""


def test_orchestration_sessions_do_not_create_user_scope() -> None:
    assert (
        memory_scope_from_session_key(
            "orchestration:5adfa5751b41:controller:personal-secretary-zhanghaibo"
        )
        == ""
    )
    assert canonicalize_user_scope(
        "orchestration:5adfa5751b41:controller:personal-secretary-zhanghaibo"
    ) == ""


def test_stores_share_canonical_user_scope_for_legacy_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    memory_store = MemoryStore(workspace)
    memory_store.write_memory("用户偏好统一 scope。", "test", user_scope="user:direct:zhanghaibo")
    assert (
        workspace
        / "memory"
        / "users"
        / "user_wework_wework-main_direct_zhanghaibo"
        / "daily"
    ).is_dir()

    diet_store = DietStore(workspace)
    diet_store.update_profile("user:direct:zhanghaibo", height_cm=166)
    assert diet_store.get_profile(CANONICAL_ZHANGHAIBO)["height_cm"] == 166

    personal_store = PersonalStore(workspace)
    personal_store.add_todo("统一用户 scope", user_scope="user:direct:zhanghaibo")
    assert personal_store.list_todos(user_scope=CANONICAL_ZHANGHAIBO)[0]["title"] == "统一用户 scope"


def test_memory_store_skips_transient_scope_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_store = MemoryStore(workspace)

    for scope in (
        "system:cron:health-check",
        "orchestration:5adfa5751b41:controller:personal-secretary-zhanghaibo",
    ):
        result = memory_store.write_memory(
            "临时任务不应该写入用户记忆。",
            "test",
            user_scope=scope,
        )

        assert result == "Memory write skipped (transient task scope)"
    assert not (workspace / "memory" / "users").exists()
