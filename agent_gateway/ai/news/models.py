from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class NewsSourceConfig:
    """单个新闻源配置。"""

    id: str
    type: str
    enabled: bool = True
    tags: tuple[str, ...] = ()
    url: str = ""
    repo: str = ""
    query: str = ""
    max_results: int = 5
    url_patterns: tuple[str, ...] = ()
    exclude_url_patterns: tuple[str, ...] = ()
    min_stars: int = 0
    pushed_within_days: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsSourceConfig":
        """从 JSON 配置行恢复新闻源定义。"""

        return cls(
            id=str(data.get("id", "")).strip(),
            type=str(data.get("type", "")).strip(),
            enabled=bool(data.get("enabled", True)),
            tags=tuple(str(item) for item in data.get("tags", []) if str(item).strip()),
            url=str(data.get("url", "")).strip(),
            repo=str(data.get("repo", "")).strip(),
            query=str(data.get("query", "")).strip(),
            max_results=max(1, int(data.get("max_results", 5))),
            url_patterns=tuple(
                str(item) for item in data.get("url_patterns", []) if str(item).strip()
            ),
            exclude_url_patterns=tuple(
                str(item) for item in data.get("exclude_url_patterns", []) if str(item).strip()
            ),
            min_stars=max(0, int(data.get("min_stars", 0) or 0)),
            pushed_within_days=max(0, int(data.get("pushed_within_days", 0) or 0)),
        )


@dataclass(slots=True)
class NewsItem:
    """一条标准化新闻条目。"""

    id: str
    source_id: str
    source_type: str
    title: str
    url: str
    published_at: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        source: NewsSourceConfig,
        title: str,
        url: str,
        published_at: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "NewsItem":
        """基于采集结果构建一条标准化新闻对象。"""

        normalized_url = normalize_url(url)
        item_id = stable_item_id(source.id, normalized_url or title)
        return cls(
            id=item_id,
            source_id=source.id,
            source_type=source.type,
            title=title.strip(),
            url=normalized_url,
            published_at=published_at.strip(),
            summary=summary.strip(),
            tags=source.tags,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """转成可持久化结构。"""

        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
            "summary": self.summary,
            "tags": list(self.tags),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        """从持久化结构恢复新闻对象。"""

        return cls(
            id=str(data.get("id", "")),
            source_id=str(data.get("source_id", "")),
            source_type=str(data.get("source_type", "")),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            published_at=str(data.get("published_at", "")),
            summary=str(data.get("summary", "")),
            tags=tuple(str(item) for item in data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class NewsCollectionResult:
    """一次采集任务的结果集合。"""

    items: list[NewsItem]
    errors: list[str] = field(default_factory=list)


def stable_item_id(source_id: str, value: str) -> str:
    """为同一来源下的同一条目生成稳定 ID。"""

    digest = hashlib.sha256(f"{source_id}:{value}".encode("utf-8")).hexdigest()
    return digest[:16]


def normalize_url(url: str) -> str:
    """规范化 URL 字段。"""

    return url.strip()


def parse_datetime(value: str) -> datetime | None:
    """把来源返回的时间字符串解析成 UTC 时间。"""

    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
