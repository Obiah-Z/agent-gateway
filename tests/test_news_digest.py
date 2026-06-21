import json
from pathlib import Path

from agent_gateway.ai.news.collector import NewsCollector
from agent_gateway.ai.news.digest import build_digest_prompt
from agent_gateway.ai.news.models import NewsItem, NewsSourceConfig
from agent_gateway.ai.news.sources import parse_html_news_page
from agent_gateway.ai.news.store import NewsDigestStore


class FakeNewsSourceClient:
    def __init__(self, items_by_source: dict[str, list[NewsItem]]) -> None:
        self.items_by_source = items_by_source
        self.calls: list[str] = []

    def collect(self, source, *, lookback_hours: int, max_items: int):
        del lookback_hours
        self.calls.append(source.id)
        return self.items_by_source.get(source.id, [])[:max_items]

    def close(self) -> None:
        pass


def _source(source_id: str) -> NewsSourceConfig:
    return NewsSourceConfig(id=source_id, type="github_releases", repo="owner/repo")


def _item(source_id: str, title: str, url: str) -> NewsItem:
    return NewsItem.build(
        source=_source(source_id),
        title=title,
        url=url,
        published_at="2026-06-15T00:00:00Z",
        summary="Release summary",
    )


def test_news_collector_loads_sources_filters_seen_and_records_items(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(
        json.dumps(
            {
                "sources": [
                    {"id": "enabled", "type": "github_releases", "repo": "owner/repo"},
                    {"id": "disabled", "type": "github_releases", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    store = NewsDigestStore(tmp_path / "news-store")
    old = _item("enabled", "Old release", "https://example.com/old")
    fresh = _item("enabled", "Fresh release", "https://example.com/fresh")
    store.mark_seen([old])
    fake_client = FakeNewsSourceClient({"enabled": [old, fresh]})

    collector = NewsCollector(sources_file, store, client=fake_client)
    result = collector.collect(lookback_hours=24, max_items=5, per_source_max_items=5)

    assert fake_client.calls == ["enabled"]
    assert [item.title for item in result.items] == ["Fresh release"]
    assert "Fresh release" in (tmp_path / "news-store" / "collected-items.jsonl").read_text(
        encoding="utf-8"
    )


def test_build_digest_prompt_contains_candidate_sources() -> None:
    item = _item("openai-news", "OpenAI Agent Update", "https://openai.com/news/example")

    prompt = build_digest_prompt([item], lookback_hours=24, max_output_items=6)

    assert "只能使用候选来源" in prompt
    assert "OpenAI Agent Update" in prompt
    assert "https://openai.com/news/example" in prompt


def test_parse_html_news_page_extracts_filtered_official_links() -> None:
    source = NewsSourceConfig(
        id="anthropic-news",
        type="html_page",
        url="https://www.anthropic.com/news",
        tags=("official", "anthropic"),
        url_patterns=("/news/",),
        exclude_url_patterns=("/company/",),
    )
    html = """
    <html>
      <body>
        <article>
          <a href="/news/agent-tool-use-update">
            Agent tool use update
          </a>
          <time datetime="2026-06-15T08:00:00Z">June 15, 2026</time>
          <p>New agent tooling capabilities for developers.</p>
        </article>
        <article>
          <a href="/company/careers">Careers</a>
        </article>
        <article>
          <a href="https://www.anthropic.com/news/agent-tool-use-update?utm=feed">
            Read more
          </a>
        </article>
      </body>
    </html>
    """

    items = parse_html_news_page(html, source, lookback_hours=24 * 365, max_items=5)

    assert len(items) == 1
    assert items[0].title == "Agent tool use update"
    assert items[0].url == "https://www.anthropic.com/news/agent-tool-use-update"
    assert items[0].published_at == "2026-06-15T08:00:00Z"
    assert "New agent tooling" in items[0].summary


def test_parse_html_news_page_falls_back_to_slug_title() -> None:
    source = NewsSourceConfig(
        id="deepmind-blog",
        type="html_page",
        url="https://deepmind.google/blog/",
        url_patterns=("/blog/",),
    )
    html = """
    <section>
      <a href="/blog/">Skip to main content</a>
      <a href="/blog/building-more-helpful-ai-agent-systems/">Learn more</a>
      <span>Jun 14, 2026</span>
    </section>
    """

    items = parse_html_news_page(html, source, lookback_hours=24 * 365, max_items=5)

    assert len(items) == 1
    assert items[0].title == "Building More Helpful AI Agent Systems"
    assert items[0].url == "https://deepmind.google/blog/building-more-helpful-ai-agent-systems"
    assert items[0].published_at == "Jun 14, 2026"


def test_parse_html_news_page_skips_navigation_links() -> None:
    source = NewsSourceConfig(
        id="deepmind-blog",
        type="html_page",
        url="https://deepmind.google/blog/",
        url_patterns=("/blog/",),
    )
    html = """
    <nav>
      <a class="subnav__link" data-event-nav-name="Research - SIMA" href="/blog/sima-2/">
        SIMA 2
      </a>
    </nav>
    <article>
      <a href="/blog/agent-safety-research/">
        <h3>Investing in multi-agent AI safety research</h3>
      </a>
      <time datetime="2026-06-13T00:00:00Z"></time>
    </article>
    """

    items = parse_html_news_page(html, source, lookback_hours=24 * 365, max_items=5)

    assert [item.title for item in items] == ["Investing in multi-agent AI safety research"]
