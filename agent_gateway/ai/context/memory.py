from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


@dataclass(slots=True)
class MemorySearchResult:
    path: str
    score: float
    snippet: str


class MemoryStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.memory_file = workspace_root / "MEMORY.md"
        self.daily_dir = workspace_root / "memory" / "daily"
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def write_memory(self, content: str, category: str = "general") -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.daily_dir / f"{today}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "content": content,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return f"Memory saved to {today}.jsonl ({category})"

    def load_evergreen(self) -> str:
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8").strip()

    def get_stats(self) -> dict[str, int]:
        daily_files = list(self.daily_dir.glob("*.jsonl")) if self.daily_dir.is_dir() else []
        total_entries = 0
        for path in daily_files:
            try:
                total_entries += sum(
                    1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                )
            except OSError:
                continue
        return {
            "evergreen_chars": len(self.load_evergreen()),
            "daily_files": len(daily_files),
            "daily_entries": total_entries,
        }

    def recent_entries(self, limit: int = 20) -> list[dict[str, Any]]:
        """读取最近写入的 daily memory 条目，用于运维排查。"""

        safe_limit = max(1, min(int(limit), 200))
        rows: list[dict[str, Any]] = []
        if not self.daily_dir.is_dir():
            return rows
        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    raw = line.strip()
                    if not raw:
                        continue
                    entry = json.loads(raw)
                    rows.append(
                        {
                            "ts": str(entry.get("ts", "")),
                            "category": str(entry.get("category", "")),
                            "content": str(entry.get("content", "")),
                            "file": path.name,
                        }
                    )
            except (OSError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda row: row.get("ts", ""), reverse=True)
        return rows[:safe_limit]

    def hybrid_search(self, query: str, top_k: int = 5) -> list[MemorySearchResult]:
        chunks = self._load_all_chunks()
        if not chunks:
            return []
        keyword_results = self._keyword_search(query, chunks, top_k=10)
        vector_results = self._vector_search(query, chunks, top_k=10)
        merged = self._merge_hybrid_results(vector_results, keyword_results)
        decayed = self._temporal_decay(merged)
        reranked = self._mmr_rerank(decayed)
        results: list[MemorySearchResult] = []
        for row in reranked[:top_k]:
            snippet = row["chunk"]["text"]
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            results.append(
                MemorySearchResult(
                    path=row["chunk"]["path"],
                    score=round(row["score"], 4),
                    snippet=snippet,
                )
            )
        return results

    def format_results(self, results: list[MemorySearchResult]) -> str:
        if not results:
            return "No relevant memories found."
        return "\n".join(
            f"[{result.path}] (score: {result.score}) {result.snippet}"
            for result in results
        )

    def auto_recall(self, query: str, top_k: int = 3) -> str:
        results = self.hybrid_search(query, top_k=top_k)
        if not results:
            return ""
        return "\n".join(f"- [{result.path}] {result.snippet}" for result in results)

    def _load_all_chunks(self) -> list[dict[str, str]]:
        chunks: list[dict[str, str]] = []
        evergreen = self.load_evergreen()
        if evergreen:
            for paragraph in evergreen.split("\n\n"):
                text = paragraph.strip()
                if text:
                    chunks.append({"path": "MEMORY.md", "text": text})

        if self.daily_dir.is_dir():
            for path in sorted(self.daily_dir.glob("*.jsonl")):
                try:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        row = line.strip()
                        if not row:
                            continue
                        entry = json.loads(row)
                        content = entry.get("content", "")
                        if not content:
                            continue
                        category = entry.get("category", "")
                        label = f"{path.name} [{category}]" if category else path.name
                        chunks.append({"path": label, "text": content})
                except (OSError, json.JSONDecodeError):
                    continue
        return chunks

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower())
        return [token for token in tokens if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]

    @staticmethod
    def _hash_vector(text: str, dim: int = 64) -> list[float]:
        tokens = MemoryStore._tokenize(text)
        vector = [0.0] * dim
        for token in tokens:
            hashed = hash(token)
            for index in range(dim):
                bit = (hashed >> (index % 62)) & 1
                vector[index] += 1.0 if bit else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def _vector_cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    @staticmethod
    def _jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
        set_a, set_b = set(tokens_a), set(tokens_b)
        union = len(set_a | set_b)
        if union == 0:
            return 0.0
        return len(set_a & set_b) / union

    def _keyword_search(
        self,
        query: str,
        chunks: list[dict[str, str]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        chunk_tokens = [self._tokenize(chunk["text"]) for chunk in chunks]
        n = len(chunks)
        df: dict[str, int] = {}
        for tokens in chunk_tokens:
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1

        def tfidf(tokens: list[str]) -> dict[str, float]:
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            return {
                token: count * (math.log((n + 1) / (df.get(token, 0) + 1)) + 1)
                for token, count in tf.items()
            }

        def cosine(a: dict[str, float], b: dict[str, float]) -> float:
            common = set(a) & set(b)
            if not common:
                return 0.0
            dot = sum(a[key] * b[key] for key in common)
            na = math.sqrt(sum(value * value for value in a.values()))
            nb = math.sqrt(sum(value * value for value in b.values()))
            return dot / (na * nb) if na and nb else 0.0

        qvec = tfidf(query_tokens)
        scored = []
        for index, tokens in enumerate(chunk_tokens):
            if not tokens:
                continue
            score = cosine(qvec, tfidf(tokens))
            if score > 0.0:
                scored.append({"chunk": chunks[index], "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _vector_search(
        self,
        query: str,
        chunks: list[dict[str, str]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        query_vector = self._hash_vector(query)
        scored = []
        for chunk in chunks:
            chunk_vector = self._hash_vector(chunk["text"])
            score = self._vector_cosine(query_vector, chunk_vector)
            if score > 0.0:
                scored.append({"chunk": chunk, "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _merge_hybrid_results(
        vector_results: list[dict[str, Any]],
        keyword_results: list[dict[str, Any]],
        *,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for row in vector_results:
            key = row["chunk"]["text"][:100]
            merged[key] = {"chunk": row["chunk"], "score": row["score"] * vector_weight}
        for row in keyword_results:
            key = row["chunk"]["text"][:100]
            if key in merged:
                merged[key]["score"] += row["score"] * text_weight
            else:
                merged[key] = {"chunk": row["chunk"], "score": row["score"] * text_weight}
        result = list(merged.values())
        result.sort(key=lambda item: item["score"], reverse=True)
        return result

    @staticmethod
    def _temporal_decay(
        results: list[dict[str, Any]],
        decay_rate: float = 0.01,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        for row in results:
            path = row["chunk"].get("path", "")
            age_days = 0.0
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path)
            if match:
                try:
                    chunk_date = datetime.strptime(match.group(1), "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    age_days = (now - chunk_date).total_seconds() / 86400.0
                except ValueError:
                    age_days = 0.0
            row["score"] *= math.exp(-decay_rate * age_days)
        return results

    @staticmethod
    def _mmr_rerank(
        results: list[dict[str, Any]],
        lambda_param: float = 0.7,
    ) -> list[dict[str, Any]]:
        if len(results) <= 1:
            return results
        tokenized = [MemoryStore._tokenize(row["chunk"]["text"]) for row in results]
        selected: list[int] = []
        remaining = list(range(len(results)))
        reranked: list[dict[str, Any]] = []

        while remaining:
            best_index = -1
            best_score = float("-inf")
            for index in remaining:
                relevance = results[index]["score"]
                max_similarity = 0.0
                for selected_index in selected:
                    similarity = MemoryStore._jaccard_similarity(
                        tokenized[index], tokenized[selected_index]
                    )
                    if similarity > max_similarity:
                        max_similarity = similarity
                mmr = lambda_param * relevance - (1 - lambda_param) * max_similarity
                if mmr > best_score:
                    best_score = mmr
                    best_index = index
            selected.append(best_index)
            remaining.remove(best_index)
            reranked.append(results[best_index])
        return reranked


def register_memory_tools(registry: ToolRegistry, memory_store: MemoryStore) -> None:
    registry.register(
        RegisteredTool(
            name="memory_write",
            description="Save an important fact or observation to long-term memory.",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["content"],
            },
            handler=lambda content, category="general": memory_store.write_memory(
                content, category
            ),
            tags=("memory", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="memory_search",
            description="Search stored memories for relevant information.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, top_k=5: memory_store.format_results(
                memory_store.hybrid_search(query, top_k=top_k)
            ),
            tags=("memory", "read"),
        )
    )
