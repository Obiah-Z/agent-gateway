from __future__ import annotations

import json
import time
from pathlib import Path

from agent_gateway.ai.news.models import NewsItem


class NewsDigestStore:
    """新闻简报状态存储。

    同时保存“采集过的候选条目”和“已经成功推送过的条目”，避免定时简报重复发送。
    """

    def __init__(self, root: Path, *, read_backend=None, write_backend=None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.seen_file = self.root / "seen-items.jsonl"
        self.items_file = self.root / "collected-items.jsonl"
        self.read_backend = read_backend
        self.write_backend = write_backend
        self.store_name = root.name

    def seen_ids(self) -> set[str]:
        """读取已经确认推送过的新闻 ID 集合。"""

        ids = self._seen_ids_from_backend()
        if ids:
            return ids
        return self._seen_ids_from_disk()

    def _seen_ids_from_backend(self) -> set[str]:
        """优先从外部状态仓储读取已推送条目。"""

        if self.read_backend is None:
            return set()
        try:
            rows = self.read_backend.list(
                "news_items",
                limit=5000,
                filters={"store_name": self.store_name, "state": "seen"},
            )
        except Exception:
            return set()
        ids: set[str] = set()
        for row in rows:
            item_id = str(row.get("item_id") or row.get("id") or "").strip()
            if item_id:
                ids.add(item_id)
        return ids

    def _seen_ids_from_disk(self) -> set[str]:
        """从本地 JSONL 读取已推送条目。"""

        ids: set[str] = set()
        if not self.seen_file.exists():
            return ids
        for line in self.seen_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = str(payload.get("id", "")).strip()
            if item_id:
                ids.add(item_id)
        return ids

    def filter_new(self, items: list[NewsItem]) -> list[NewsItem]:
        """过滤已推送或本轮重复的条目。"""

        seen = self.seen_ids()
        result = []
        emitted: set[str] = set()
        for item in items:
            if not item.id or item.id in seen or item.id in emitted:
                continue
            emitted.add(item.id)
            result.append(item)
        return result

    def mark_seen(self, items: list[NewsItem]) -> None:
        """把成功推送的条目标记为已读。"""

        if not items:
            return
        now = time.time()
        for item in items:
            self._write_backend_item(item, state="seen", seen_at=now, collected_at=0.0)
        with self.seen_file.open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(
                    json.dumps(
                        {
                            "id": item.id,
                            "url": item.url,
                            "source_id": item.source_id,
                            "seen_at": now,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def append_collected(self, items: list[NewsItem]) -> None:
        """把本轮采集到的原始候选条目追加落盘。"""

        if not items:
            return
        now = time.time()
        for item in items:
            self._write_backend_item(item, state="collected", seen_at=0.0, collected_at=now)
        with self.items_file.open("a", encoding="utf-8") as handle:
            for item in items:
                payload = item.to_dict()
                payload["collected_at"] = now
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_backend_item(
        self,
        item: NewsItem,
        *,
        state: str,
        seen_at: float,
        collected_at: float,
    ) -> None:
        """写入新闻状态到外部仓储。"""

        if self.write_backend is None or not item.id:
            return
        now = time.time()
        row = {
            "key": f"{self.store_name}\x1f{state}\x1f{item.id}",
            "store_name": self.store_name,
            "state": state,
            "item_id": item.id,
            "source_id": item.source_id,
            "source_type": item.source_type,
            "title": item.title,
            "url": item.url,
            "published_at": item.published_at,
            "summary": item.summary,
            "tags": list(item.tags),
            "seen_at": seen_at,
            "collected_at": collected_at,
            "updated_at": now,
            "metadata": item.to_dict(),
        }
        try:
            write_item = getattr(self.write_backend, "write_news_item", None)
            if write_item is not None:
                write_item(row)
            else:
                self.write_backend.upsert("news_items", row)
        except Exception:
            return
