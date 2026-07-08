from pathlib import Path

from agent_gateway.ai.context.memory import MemoryStore, register_memory_tools
from agent_gateway.ai.tools.registry import ToolRegistry


def test_memory_store_write_and_recall(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("User prefers Python over JavaScript.", encoding="utf-8")

    store = MemoryStore(workspace)
    store.write_memory("User likes concise answers.", "preference")
    results = store.hybrid_search("python preference", top_k=5)

    assert results
    joined = " ".join(result.snippet for result in results)
    assert "Python" in joined or "concise" in joined


def test_format_memory_write_outputs_user_facing_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = MemoryStore(workspace)
    registry = ToolRegistry()
    register_memory_tools(registry, store)

    result = registry.dispatch(
        "memory_write",
        {"content": "用户长期目标是月底前完成面试项目表达。", "category": "personal_goal"},
        runtime_context={"memory_user_scope": "user:alice"},
    )
    formatted = registry.dispatch(
        "format_memory_write",
        {
            "result_text": result,
            "content": "用户长期目标是月底前完成面试项目表达。",
            "category": "personal_goal",
        },
    )

    assert "## 长期记忆已保存" in formatted
    assert "- 分类：personal_goal" in formatted
    assert "- 范围：user:alice" in formatted
    assert "- 位置：" in formatted
    assert "- 用户长期目标是月底前完成面试项目表达。" in formatted
    assert "不会自动新增待办、餐食、体重、复盘或修改档案" in formatted


def test_memory_store_lists_recent_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    store = MemoryStore(workspace)
    store.write_memory("first memory", "general")
    store.write_memory("second memory", "preference")

    rows = store.recent_entries(limit=1)

    assert len(rows) == 1
    assert rows[0]["content"] == "second memory"
    assert rows[0]["category"] == "preference"
    assert rows[0]["file"].endswith(".jsonl")


def test_memory_store_isolates_daily_memory_by_user_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    store = MemoryStore(workspace)
    store.write_memory("Alice likes oats for breakfast.", "preference", user_scope="user:alice")
    store.write_memory("Bob prefers rice for lunch.", "preference", user_scope="user:bob")

    alice_results = store.hybrid_search("oats breakfast", top_k=3, user_scope="user:alice")
    bob_results = store.hybrid_search("oats breakfast", top_k=3, user_scope="user:bob")
    recent = store.recent_entries(limit=10)

    assert any("Alice likes oats" in result.snippet for result in alice_results)
    assert not any("Alice likes oats" in result.snippet for result in bob_results)
    assert (workspace / "memory" / "users" / "user_alice" / "daily").is_dir()
    assert (workspace / "memory" / "users" / "user_bob" / "daily").is_dir()
    assert {row["user_scope"] for row in recent} == {"user:alice", "user:bob"}


def test_memory_store_hybrid_search_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
            assert table == "memory_entries"
            return [
                {
                    "content": "PostgreSQL memory recall should be used first.",
                    "category": "database",
                    "source_file": "postgres",
                }
            ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("Local only content.", encoding="utf-8")
    store = MemoryStore(workspace)
    store.read_backend = FakeReadBackend()

    results = store.hybrid_search("PostgreSQL recall", top_k=3)

    assert results
    assert "PostgreSQL memory recall" in results[0].snippet
    assert results[0].path == "postgres [database]"


def test_memory_store_filters_backend_rows_by_user_scope(tmp_path: Path) -> None:
    class FakeReadBackend:
        def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
            assert table == "memory_entries"
            return [
                {
                    "content": "Alice database memory.",
                    "category": "database",
                    "source_file": "postgres",
                    "metadata": {"user_scope": "user:alice"},
                },
                {
                    "content": "Bob database memory.",
                    "category": "database",
                    "source_file": "postgres",
                    "metadata": {"user_scope": "user:bob"},
                },
            ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = MemoryStore(workspace)
    store.read_backend = FakeReadBackend()

    results = store.hybrid_search("database memory", top_k=5, user_scope="user:alice")

    snippets = " ".join(result.snippet for result in results)
    assert "Alice database memory" in snippets
    assert "Bob database memory" not in snippets


def test_memory_store_stats_prefers_read_backend(tmp_path: Path) -> None:
    class FakeReadBackend:
        def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
            assert table == "memory_entries"
            return [
                {"content": "first", "source_file": "2026-06-28.jsonl"},
                {"content": "second", "source_file": "2026-06-28.jsonl"},
                {"content": "third", "source_file": "2026-06-29.jsonl"},
            ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("Evergreen", encoding="utf-8")
    store = MemoryStore(workspace)
    store.write_memory_to_disk("local fallback only", "general")
    store.read_backend = FakeReadBackend()

    stats = store.get_stats()

    assert stats == {
        "evergreen_chars": len("Evergreen"),
        "daily_files": 2,
        "daily_entries": 3,
    }
