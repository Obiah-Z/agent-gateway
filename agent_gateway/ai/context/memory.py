from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry
from agent_gateway.runtime.user_scope import canonicalize_user_scope


@dataclass(slots=True)
class MemorySearchResult:
    """单条记忆检索结果。"""

    path: str
    score: float
    snippet: str


class MemoryStore:
    """记忆存储与检索入口。

    同时管理长期记忆 `MEMORY.md` 和按天滚动的 `memory/daily/*.jsonl`，并提供
    写入、统计、召回和混合检索能力。
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.memory_file = workspace_root / "MEMORY.md"
        self.daily_dir = workspace_root / "memory" / "daily"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.migration_root = workspace_root / "_migration"
        self.backup_sink = None
        self.read_backend: Any | None = None
        self.write_backend: Any | None = None

    def write_memory(self, content: str, category: str = "general", *, user_scope: str = "") -> str:
        """把一条新记忆追加到当天的 daily memory 文件。"""

        if self._is_system_scope(user_scope):
            return "Memory write skipped (system task scope)"
        normalized_scope = self.normalize_scope(user_scope)
        self._write_primary(content, category, user_scope=normalized_scope)
        self.write_memory_to_disk(content, category=category, user_scope=normalized_scope)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scope_suffix = f" scope={normalized_scope}" if normalized_scope else " scope=global"
        return f"Memory saved to {today}.jsonl ({category}, {scope_suffix})"

    def write_memory_to_disk(
        self,
        content: str,
        category: str = "general",
        *,
        user_scope: str = "",
    ) -> None:
        """仅写入本地 daily memory，不触发备份镜像。"""

        if self._is_system_scope(user_scope):
            return
        normalized_scope = self.normalize_scope(user_scope)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self._daily_dir_for_scope(normalized_scope) / f"{today}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "content": content,
            "user_scope": normalized_scope,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _mirror(self, content: str, category: str, *, user_scope: str = "") -> None:
        """把记忆镜像到备份 sink。"""

        sink = getattr(self, "backup_sink", None)
        if sink is None:
            return
        method = getattr(sink, "write_memory", None)
        if method is None:
            return
        try:
            method(content, category=category, user_scope=user_scope)
        except TypeError:
            try:
                method(content, category=category)
            except Exception:
                pass
        except Exception:
            pass

    def _write_primary(self, content: str, category: str, *, user_scope: str = "") -> None:
        """优先写入数据库主存储；不可用时退回备份 sink。"""

        backend = getattr(self, "write_backend", None)
        if backend is not None:
            method = getattr(backend, "write_memory", None)
            if method is not None:
                try:
                    method(content, category=category, user_scope=user_scope)
                    return
                except TypeError:
                    try:
                        method(content, category=category)
                        return
                    except Exception:
                        pass
                except Exception:
                    pass
        self._mirror(content, category, user_scope=user_scope)

    def write_memory_migration(
        self,
        content: str,
        category: str = "general",
        *,
        user_scope: str = "",
    ) -> None:
        """把记忆写入迁移专用目录。"""

        normalized_scope = self.normalize_scope(user_scope)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.migration_root / self._scope_dir_name(normalized_scope) / "daily" / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "content": content,
            "user_scope": normalized_scope,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load_evergreen(self) -> str:
        """读取长期记忆文件 `MEMORY.md`。"""

        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8").strip()

    def get_stats(self) -> dict[str, int]:
        """返回长期记忆和日记忆的体量统计。"""

        backend_stats = self._get_stats_from_backend()
        if backend_stats is not None:
            return backend_stats
        return self._get_stats_from_disk()

    def _get_stats_from_backend(self) -> dict[str, int] | None:
        """优先从 PostgreSQL memory_entries 统计记忆体量。"""

        backend = self.read_backend
        if backend is None:
            return None
        try:
            rows = backend.list("memory_entries", limit=2000)
        except Exception:
            return None
        if not rows:
            return None
        source_files = {
            str(row.get("source_file", "")).strip()
            for row in rows
            if str(row.get("source_file", "")).strip()
        }
        return {
            "evergreen_chars": len(self.load_evergreen()),
            "daily_files": len(source_files),
            "daily_entries": len(rows),
        }

    def _get_stats_from_disk(self) -> dict[str, int]:
        """从本地 daily JSONL 统计记忆体量，作为数据库不可用时的兜底。"""

        daily_files = self._iter_daily_files()
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
        if self.read_backend is not None:
            try:
                rows = self.read_backend.list("memory_entries", limit=safe_limit)
                if rows:
                    return rows[:safe_limit]
            except Exception:
                pass
        rows: list[dict[str, Any]] = []
        daily_files = self._iter_daily_files()
        if not daily_files:
            return rows
        for path in sorted(daily_files, reverse=True):
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
                            "user_scope": str(entry.get("user_scope", "")),
                            "file": path.name,
                            "path": str(path.relative_to(self.workspace_root)),
                        }
                    )
            except (OSError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda row: row.get("ts", ""), reverse=True)
        return rows[:safe_limit]

    def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_scope: str = "",
    ) -> list[MemorySearchResult]:
        """执行混合检索。

        先做关键词检索和哈希向量检索，再合并分数、做时间衰减和去冗余重排。
        """

        chunks = self._load_all_chunks(user_scope=self.normalize_scope(user_scope))
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
        """把检索结果格式化成适合直接注入 prompt 的文本。"""

        if not results:
            return "No relevant memories found."
        return "\n".join(
            f"[{result.path}] (score: {result.score}) {result.snippet}"
            for result in results
        )

    def auto_recall(self, query: str, top_k: int = 3, *, user_scope: str = "") -> str:
        """为一次对话自动召回最相关的记忆片段。"""

        results = self.hybrid_search(query, top_k=top_k, user_scope=user_scope)
        if not results:
            return ""
        return "\n".join(f"- [{result.path}] {result.snippet}" for result in results)

    def _load_all_chunks(self, *, user_scope: str = "") -> list[dict[str, str]]:
        """把长期记忆和日记忆切成统一检索块。"""

        backend_chunks = self._load_chunks_from_backend(user_scope=user_scope)
        if backend_chunks:
            return backend_chunks
        return self._load_chunks_from_disk(user_scope=user_scope)

    def _load_chunks_from_backend(self, *, user_scope: str = "") -> list[dict[str, str]]:
        """优先从 PostgreSQL memory_entries 构造检索块。"""

        backend = self.read_backend
        if backend is None:
            return []
        try:
            rows = backend.list("memory_entries", limit=2000)
        except Exception:
            return []
        chunks: list[dict[str, str]] = []
        for row in rows:
            if not self._row_matches_scope(row, user_scope):
                continue
            content = str(row.get("content", "")).strip()
            if not content:
                continue
            category = str(row.get("category", "")).strip()
            source_file = str(row.get("source_file", "")).strip() or "postgres"
            label = f"{source_file} [{category}]" if category else source_file
            chunks.append({"path": label, "text": content})
        return chunks

    def _load_chunks_from_disk(self, *, user_scope: str = "") -> list[dict[str, str]]:
        """从本地 MEMORY.md 和 daily JSONL 构造检索块，作为数据库不可用时的兜底。"""

        chunks: list[dict[str, str]] = []
        evergreen = self.load_evergreen()
        if evergreen:
            for paragraph in evergreen.split("\n\n"):
                text = paragraph.strip()
                if text:
                    chunks.append({"path": "MEMORY.md", "text": text})

        daily_dir = self._daily_dir_for_scope(user_scope)
        if daily_dir.is_dir():
            for path in sorted(daily_dir.glob("*.jsonl")):
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
    def normalize_scope(user_scope: str) -> str:
        """规范化记忆作用域，空值代表全局记忆。"""

        return canonicalize_user_scope(user_scope)

    @staticmethod
    def _is_system_scope(user_scope: str) -> bool:
        return str(user_scope or "").strip().startswith("system:")

    @staticmethod
    def _scope_dir_name(user_scope: str) -> str:
        if not user_scope:
            return "global"
        slug = re.sub(r"[^a-zA-Z0-9._=-]+", "_", user_scope.strip())
        slug = slug.strip("._-")[:160]
        return slug or "global"

    def _daily_dir_for_scope(self, user_scope: str) -> Path:
        if not user_scope:
            return self.daily_dir
        path = self.workspace_root / "memory" / "users" / self._scope_dir_name(user_scope) / "daily"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _iter_daily_files(self) -> list[Path]:
        files: list[Path] = []
        if self.daily_dir.is_dir():
            files.extend(self.daily_dir.glob("*.jsonl"))
        users_root = self.workspace_root / "memory" / "users"
        if users_root.is_dir():
            files.extend(users_root.glob("*/daily/*.jsonl"))
        return list(files)

    @staticmethod
    def _row_matches_scope(row: dict[str, Any], user_scope: str) -> bool:
        metadata = row.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        row_scope = str(metadata.get("user_scope") or row.get("user_scope") or "").strip()
        return row_scope == user_scope

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """把中英文文本切成轻量 token 序列。"""

        tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower())
        return [token for token in tokens if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]

    @staticmethod
    def _hash_vector(text: str, dim: int = 64) -> list[float]:
        """生成无需外部模型的哈希向量表示。"""

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
        """计算两个哈希向量的余弦相似度。"""

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    @staticmethod
    def _jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
        """用 token 集合重合度衡量两个记忆块是否过于相似。"""

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
        """基于 TF-IDF 风格分数做关键词检索。"""

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
        normalized_query = query.strip().lower()
        scored = []
        for index, tokens in enumerate(chunk_tokens):
            if not tokens:
                continue
            score = cosine(qvec, tfidf(tokens))
            # 中文短语常被正则切成整段长 token；直接子串命中应稳定返回。
            if normalized_query and normalized_query in chunks[index]["text"].lower():
                score = max(score, 1.0)
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
        """基于本地哈希向量做近似语义检索。"""

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
        """合并关键词和向量检索结果。"""

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
        """对较旧的 daily memory 做轻度时间衰减。"""

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
        """用 MMR 重排，减少返回结果之间的重复内容。"""

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
    def memory_write_handler(
        content: str,
        category: str = "general",
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = user_scope or str((__runtime_context or {}).get("memory_user_scope", ""))
        return memory_store.write_memory(content, category, user_scope=scope)

    def format_memory_write_handler(
        result_text: str,
        content: str = "",
        category: str = "",
    ) -> str:
        if not result_text.strip():
            return "Error: result_text is required"
        match = re.search(
            r"Memory saved to (?P<file>[^ ]+) \((?P<category>[^,]+),\s*scope=(?P<scope>[^)]+)\)",
            result_text.strip(),
        )
        saved_file = match.group("file") if match else "记忆文件"
        saved_category = category.strip() or (match.group("category").strip() if match else "general")
        saved_scope = match.group("scope").strip() if match else "当前用户"
        content_preview = " ".join(content.strip().split())
        if len(content_preview) > 120:
            content_preview = content_preview[:117].rstrip() + "..."

        sections = [
            "## 长期记忆已保存",
            f"- 分类：{saved_category}",
            f"- 范围：{saved_scope}",
            f"- 位置：{saved_file}",
            "",
            "## 保存内容",
            f"- {content_preview}" if content_preview else "- 已保存用户确认的长期信息。",
            "",
            "> 边界：这只是长期记忆保存确认，不会自动新增待办、餐食、体重、复盘或修改档案。",
        ]
        return "\n".join(sections).strip()

    def memory_search_handler(
        query: str,
        top_k: int = 5,
        *,
        user_scope: str = "",
        __runtime_context: dict[str, Any] | None = None,
    ) -> str:
        scope = user_scope or str((__runtime_context or {}).get("memory_user_scope", ""))
        return memory_store.format_results(
            memory_store.hybrid_search(query, top_k=top_k, user_scope=scope)
        )

    def format_memory_search_handler(results_text: str, query: str = "") -> str:
        if not results_text.strip():
            return "Error: results_text is required"
        if results_text.strip() == "No relevant memories found.":
            query_line = f"- 查询：{query.strip()}" if query.strip() else "- 查询：未提供"
            return "\n".join(
                [
                    "## 长期记忆检索",
                    query_line,
                    "",
                    "## 结果",
                    "- 暂未找到相关长期记忆。",
                    "",
                    "> 边界：这是长期记忆检索结果，只读取已保存记忆，不会新增、修改或删除任何内容。",
                ]
            )

        memory_lines = []
        for line in results_text.splitlines():
            text = line.strip()
            if not text:
                continue
            match = re.match(r"^\[(?P<path>.+)\]\s+\(score:\s*(?P<score>[^)]+)\)\s*(?P<snippet>.*)$", text)
            if match:
                snippet = " ".join(match.group("snippet").strip().split())
                if len(snippet) > 140:
                    snippet = snippet[:137].rstrip() + "..."
                memory_lines.append(f"- {snippet}（来源：{match.group('path')}，相关度：{match.group('score')}）")
            else:
                memory_lines.append(f"- {text}")

        sections = [
            "## 长期记忆检索",
            f"- 查询：{query.strip()}" if query.strip() else "- 查询：未提供",
            f"- 命中：{len(memory_lines)} 条",
            "",
            "## 结果",
            "\n".join(memory_lines) if memory_lines else "- 暂未找到相关长期记忆。",
            "",
            "> 边界：这是长期记忆检索结果，只读取已保存记忆，不会新增、修改或删除任何内容。",
        ]
        return "\n".join(sections).strip()

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
            handler=memory_write_handler,
            tags=("memory", "write"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_memory_write",
            description=(
                "Format a memory_write confirmation string into a concise Chinese "
                "Markdown long-term memory save confirmation for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["result_text"],
                "properties": {
                    "result_text": {
                        "type": "string",
                        "description": "Text returned by memory_write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Original memory content that was saved.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Original memory category.",
                    },
                },
            },
            handler=format_memory_write_handler,
            tags=("memory", "write", "format", "user-facing"),
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
            handler=memory_search_handler,
            tags=("memory", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="format_memory_search",
            description=(
                "Format memory_search results into a concise Chinese Markdown "
                "long-term memory lookup summary for chat replies."
            ),
            input_schema={
                "type": "object",
                "required": ["results_text"],
                "properties": {
                    "results_text": {
                        "type": "string",
                        "description": "Text returned by memory_search.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Original memory search query.",
                    },
                },
            },
            handler=format_memory_search_handler,
            tags=("memory", "read", "format", "user-facing"),
        )
    )
