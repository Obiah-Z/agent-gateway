from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional during bootstrap
    httpx = None  # type: ignore[assignment]

from agent_gateway.config import GatewaySettings
from agent_gateway.ai.tools.registry import RegisteredTool, ToolRegistry


@dataclass(slots=True)
class TavilyClient:
    api_key: str
    base_url: str = "https://api.tavily.com"
    timeout_seconds: float = 15.0
    _http: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if httpx is None:
            raise RuntimeError("web search requires httpx")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")
        self.base_url = self.base_url.rstrip("/")
        self._http = httpx.Client(timeout=self.timeout_seconds, trust_env=False)

    def search(
        self,
        *,
        query: str,
        max_results: int,
        search_depth: str = "basic",
        include_answer: bool = True,
        include_raw_content: bool = False,
        topic: str = "general",
        days: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max(1, min(max_results, 10)),
            "search_depth": search_depth if search_depth in {"basic", "advanced"} else "basic",
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "topic": topic if topic in {"general", "news"} else "general",
        }
        if days is not None and days > 0:
            payload["days"] = days
        response = self._http.post(
            f"{self.base_url}/search",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Tavily search returned a non-object response")
        return data

    def extract(self, *, urls: list[str], extract_depth: str = "basic") -> dict[str, Any]:
        response = self._http.post(
            f"{self.base_url}/extract",
            headers=self._headers(),
            json={
                "urls": urls,
                "extract_depth": (
                    extract_depth if extract_depth in {"basic", "advanced"} else "basic"
                ),
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Tavily extract returned a non-object response")
        return data

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def register_web_search_tools(
    registry: ToolRegistry,
    settings: GatewaySettings,
    *,
    client: TavilyClient | None = None,
) -> None:
    if not settings.web_search_enabled:
        return
    if settings.web_search_provider.strip().lower() != "tavily":
        raise RuntimeError(
            f"Unsupported web search provider: {settings.web_search_provider}"
        )

    tavily = client or TavilyClient(
        api_key=settings.tavily_api_key,
        base_url=settings.tavily_base_url,
        timeout_seconds=settings.web_search_timeout_seconds,
    )
    max_results_default = max(1, min(settings.web_search_max_results, 10))
    max_output_chars = max(1_000, settings.web_search_max_output_chars)

    def web_search(
        query: str,
        max_results: int = max_results_default,
        search_depth: str = "basic",
        topic: str = "general",
        days: int | None = None,
    ) -> str:
        if not query.strip():
            return "Error: query is required"
        data = tavily.search(
            query=query.strip(),
            max_results=max_results,
            search_depth=search_depth,
            topic=topic,
            days=days,
            include_answer=True,
            include_raw_content=False,
        )
        return _truncate_json(_normalize_search_response(data), max_output_chars)

    def fetch_url(url: str, extract_depth: str = "basic") -> str:
        if not url.strip():
            return "Error: url is required"
        data = tavily.extract(urls=[url.strip()], extract_depth=extract_depth)
        return _truncate_json(_normalize_extract_response(data), max_output_chars)

    registry.register(
        RegisteredTool(
            name="web_search",
            description=(
                "Search the public web using Tavily. Use this for current or external "
                "facts and return source URLs for verification."
            ),
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                    },
                    "topic": {"type": "string", "enum": ["general", "news"]},
                    "days": {"type": "integer", "minimum": 1},
                },
            },
            handler=web_search,
            tags=("web", "search", "network", "read"),
        )
    )
    registry.register(
        RegisteredTool(
            name="fetch_url",
            description=(
                "Extract readable page content for a URL through Tavily. Use this to "
                "verify important search results before answering."
            ),
            input_schema={
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                    "extract_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                    },
                },
            },
            handler=fetch_url,
            tags=("web", "fetch", "network", "read"),
        )
    )


def _normalize_search_response(data: dict[str, Any]) -> dict[str, Any]:
    results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score"),
                "published_date": item.get("published_date", ""),
            }
        )
    return {
        "provider": "tavily",
        "query": data.get("query", ""),
        "answer": data.get("answer", ""),
        "results": results,
        "source_count": len(results),
    }


def _normalize_extract_response(data: dict[str, Any]) -> dict[str, Any]:
    results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "url": item.get("url", ""),
                "raw_content": item.get("raw_content", ""),
            }
        )
    failures = []
    for item in data.get("failed_results", []):
        if not isinstance(item, dict):
            continue
        failures.append(
            {
                "url": item.get("url", ""),
                "error": item.get("error", ""),
            }
        )
    return {
        "provider": "tavily",
        "results": results,
        "failed_results": failures,
    }


def _truncate_json(payload: dict[str, Any], limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    truncated = dict(payload)
    truncated["truncated"] = True
    truncated["original_chars"] = len(text)
    compact = json.dumps(truncated, ensure_ascii=False)
    if len(compact) <= limit:
        return compact
    return compact[:limit] + f"\n... [truncated, {len(compact)} total chars]"
