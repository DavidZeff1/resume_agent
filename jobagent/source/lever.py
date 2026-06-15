"""Lever adapter — uses the official public Postings API (JSON).

    GET https://api.lever.co/v0/postings/{token}?mode=json
"""

from __future__ import annotations

from typing import Mapping

from ..logging_setup import get_logger
from .base import AdapterError, SourcedJob, company_token, html_to_text, register
from .http_client import PoliteClient

log = get_logger("source.lever")

API = "https://api.lever.co/v0/postings/{token}?mode=json"


@register("lever")
def fetch(client: PoliteClient, company: Mapping) -> list[SourcedJob]:
    token = company_token(company)
    if not token:
        raise AdapterError(f"lever: no board_token/board_url for {company.get('name')}")
    data = client.get_json(API.format(token=token), sanctioned_api=True)
    if not isinstance(data, list):
        raise AdapterError(f"lever: unexpected payload for {token}")
    jobs: list[SourcedJob] = []
    for p in data:
        categories = p.get("categories") or {}
        description = p.get("descriptionPlain") or html_to_text(p.get("description"))
        jobs.append(
            SourcedJob(
                title=(p.get("text") or "").strip(),
                url=(p.get("hostedUrl") or p.get("applyUrl") or "").strip(),
                external_id=str(p.get("id")) if p.get("id") else None,
                location=categories.get("location"),
                description_text=description,
                raw=p,
            )
        )
    log.info("lever:%s -> %d jobs", token, len(jobs))
    return jobs
