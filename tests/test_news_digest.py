import json
from pathlib import Path

from agent_gateway.ai.news.collector import NewsCollector
from agent_gateway.ai.news.digest import build_digest_prompt, build_github_skill_digest_prompt
from agent_gateway.ai.news.models import NewsItem, NewsSourceConfig
from agent_gateway.ai.news.sources import NewsSourceClient, parse_html_news_page
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


class FakeNewsStateRepository:
    enabled = True

    def __init__(self, rows=None, *, fail: bool = False) -> None:
        self.rows = list(rows or [])
        self.fail = fail
        self.written: list[dict[str, object]] = []

    def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
        del limit, cursor
        if self.fail:
            raise RuntimeError("postgres unavailable")
        if table != "news_items":
            return []
        filters = filters or {}
        return [
            row
            for row in self.rows
            if all(str(row.get(key, "")) == str(value) for key, value in filters.items())
        ]

    def write_news_item(self, row: dict[str, object]):
        if self.fail:
            raise RuntimeError("postgres unavailable")
        self.written.append(dict(row))
        return row


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


def test_news_digest_store_prefers_postgres_seen_ids(tmp_path: Path) -> None:
    old = _item("enabled", "Old release", "https://example.com/old")
    fresh = _item("enabled", "Fresh release", "https://example.com/fresh")
    repo = FakeNewsStateRepository(
        [
            {
                "store_name": "news-store",
                "state": "seen",
                "item_id": old.id,
            }
        ]
    )
    store = NewsDigestStore(tmp_path / "news-store", read_backend=repo)

    assert [item.id for item in store.filter_new([old, fresh])] == [fresh.id]


def test_news_digest_store_writes_postgres_and_local_files(tmp_path: Path) -> None:
    item = _item("enabled", "Fresh release", "https://example.com/fresh")
    repo = FakeNewsStateRepository()
    store = NewsDigestStore(tmp_path / "news-store", write_backend=repo)

    store.append_collected([item])
    store.mark_seen([item])

    assert [row["state"] for row in repo.written] == ["collected", "seen"]
    assert repo.written[0]["store_name"] == "news-store"
    assert repo.written[0]["item_id"] == item.id
    assert "Fresh release" in (tmp_path / "news-store" / "collected-items.jsonl").read_text(
        encoding="utf-8"
    )
    assert item.id in (tmp_path / "news-store" / "seen-items.jsonl").read_text(encoding="utf-8")


def test_news_digest_store_keeps_local_files_when_postgres_fails(tmp_path: Path) -> None:
    item = _item("enabled", "Fresh release", "https://example.com/fresh")
    store = NewsDigestStore(
        tmp_path / "news-store",
        read_backend=FakeNewsStateRepository(fail=True),
        write_backend=FakeNewsStateRepository(fail=True),
    )

    store.append_collected([item])
    store.mark_seen([item])

    assert item.id in store.seen_ids()


def test_news_collector_dedupes_same_url_across_sources(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(
        json.dumps(
            {
                "sources": [
                    {"id": "skills", "type": "github_search_repositories"},
                    {"id": "plugins", "type": "github_search_repositories"},
                ]
            }
        ),
        encoding="utf-8",
    )
    duplicate_a = _item("skills", "Same repo from skills", "https://github.com/owner/repo")
    duplicate_b = _item("plugins", "Same repo from plugins", "https://github.com/owner/repo")
    unique = _item("plugins", "Different repo", "https://github.com/owner/other")
    fake_client = FakeNewsSourceClient(
        {
            "skills": [duplicate_a],
            "plugins": [duplicate_b, unique],
        }
    )

    collector = NewsCollector(
        sources_file,
        NewsDigestStore(tmp_path / "news-store"),
        client=fake_client,
    )
    result = collector.collect(lookback_hours=24, max_items=5, per_source_max_items=5)

    assert [item.url for item in result.items] == [
        "https://github.com/owner/repo",
        "https://github.com/owner/other",
    ]


def test_build_digest_prompt_contains_candidate_sources() -> None:
    item = _item("openai-news", "OpenAI Agent Update", "https://openai.com/news/example")

    prompt = build_digest_prompt([item], lookback_hours=24, max_output_items=6)

    assert "只能使用候选来源" in prompt
    assert "OpenAI Agent Update" in prompt
    assert "https://openai.com/news/example" in prompt


def test_build_github_skill_digest_prompt_contains_repo_metrics() -> None:
    source = NewsSourceConfig(
        id="github-agent-skills",
        type="github_search_repositories",
        tags=("github", "skill"),
    )
    item = NewsItem.build(
        source=source,
        title="owner/useful-agent-skill",
        url="https://github.com/owner/useful-agent-skill",
        published_at="2026-06-20T00:00:00Z",
        summary="Reusable skill for AI agents.",
        metadata={
            "stars": 1200,
            "forks": 80,
            "language": "Python",
            "topics": ["agents", "skills"],
        },
    )

    prompt = build_github_skill_digest_prompt([item], lookback_hours=168, max_output_items=6)

    assert "热门 Skill 发现" in prompt
    assert "owner/useful-agent-skill" in prompt
    assert "stars: 1200" in prompt
    assert "可用于我这个 Gateway" in prompt


def test_github_search_repositories_source_collects_ranked_repos(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "items": [
                    {
                        "full_name": "owner/agent-skills",
                        "html_url": "https://github.com/owner/agent-skills",
                        "description": "Reusable skills for agents",
                        "pushed_at": "2026-06-20T00:00:00Z",
                        "stargazers_count": 1000,
                        "forks_count": 70,
                        "language": "Python",
                        "topics": ["agent", "skills"],
                    }
                ]
            }

    class FakeHttp:
        def __init__(self, *args, **kwargs) -> None:
            self.calls = []

        def get(self, url, params=None, headers=None):
            self.calls.append({"url": url, "params": params, "headers": headers})
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr("agent_gateway.ai.news.sources.httpx.Client", FakeHttp)
    client = NewsSourceClient(timeout_seconds=1)
    source = NewsSourceConfig(
        id="github-agent-skills",
        type="github_search_repositories",
        query="skill agent in:name,description",
        min_stars=50,
        pushed_within_days=30,
        max_results=5,
    )

    items = client.collect(source, lookback_hours=168, max_items=5)

    assert [item.title for item in items] == ["owner/agent-skills"]
    assert items[0].metadata["stars"] == 1000
    assert "仓库描述：Reusable skills for agents" in items[0].summary
    assert "热度：1000 stars，70 forks" in items[0].summary
    assert "主要语言：Python" in items[0].summary
    assert "主题：agent, skills" in items[0].summary
    assert "stars:>=50" in client._http.calls[0]["params"]["q"]  # type: ignore[attr-defined]
    assert "pushed:>=" in client._http.calls[0]["params"]["q"]  # type: ignore[attr-defined]


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
