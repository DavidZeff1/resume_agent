"""Workable adapter — uses the public widget JSON endpoint.

    GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true

This is a sanctioned JSON endpoint (the same one Workable's own embeddable
careers widget calls), so we prefer it over scraping the JS-rendered page.
"""

from __future__ import annotations

from typing import Mapping

from ..logging_setup import get_logger
from .base import AdapterError, SourcedJob, company_token, html_to_text, register
from .http_client import PoliteClient

log = get_logger("source.workable")

API = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"


@register("workable")
def fetch(client: PoliteClient, company: Mapping) -> list[SourcedJob]:
    token = company_token(company)
    if not token:
        raise AdapterError(f"workable: no board_token/board_url for {company.get('name')}")
    data = client.get_json(API.format(token=token), sanctioned_api=True)
    jobs: list[SourcedJob] = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        location = ", ".join(
            str(loc[k]) for k in ("city", "region", "country") if loc.get(k)
        ) or None
        shortcode = j.get("shortcode")
        url = (
            j.get("url")
            or j.get("application_url")
            or (f"https://apply.workable.com/{token}/j/{shortcode}/" if shortcode else "")
        )
        jobs.append(
            SourcedJob(
                title=(j.get("title") or "").strip(),
                url=url.strip(),
                external_id=str(shortcode) if shortcode else None,
                location=location,
                description_text=html_to_text(j.get("description")),
                raw=j,
            )
        )
    log.info("workable:%s -> %d jobs", token, len(jobs))
    return jobs
