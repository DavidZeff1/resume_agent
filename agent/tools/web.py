"""Web tools — all free, no API keys.

``web_search`` uses DuckDuckGo's keyless HTML endpoint; ``fetch_url`` does a
plain GET and strips tags to readable text.
"""

from __future__ import annotations

import html
import re

import httpx

from . import registry

_UA = "Mozilla/5.0 (compatible; tiny-agent/0.1)"
_MAX_CHARS = 6000


@registry.tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return a list of result titles, URLs, and snippets.

    Args:
        query: the search query
        max_results: how many results to return (default 5)
    """
    resp = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": _UA},
        timeout=20,
        follow_redirects=True,
    )
    resp.raise_for_status()
    pattern = re.compile(
        r'result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?result__snippet[^>]*>(?P<snip>.*?)</a>',
        re.DOTALL,
    )
    out: list[str] = []
    for m in pattern.finditer(resp.text):
        url = html.unescape(m.group("url"))
        title = _strip(m.group("title"))
        snippet = _strip(m.group("snip"))
        out.append(f"{title}\n{url}\n{snippet}")
        if len(out) >= max_results:
            break
    return "\n\n".join(out) if out else "(no results)"


@registry.tool
def fetch_url(url: str) -> str:
    """Fetch a web page and return its readable text content.

    Args:
        url: the absolute URL to fetch (http/https)
    """
    if not url.startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://"
    resp = httpx.get(
        url, headers={"User-Agent": _UA}, timeout=20, follow_redirects=True
    )
    resp.raise_for_status()
    text = _strip(resp.text)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
    return text


def _strip(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", markup)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
