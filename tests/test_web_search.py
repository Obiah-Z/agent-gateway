import json
from pathlib import Path

import pytest

from agent_gateway.config import GatewaySettings
from agent_gateway.ai.tools.registry import ToolRegistry
from agent_gateway.ai.tools.web_search import FirecrawlClient, register_web_search_tools


class FakeTavilyClient:
    def __init__(self) -> None:
        self.search_calls = []
        self.extract_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {
            "query": kwargs["query"],
            "answer": "short answer",
            "results": [
                {
                    "title": "Result A",
                    "url": "https://example.test/a",
                    "content": "Snippet A",
                    "score": 0.91,
                    "published_date": "2026-06-12",
                }
            ],
        }

    def extract(self, **kwargs):
        self.extract_calls.append(kwargs)
        return {
            "results": [
                {
                    "url": kwargs["urls"][0],
                    "raw_content": "Readable page content",
                }
            ],
            "failed_results": [],
        }


class FakeFirecrawlClient:
    def __init__(self) -> None:
        self.search_calls = []
        self.extract_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Firecrawl",
                        "url": "https://firecrawl.dev",
                        "description": "AI web scraping",
                        "markdown": "# Firecrawl",
                        "metadata": {
                            "title": "Firecrawl metadata",
                            "sourceURL": "https://firecrawl.dev",
                        },
                    }
                ],
            },
        }

    def extract(self, **kwargs):
        self.extract_calls.append(kwargs)
        return {
            "success": True,
            "results": [
                {
                    "url": kwargs["urls"][0],
                    "success": True,
                    "data": {
                        "markdown": "# Page",
                        "metadata": {
                            "sourceURL": kwargs["urls"][0],
                            "title": "Page",
                        },
                    },
                }
            ],
            "failed_results": [],
        }


class FakeHttpResponse:
    status_code = 200
    text = ""

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class FakeHttpClient:
    def __init__(self) -> None:
        self.post_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append({"args": args, **kwargs})
        return FakeHttpResponse({"success": True, "data": {"news": []}})

    def close(self) -> None:
        return None


def _settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    provider: str = "tavily",
) -> GatewaySettings:
    return GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        web_search_enabled=enabled,
        web_search_provider=provider,
        tavily_api_key="test-key",
        firecrawl_api_key="fc-test-key",
        web_search_max_results=3,
        web_search_max_output_chars=4000,
    )


def test_web_search_tools_are_registered_when_enabled(tmp_path: Path) -> None:
    registry = ToolRegistry()

    register_web_search_tools(registry, _settings(tmp_path), client=FakeTavilyClient())

    assert "web_search" in registry.names()
    assert "fetch_url" in registry.names()
    assert "web_search" in registry.names_for_tags(["web"])
    assert "fetch_url" in registry.names_for_tags(["fetch"])


def test_web_search_tools_are_not_registered_when_disabled(tmp_path: Path) -> None:
    registry = ToolRegistry()

    register_web_search_tools(registry, _settings(tmp_path, enabled=False), client=FakeTavilyClient())

    assert "web_search" not in registry.names()
    assert "fetch_url" not in registry.names()


def test_web_search_returns_normalized_json(tmp_path: Path) -> None:
    registry = ToolRegistry()
    fake = FakeTavilyClient()
    register_web_search_tools(registry, _settings(tmp_path), client=fake)

    raw = registry.dispatch(
        "web_search",
        {
            "query": "latest claude news",
            "max_results": 2,
            "search_depth": "advanced",
            "topic": "news",
            "days": 7,
        },
    )
    payload = json.loads(raw)

    assert fake.search_calls[0]["query"] == "latest claude news"
    assert fake.search_calls[0]["max_results"] == 2
    assert fake.search_calls[0]["search_depth"] == "advanced"
    assert fake.search_calls[0]["topic"] == "news"
    assert fake.search_calls[0]["days"] == 7
    assert payload["provider"] == "tavily"
    assert payload["answer"] == "short answer"
    assert payload["source_count"] == 1
    assert payload["results"][0]["url"] == "https://example.test/a"


