from __future__ import annotations

import json
from pathlib import Path

from agent_gateway.ai.news.models import NewsCollectionResult, NewsItem, NewsSourceConfig
from agent_gateway.ai.news.sources import NewsSourceClient, parse_source_datetime
from agent_gateway.ai.news.store import NewsDigestStore


class NewsCollector:
    def __init__(
        self,
        sources_file: Path,
        store: NewsDigestStore,
        *,
        client: NewsSourceClient | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.sources_file = sources_file
        self.store = store
        self.client = client or NewsSourceClient(timeout_seconds=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def load_sources(self) -> list[NewsSourceConfig]:
        if not self.sources_file.exists():
            return []
        try:
            payload = json.loads(self.sources_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = []
        for row in payload.get("sources", []):
            if not isinstance(row, dict):
                continue
            source = NewsSourceConfig.from_dict(row)
            if source.id and source.type and source.enabled:
                rows.append(source)
        return rows

    def collect(
        self,
        *,
        lookback_hours: int,
        max_items: int,
        per_source_max_items: int = 5,
    ) -> NewsCollectionResult:
        collected: list[NewsItem] = []
        errors: list[str] = []
        for source in self.load_sources():
            try:
                items = self.client.collect(
                    source,
                    lookback_hours=lookback_hours,
                    max_items=min(per_source_max_items, source.max_results),
                )
            except Exception as exc:
                errors.append(f"{source.id}: {type(exc).__name__}: {exc}")
                continue
            collected.extend(items)

        collected = _sort_items(collected)
        self.store.append_collected(collected)
        fresh = self.store.filter_new(collected)
        return NewsCollectionResult(items=fresh[: max(1, max_items)], errors=errors)


def _sort_items(items: list[NewsItem]) -> list[NewsItem]:
    def key(item: NewsItem) -> tuple[int, float]:
        source_rank = {
            "rss": 0,
            "html_page": 1,
            "github_releases": 2,
            "arxiv": 3,
        }.get(item.source_type, 9)
        parsed = parse_source_datetime(item.published_at)
        timestamp = parsed.timestamp() if parsed is not None else 0.0
        return (source_rank, -timestamp)

    return sorted(items, key=key)
