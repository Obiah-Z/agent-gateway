from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlencode, urlparse
from xml.etree import ElementTree

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from agent_gateway.ai.news.models import NewsItem, NewsSourceConfig, parse_datetime


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class NewsSourceClient:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        if httpx is None:
            raise RuntimeError("news collection requires httpx")
        self.timeout_seconds = timeout_seconds
        self._http = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def collect(
        self,
        source: NewsSourceConfig,
        *,
        lookback_hours: int,
        max_items: int,
    ) -> list[NewsItem]:
        if source.type == "rss":
            return self._collect_rss(source, lookback_hours=lookback_hours, max_items=max_items)
        if source.type == "github_releases":
            return self._collect_github_releases(
                source,
                lookback_hours=lookback_hours,
                max_items=max_items,
            )
        if source.type == "arxiv":
            return self._collect_arxiv(source, lookback_hours=lookback_hours, max_items=max_items)
        if source.type == "html_page":
            return self._collect_html_page(
                source,
                lookback_hours=lookback_hours,
                max_items=max_items,
            )
        raise RuntimeError(f"unsupported news source type: {source.type}")

    def _collect_rss(
        self,
        source: NewsSourceConfig,
        *,
        lookback_hours: int,
        max_items: int,
    ) -> list[NewsItem]:
        if not source.url:
            return []
        response = self._http.get(source.url)
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        rows = []
        for item in root.findall(".//item"):
            title = _node_text(item, "title")
            link = _node_text(item, "link")
            published = _node_text(item, "pubDate") or _node_text(item, "published")
            summary = _node_text(item, "description")
            if not title or not link:
                continue
            if not _within_lookback(published, lookback_hours):
                continue
            rows.append(
                NewsItem.build(
                    source=source,
                    title=title,
                    url=link,
                    published_at=published,
                    summary=_strip_markup(summary),
                )
            )
            if len(rows) >= max_items:
                break
        return rows

    def _collect_github_releases(
        self,
        source: NewsSourceConfig,
        *,
        lookback_hours: int,
        max_items: int,
    ) -> list[NewsItem]:
        if not source.repo:
            return []
        url = f"https://api.github.com/repos/{source.repo}/releases"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._http.get(url, params={"per_page": max(1, min(max_items, 30))}, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return []
        rows = []
        for release in data:
            if not isinstance(release, dict):
                continue
            published = str(release.get("published_at") or release.get("created_at") or "")
            if not _within_lookback(published, lookback_hours):
                continue
            title = str(release.get("name") or release.get("tag_name") or "").strip()
            link = str(release.get("html_url") or "").strip()
            if not title or not link:
                continue
            rows.append(
                NewsItem.build(
                    source=source,
                    title=f"{source.repo}: {title}",
                    url=link,
                    published_at=published,
                    summary=str(release.get("body") or "")[:800],
                    metadata={"tag_name": release.get("tag_name", "")},
                )
            )
            if len(rows) >= max_items:
                break
        return rows

    def _collect_arxiv(
        self,
        source: NewsSourceConfig,
        *,
        lookback_hours: int,
        max_items: int,
    ) -> list[NewsItem]:
        query = source.query or 'all:"AI agent"'
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max(1, min(max_items, 20)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        response = self._http.get(f"https://export.arxiv.org/api/query?{urlencode(params)}")
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        rows = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = _atom_text(entry, "title")
            link = _atom_link(entry)
            published = _atom_text(entry, "published")
            summary = _atom_text(entry, "summary")
            if not title or not link:
                continue
            if not _within_lookback(published, lookback_hours):
                continue
            rows.append(
                NewsItem.build(
                    source=source,
                    title=title,
                    url=link,
                    published_at=published,
                    summary=" ".join(summary.split())[:800],
                )
            )
            if len(rows) >= max_items:
                break
        return rows

    def _collect_html_page(
        self,
        source: NewsSourceConfig,
        *,
        lookback_hours: int,
        max_items: int,
    ) -> list[NewsItem]:
        if not source.url:
            return []
        response = self._http.get(source.url)
        response.raise_for_status()
        return parse_html_news_page(
            response.text,
            source,
            lookback_hours=lookback_hours,
            max_items=max_items,
        )


def _node_text(node: ElementTree.Element, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else ""


def _atom_text(node: ElementTree.Element, tag: str) -> str:
    found = node.find(f"atom:{tag}", ATOM_NS)
    return (found.text or "").strip() if found is not None else ""


def _atom_link(node: ElementTree.Element) -> str:
    for link in node.findall("atom:link", ATOM_NS):
        href = link.attrib.get("href", "").strip()
        rel = link.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return href
    return ""


def _within_lookback(value: str, lookback_hours: int) -> bool:
    parsed = parse_source_datetime(value)
    if parsed is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    return parsed >= cutoff


def parse_source_datetime(value: str) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if date.tzinfo is None:
        return date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def _strip_markup(value: str) -> str:
    text = value.replace("<![CDATA[", "").replace("]]>", "")
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)[:800]


def parse_html_news_page(
    html: str,
    source: NewsSourceConfig,
    *,
    lookback_hours: int,
    max_items: int,
) -> list[NewsItem]:
    rows: list[NewsItem] = []
    seen_urls: set[str] = set()
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html, re.IGNORECASE | re.DOTALL):
        attrs = match.group("attrs")
        if _has_nav_semantics(attrs):
            continue
        href = _attr_value(attrs, "href")
        if not href:
            continue
        url = urljoin(source.url, href)
        if not _is_candidate_url(url, source):
            continue
        normalized_url = _canonical_page_url(url)
        if normalized_url == _canonical_page_url(source.url):
            continue
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        title = _extract_link_title(match.group("body"))
        if _is_weak_link_title(title):
            title = _title_from_url(normalized_url)
        if not title:
            continue
        context = _extract_context(html, match.start(), match.end())
        published = _extract_date(context)
        if published and not _within_lookback(published, lookback_hours):
            continue
        summary = _clean_text(_strip_markup(context))
        rows.append(
            NewsItem.build(
                source=source,
                title=title,
                url=normalized_url,
                published_at=published,
                summary=summary,
            )
        )
        if len(rows) >= max_items:
            break
    return rows


def _attr_value(attrs: str, name: str) -> str:
    pattern = rf"""{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))"""
    match = re.search(pattern, attrs, re.IGNORECASE)
    if not match:
        return ""
    return next((group for group in match.groups() if group is not None), "").strip()


def _is_candidate_url(url: str, source: NewsSourceConfig) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if source.url_patterns and not any(pattern in url for pattern in source.url_patterns):
        return False
    if source.exclude_url_patterns and any(pattern in url for pattern in source.exclude_url_patterns):
        return False
    return True


def _has_nav_semantics(attrs: str) -> bool:
    text = attrs.lower()
    return any(
        token in text
        for token in (
            "nav",
            "menu",
            "subnav",
            "breadcrumb",
            "footer",
            "header",
            "site-nav",
            "data-event-nav-name",
        )
    )


def _canonical_page_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=path, query="", fragment="").geturl()


