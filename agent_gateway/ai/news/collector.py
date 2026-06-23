from __future__ import annotations

import json
from pathlib import Path

from agent_gateway.ai.news.models import NewsCollectionResult, NewsItem, NewsSourceConfig
from agent_gateway.ai.news.sources import NewsSourceClient, parse_source_datetime
from agent_gateway.ai.news.store import NewsDigestStore


class NewsCollector:
    """新闻采集编排器。

    负责读取来源配置、调用具体 source client、去重排序，并结合 store 过滤已推送条目。
    """

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
        """关闭内部 client；仅在 collector 自己创建 client 时生效。"""

        if self._owns_client:
            self.client.close()

    def load_sources(self) -> list[NewsSourceConfig]:
        """从 sources 文件加载有效且启用的新闻源。"""

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
        """执行一轮新闻采集，并返回去重后的新鲜条目。"""

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
        collected = _dedupe_by_url(collected)
        self.store.append_collected(collected)
        fresh = self.store.filter_new(collected)
        return NewsCollectionResult(items=fresh[: max(1, max_items)], errors=errors)


def _sort_items(items: list[NewsItem]) -> list[NewsItem]:
    """按来源优先级和发布时间排序。"""

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


def _dedupe_by_url(items: list[NewsItem]) -> list[NewsItem]:
    """按 URL 去重，避免同一条内容被多个来源重复保留。"""

    result: list[NewsItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.url.strip() or item.id
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
