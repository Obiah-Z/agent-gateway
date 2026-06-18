from pathlib import Path

from agent_gateway.intelligence.memory import MemoryStore


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
