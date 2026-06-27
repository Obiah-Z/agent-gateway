from pathlib import Path

from agent_gateway.ai.context.memory import MemoryStore


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
