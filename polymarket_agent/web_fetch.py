"""
Web fetch and search helpers for specialist research agents.

These are thin deterministic Python wrappers that agents can call via:
  python3 main.py web-fetch --url <url> --output <path>
  python3 main.py web-search --query <query> --output <path>

Results are written to JSON so they are auditable and inspectable
like all other run artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_USER_AGENT = "polymarket-agent-research/0.1"
_DEFAULT_TIMEOUT = 15


def fetch_url(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """
    Fetch a single URL and return its content as a structured dict.

    Returns:
      {
        "url": str,
        "fetched_at": ISO timestamp,
        "status": "ok" | "error",
        "content_type": str,
        "text": str,        # raw response text (truncated to 50k chars)
        "json": any,        # parsed JSON when content-type is application/json
        "error": str,       # only present on failure
      }
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/html, text/plain, */*",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_bytes = response.read(524288)  # max 512 KB
            content_type = response.headers.get("Content-Type", "")
            text = raw_bytes.decode("utf-8", errors="replace")[:50000]
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "fetched_at": fetched_at,
            "status": "error",
            "error": str(exc),
        }

    result: dict[str, Any] = {
        "url": url,
        "fetched_at": fetched_at,
        "status": "ok",
        "content_type": content_type,
        "text": text,
    }

    if "application/json" in content_type:
        try:
            result["json"] = json.loads(text)
        except json.JSONDecodeError:
            pass

    return result


def search_web(query: str, *, max_results: int = 10) -> dict[str, Any]:
    """
    Search using the DuckDuckGo Instant Answer JSON API (no API key required).

    Returns a structured dict with organic results extracted from the response.
    Note: DuckDuckGo's free API returns instant answers and related topics,
    not full organic search results. For full results, configure a Brave Search
    API key in BRAVE_SEARCH_API_KEY and that path will be used instead.

    Returns:
      {
        "query": str,
        "searched_at": ISO timestamp,
        "status": "ok" | "error",
        "source": "duckduckgo" | "brave",
        "results": [
          {
            "title": str,
            "url": str,
            "snippet": str,
          }
        ],
        "error": str,   # only on failure
      }
    """
    import os

    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if brave_key:
        return _search_brave(query, brave_key, max_results=max_results)
    return _search_duckduckgo(query, max_results=max_results)


def _search_brave(query: str, api_key: str, *, max_results: int = 10) -> dict[str, Any]:
    searched_at = datetime.now(timezone.utc).isoformat()
    params = urlencode({"q": query, "count": min(max_results, 20)})
    url = f"https://api.search.brave.com/res/v1/web/search?{params}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=_DEFAULT_TIMEOUT) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {
            "query": query,
            "searched_at": searched_at,
            "status": "error",
            "source": "brave",
            "error": str(exc),
            "results": [],
        }

    results = []
    for item in (payload.get("web", {}).get("results") or [])[:max_results]:
        results.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("description", "")),
            }
        )

    return {
        "query": query,
        "searched_at": searched_at,
        "status": "ok",
        "source": "brave",
        "results": results,
    }


def _search_duckduckgo(query: str, *, max_results: int = 10) -> dict[str, Any]:
    """
    Search via the DuckDuckGo HTML endpoint, parsing result titles, URLs, and snippets.
    No API key required. Results are parsed with lightweight regex rather than a full
    HTML parser to keep dependencies at zero.
    """
    import re

    searched_at = datetime.now(timezone.utc).isoformat()
    params = urlencode({"q": query, "kl": "us-en"})
    url = f"https://html.duckduckgo.com/html/?{params}"
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urlopen(request, timeout=_DEFAULT_TIMEOUT) as response:
            html = response.read(1048576).decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {
            "query": query,
            "searched_at": searched_at,
            "status": "error",
            "source": "duckduckgo",
            "error": str(exc),
            "results": [],
        }

    results = []

    # Extract result blocks: each result is wrapped in <div class="result__body">
    # Title links look like: <a class="result__a" href="...">title</a>
    # Snippets look like: <a class="result__snippet">...</a>
    title_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    tag_pattern = re.compile(r"<[^>]+>")

    titles = title_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (href, raw_title) in enumerate(titles[:max_results]):
        # DDG wraps outbound URLs; extract the actual destination
        clean_url = href
        uddg_match = re.search(r"uddg=([^&]+)", href)
        if uddg_match:
            from urllib.parse import unquote

            clean_url = unquote(uddg_match.group(1))

        title = tag_pattern.sub("", raw_title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = tag_pattern.sub("", snippets[i]).strip()[:500]

        if title and clean_url:
            results.append({"title": title, "url": clean_url, "snippet": snippet})

    return {
        "query": query,
        "searched_at": searched_at,
        "status": "ok",
        "source": "duckduckgo",
        "results": results[:max_results],
    }


def write_fetch_result(url: str, output_path: Path) -> dict[str, Any]:
    result = fetch_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def write_search_result(query: str, output_path: Path, *, max_results: int = 10) -> dict[str, Any]:
    result = search_web(query, max_results=max_results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
