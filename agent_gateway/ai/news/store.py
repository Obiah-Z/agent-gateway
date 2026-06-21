from __future__ import annotations

import json
import time
from pathlib import Path

from agent_gateway.ai.news.models import NewsItem


class NewsDigestStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.seen_file = self.root / "seen-items.jsonl"
        self.items_file = self.root / "collected-items.jsonl"

    def seen_ids(self) -> set[str]:
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
        if not items:
            return
        now = time.time()
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
        if not items:
            return
        now = time.time()
        with self.items_file.open("a", encoding="utf-8") as handle:
            for item in items:
                payload = item.to_dict()
                payload["collected_at"] = now
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
