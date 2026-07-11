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


@dataclass(slots=True)
class FirecrawlClient:
    api_key: str
    base_url: str = "https://api.firecrawl.dev"
    timeout_seconds: float = 15.0
    _http: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if httpx is None:
            raise RuntimeError("Firecrawl tools require httpx")
        if not self.api_key:
            raise RuntimeError("FIRECRAWL_API_KEY is not configured")
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
        del include_answer, include_raw_content
        payload: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(max_results, 10)),
        }
        normalized_topic = topic.strip().lower()
        if normalized_topic == "news":
            payload["sources"] = ["news"]
        if days is not None and days > 0:
            payload["tbs"] = _firecrawl_tbs_for_days(days)
        if search_depth == "advanced":
            payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
        response = self._http.post(
            f"{self.base_url}/v2/search",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Firecrawl search returned a non-object response")
        return data

    def extract(self, *, urls: list[str], extract_depth: str = "basic") -> dict[str, Any]:
        results = []
        failures = []
        for url in urls:
            payload: dict[str, Any] = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            if extract_depth == "advanced":
                payload["waitFor"] = 1000
            response = self._http.post(
                f"{self.base_url}/v2/scrape",
                headers=self._headers(),
                json=payload,
            )
            if response.status_code >= 400:
                failures.append({"url": url, "error": response.text})
                continue
            data = response.json()
            if isinstance(data, dict):
                results.append({"url": url, **data})
            else:
                failures.append({"url": url, "error": "non-object response"})
        return {"success": not failures, "results": results, "failed_results": failures}

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def _firecrawl_tbs_for_days(days: int) -> str:
    if days <= 1:
        return "qdr:d"
    if days <= 7:
        return "qdr:w"
    if days <= 31:
        return "qdr:m"
    return "qdr:y"


def register_web_search_tools(
    registry: ToolRegistry,
    settings: GatewaySettings,
    *,
    client: TavilyClient | FirecrawlClient | None = None,
) -> None:
    if not settings.web_search_enabled:
        return
    provider = settings.web_search_provider.strip().lower()
    if provider not in {"tavily", "firecrawl"}:
        raise RuntimeError(f"Unsupported web search provider: {settings.web_search_provider}")

    search_client = client or (
        TavilyClient(
            api_key=settings.tavily_api_key,
            base_url=settings.tavily_base_url,
            timeout_seconds=settings.web_search_timeout_seconds,
        )
        if provider == "tavily"
        else FirecrawlClient(
            api_key=settings.firecrawl_api_key,
            base_url=settings.firecrawl_base_url,
            timeout_seconds=settings.web_search_timeout_seconds,
        )
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
        data = search_client.search(
            query=query.strip(),
            max_results=max_results,
            search_depth=search_depth,
            topic=topic,
            days=days,
            include_answer=True,
            include_raw_content=False,
        )
        return _truncate_json(
            _normalize_search_response(data, provider=provider),
            max_output_chars,
        )

    def fetch_url(url: str, extract_depth: str = "basic") -> str:
        if not url.strip():
            return "Error: url is required"
        data = search_client.extract(urls=[url.strip()], extract_depth=extract_depth)
        return _truncate_json(
            _normalize_extract_response(data, provider=provider),
            max_output_chars,
        )

    registry.register(
        RegisteredTool(
            name="web_search",
            description=(
                "Search the public web using the configured provider (Tavily or "
                "Firecrawl). Use this for current or external facts and return "
                "source URLs for verification."
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
                "Extract readable page content for a URL through the configured "
                "provider (Tavily or Firecrawl). Use this to verify important search "
                "results before answering."
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


def _normalize_search_response(
    data: dict[str, Any],
    *,
    provider: str = "tavily",
) -> dict[str, Any]:
    if provider == "firecrawl":
        return _normalize_firecrawl_search_response(data)
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


def _normalize_extract_response(
    data: dict[str, Any],
    *,
    provider: str = "tavily",
) -> dict[str, Any]:
    if provider == "firecrawl":
        return _normalize_firecrawl_extract_response(data)
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


def _normalize_firecrawl_search_response(data: dict[str, Any]) -> dict[str, Any]:
    raw_results = data.get("data", data.get("results", []))
    if isinstance(raw_results, dict):
        flattened = []
        for source in ("web", "news", "images"):
            entries = raw_results.get(source, [])
            if isinstance(entries, list):
                flattened.extend(entries)
        raw_results = flattened
    results = []
    for item in raw_results if isinstance(raw_results, list) else []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        results.append(
            {
                "title": item.get("title") or metadata.get("title", ""),
                "url": item.get("url") or metadata.get("sourceURL", ""),
                "content": item.get("description")
                or item.get("snippet")
                or item.get("markdown")
                or item.get("content")
                or "",
                "score": item.get("score"),
                "published_date": item.get("publishedDate")
                or item.get("date")
                or metadata.get("publishedTime", ""),
            }
        )
    return {
        "provider": "firecrawl",
        "query": data.get("query", ""),
        "answer": data.get("answer", ""),
        "results": results,
        "source_count": len(results),
    }


def _normalize_firecrawl_extract_response(data: dict[str, Any]) -> dict[str, Any]:
    results = []
    failures = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        payload = item.get("data") if isinstance(item.get("data"), dict) else item
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        results.append(
            {
                "url": url or metadata.get("sourceURL", ""),
                "raw_content": payload.get("markdown")
                or payload.get("content")
                or payload.get("html")
                or "",
                "metadata": metadata,
            }
        )
    for item in data.get("failed_results", []):
        if not isinstance(item, dict):
            continue
        failures.append({"url": item.get("url", ""), "error": item.get("error", "")})
    return {
        "provider": "firecrawl",
        "results": results,
        "failed_results": failures,
    }


def _truncate_json(payload: dict[str, Any], limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    max_string_chars = max(200, limit // 4)
    while max_string_chars >= 200:
        truncated = _truncate_value(payload, max_string_chars)
        if isinstance(truncated, dict):
            truncated["truncated"] = True
            truncated["original_chars"] = len(text)
        compact = json.dumps(truncated, ensure_ascii=False)
        if len(compact) <= limit:
            return compact
        max_string_chars //= 2
    return json.dumps(_summarize_truncated_payload(payload, len(text)), ensure_ascii=False)


def _truncate_value(value: Any, max_string_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return value[:max_string_chars] + "... [truncated]"
    if isinstance(value, list):
        return [_truncate_value(item, max_string_chars) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_value(item, max_string_chars) for key, item in value.items()}
    return value


def _summarize_truncated_payload(payload: dict[str, Any], original_chars: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "provider": payload.get("provider", ""),
        "truncated": True,
        "original_chars": original_chars,
        "note": "Tool output exceeded max output chars and was summarized.",
    }
    results = payload.get("results")
    if isinstance(results, list):
        summary["source_count"] = payload.get("source_count", len(results))
        summary["result_count"] = len(results)
        if results and isinstance(results[0], dict):
            first = results[0]
            content = first.get("raw_content") or first.get("content") or ""
            metadata = first.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            summary["first_result"] = {
                "title": first.get("title") or metadata.get("title", ""),
                "url": first.get("url", ""),
                "content_preview": str(content)[:500],
            }
    failures = payload.get("failed_results")
    if isinstance(failures, list):
        summary["failed_count"] = len(failures)
    return summary