def test_fetch_url_returns_normalized_extract_json(tmp_path: Path) -> None:
    registry = ToolRegistry()
    fake = FakeTavilyClient()
    register_web_search_tools(registry, _settings(tmp_path), client=fake)

    raw = registry.dispatch(
        "fetch_url",
        {
            "url": "https://example.test/a",
            "extract_depth": "advanced",
        },
    )
    payload = json.loads(raw)

    assert fake.extract_calls[0]["urls"] == ["https://example.test/a"]
    assert fake.extract_calls[0]["extract_depth"] == "advanced"
    assert payload["provider"] == "tavily"
    assert payload["results"][0]["raw_content"] == "Readable page content"


def test_web_search_output_is_truncated(tmp_path: Path) -> None:
    class LargeFakeTavilyClient(FakeTavilyClient):
        def search(self, **kwargs):
            return {
                "query": kwargs["query"],
                "answer": "x" * 5000,
                "results": [],
            }

    registry = ToolRegistry()
    settings = _settings(tmp_path)
    settings.web_search_max_output_chars = 1200
    register_web_search_tools(registry, settings, client=LargeFakeTavilyClient())

    raw = registry.dispatch("web_search", {"query": "large"})
    payload = json.loads(raw)

    assert len(raw) <= 1300
    assert payload["truncated"] is True
    assert payload["original_chars"] > len(raw)


def test_firecrawl_provider_uses_existing_tool_names(tmp_path: Path) -> None:
    registry = ToolRegistry()

    register_web_search_tools(
        registry,
        _settings(tmp_path, provider="firecrawl"),
        client=FakeFirecrawlClient(),
    )

    assert "web_search" in registry.names()
    assert "fetch_url" in registry.names()


def test_firecrawl_search_honors_news_and_recent_filters() -> None:
    client = FirecrawlClient(api_key="fc-test-key", base_url="https://api.firecrawl.test")
    fake_http = FakeHttpClient()
    client._http = fake_http

    client.search(
        query="latest rabbitmq news",
        max_results=3,
        topic="news",
        days=7,
    )

    payload = fake_http.post_calls[0]["json"]
    assert payload["query"] == "latest rabbitmq news"
    assert payload["limit"] == 3
    assert payload["sources"] == ["news"]
    assert payload["tbs"] == "qdr:w"


def test_firecrawl_web_search_returns_normalized_json(tmp_path: Path) -> None:
    registry = ToolRegistry()
    fake = FakeFirecrawlClient()
    register_web_search_tools(
        registry,
        _settings(tmp_path, provider="firecrawl"),
        client=fake,
    )

    raw = registry.dispatch(
        "web_search",
        {
            "query": "firecrawl docs",
            "max_results": 2,
            "search_depth": "advanced",
        },
    )
    payload = json.loads(raw)

    assert fake.search_calls[0]["query"] == "firecrawl docs"
    assert fake.search_calls[0]["max_results"] == 2
    assert fake.search_calls[0]["search_depth"] == "advanced"
    assert payload["provider"] == "firecrawl"
    assert payload["source_count"] == 1
    assert payload["results"][0]["title"] == "Firecrawl"
    assert payload["results"][0]["url"] == "https://firecrawl.dev"
    assert payload["results"][0]["content"] == "AI web scraping"


def test_firecrawl_fetch_url_returns_normalized_extract_json(tmp_path: Path) -> None:
    registry = ToolRegistry()
    fake = FakeFirecrawlClient()
    register_web_search_tools(
        registry,
        _settings(tmp_path, provider="firecrawl"),
        client=fake,
    )

    raw = registry.dispatch(
        "fetch_url",
        {
            "url": "https://example.test",
            "extract_depth": "advanced",
        },
    )
    payload = json.loads(raw)

    assert fake.extract_calls[0]["urls"] == ["https://example.test"]
    assert fake.extract_calls[0]["extract_depth"] == "advanced"
    assert payload["provider"] == "firecrawl"
    assert payload["results"][0]["url"] == "https://example.test"
    assert payload["results"][0]["raw_content"] == "# Page"
    assert payload["results"][0]["metadata"]["title"] == "Page"


def test_web_search_rejects_unsupported_provider(tmp_path: Path) -> None:
    registry = ToolRegistry()

    with pytest.raises(RuntimeError, match="Unsupported web search provider"):
        register_web_search_tools(
            registry,
            _settings(tmp_path, provider="unknown"),
            client=FakeTavilyClient(),
        )
