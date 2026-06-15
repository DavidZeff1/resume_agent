"""Generic HTML adapter (ats_type: other / comeet) — the BeautifulSoup fallback.

For boards without a sanctioned JSON API we parse the careers *page* politely
(robots.txt enforced upstream). We prefer schema.org ``JobPosting`` JSON-LD —
which many career sites embed and which is structured and stable — and fall
back to a light anchor-link heuristic only if no structured data is present.
"""

from __future__ import annotations

import json
from typing import Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..logging_setup import get_logger
from .base import AdapterError, SourcedJob, html_to_text, register
from .http_client import PoliteClient

log = get_logger("source.html")


def _iter_jsonld_objects(soup: BeautifulSoup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        # JSON-LD may be a single object, a list, or wrapped in @graph.
        if isinstance(data, dict) and "@graph" in data:
            yield from data["@graph"]
        elif isinstance(data, list):
            yield from data
        else:
            yield data


def _jobposting_to_sourced(obj: dict, base_url: str) -> SourcedJob | None:
    if not isinstance(obj, dict):
        return None
    types = obj.get("@type")
    types = types if isinstance(types, list) else [types]
    if "JobPosting" not in types:
        return None
    url = obj.get("url") or obj.get("@id") or ""
    if url:
        url = urljoin(base_url, url)
    location = None
    loc = obj.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address") or {}
        if isinstance(addr, dict):
            location = addr.get("addressLocality") or addr.get("addressRegion")
    return SourcedJob(
        title=(obj.get("title") or "").strip(),
        url=url.strip(),
        external_id=str(obj.get("identifier")) if obj.get("identifier") else None,
        location=location,
        description_text=html_to_text(obj.get("description")),
        raw=obj,
    )


def parse_html(html_text: str, base_url: str) -> list[SourcedJob]:
    """Pure parser (no I/O) so it is trivially unit-testable."""
    soup = BeautifulSoup(html_text, "lxml")
    jobs: list[SourcedJob] = []
    seen: set[str] = set()

    for obj in _iter_jsonld_objects(soup):
        job = _jobposting_to_sourced(obj, base_url)
        if job and job.url and job.url not in seen and job.title:
            seen.add(job.url)
            jobs.append(job)

    if jobs:
        return jobs

    # Fallback heuristic: anchors that look like individual job postings.
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        if any(k in href.lower() for k in ("/job", "/jobs/", "/position", "/careers/", "/openings/")):
            if href not in seen:
                seen.add(href)
                jobs.append(SourcedJob(title=text, url=href, raw={"href": href, "text": text}))
    return jobs


@register("other")
@register("comeet")
def fetch(client: PoliteClient, company: Mapping) -> list[SourcedJob]:
    board_url = (company.get("board_url") or "").strip()
    if not board_url:
        raise AdapterError(
            f"html: ats_type '{company.get('ats_type')}' requires board_url "
            f"for {company.get('name')}"
        )
    resp = client.get(board_url, sanctioned_api=False)
    jobs = parse_html(resp.text, board_url)
    log.info("html:%s -> %d jobs", board_url, len(jobs))
    return jobs