def _is_weak_link_title(title: str) -> bool:
    if not title:
        return True
    normalized = title.strip().lower()
    weak = {
        "learn more",
        "read more",
        "view more",
        "more",
        "blog",
        "news",
        "阅读更多",
        "了解更多",
        "查看详情",
    }
    return normalized in weak or len(normalized) < 8


def _extract_link_title(html: str) -> str:
    heading = re.search(
        r"<h[1-6]\b[^>]*>(?P<title>.*?)</h[1-6]>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if heading:
        return _clean_text(_strip_markup(heading.group("title")))
    return _clean_text(_strip_markup(html))


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.rstrip("/").split("/")[-1]
    if not slug:
        return ""
    words = []
    for part in re.split(r"[-_]+", slug):
        if not part:
            continue
        upper = part.upper()
        words.append(upper if upper in {"AI", "API", "MCP", "RAG", "LLM"} else part.capitalize())
    return " ".join(words)


def _extract_context(html: str, start: int, end: int) -> str:
    structural_left = max(
        html.rfind("<article", 0, start),
        html.rfind("<li", 0, start),
        html.rfind("<section", 0, start),
    )
    left = structural_left if structural_left >= 0 else max(0, start - 700)
    structural_right = min(
        (
            position + len(marker)
            for marker in ("</article>", "</li>", "</section>")
            if (position := html.find(marker, end)) >= 0
        ),
        default=-1,
    )
    right = structural_right if structural_right >= 0 else min(len(html), end + 900)
    return html[left:right]


def _extract_date(value: str) -> str:
    datetime_attr = re.search(r'datetime=["\']([^"\']+)["\']', value, re.IGNORECASE)
    if datetime_attr:
        return datetime_attr.group(1).strip()
    date_match = re.search(
        r"\b(20\d{2}-\d{2}-\d{2}|20\d{2}/\d{2}/\d{2})\b",
        value,
    )
    if date_match:
        return date_match.group(1).replace("/", "-")
    long_date = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
        r"\s+\d{1,2},\s+20\d{2}\b",
        value,
        re.IGNORECASE,
    )
    if long_date:
        return long_date.group(0)
    return ""


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())
