"""ATS adapter contract + shared helpers.

An adapter turns one company's board into a list of ``SourcedJob`` records.
Adapters register themselves by ats_type; the runner looks them up.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import Callable, Mapping

from bs4 import BeautifulSoup

from .http_client import PoliteClient


@dataclass
class SourcedJob:
    title: str
    url: str
    external_id: str | None = None
    location: str | None = None
    description_text: str | None = None
    raw: dict = field(default_factory=dict)


class AdapterError(Exception):
    """Raised when an adapter cannot resolve a board or parse a response."""


# ats_type -> callable(client, company) -> list[SourcedJob]
Adapter = Callable[[PoliteClient, Mapping], "list[SourcedJob]"]
ADAPTERS: dict[str, Adapter] = {}


def register(ats_type: str) -> Callable[[Adapter], Adapter]:
    def deco(fn: Adapter) -> Adapter:
        ADAPTERS[ats_type] = fn
        return fn

    return deco


_BLOCK_TAGS = (
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "section", "article", "header", "footer", "blockquote",
)


def html_to_text(raw: str | None, *, unescape: bool = False) -> str:
    """Strip HTML to readable plain text (used for job descriptions).

    Block-level elements become line breaks; inline elements (``<b>`` etc.) do
    not, so sentences stay intact.
    """
    if not raw:
        return ""
    if unescape:  # some APIs HTML-entity-encode their HTML payloads
        raw = _html.unescape(raw)
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    # Empty separator: rely on the source's own whitespace so punctuation after
    # an inline tag (".", ",") doesn't gain a spurious leading space.
    text = soup.get_text("")
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def company_token(company: Mapping, *, host_hint: str | None = None) -> str | None:
    """Resolve an ATS slug from board_token, or parse it from board_url."""
    token = (company.get("board_token") or "").strip() if company.get("board_token") else None
    if token:
        return token
    url = (company.get("board_url") or "").strip()
    if not url:
        return None
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url if "//" in url else f"https://{url}")
    # Greenhouse embed boards carry the token in a `for` query param.
    for_param = parse_qs(parts.query).get("for")
    if for_param:
        return for_param[0]
    segments = [s for s in parts.path.split("/") if s]
    # Most ATS board URLs are https://<host>/<token>[/...]; take the first path
    # segment, skipping common non-token prefixes.
    skip = {"embed", "job_board", "jobs", "careers", "o", "j"}
    for seg in segments:
        if seg.lower() not in skip:
            return seg
    return segments[0] if segments else None
