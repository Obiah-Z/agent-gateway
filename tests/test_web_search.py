import json
from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.tools.registry import ToolRegistry
from agent_gateway.tools.web_search import register_web_search_tools


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


def _settings(tmp_path: Path, *, enabled: bool = True) -> GatewaySettings:
    return GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        web_search_enabled=enabled,
        tavily_api_key="test-key",
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

    assert len(raw) <= 1300
    assert "truncated" in raw
