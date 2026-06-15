"""Greenhouse adapter — uses the official public Job Board API (JSON).

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
"""

from __future__ import annotations

from typing import Mapping

from ..logging_setup import get_logger
from .base import AdapterError, SourcedJob, company_token, html_to_text, register
from .http_client import PoliteClient

log = get_logger("source.greenhouse")

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


@register("greenhouse")
def fetch(client: PoliteClient, company: Mapping) -> list[SourcedJob]:
    token = company_token(company)
    if not token:
        raise AdapterError(f"greenhouse: no board_token/board_url for {company.get('name')}")
    data = client.get_json(API.format(token=token), sanctioned_api=True)
    jobs: list[SourcedJob] = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name")
        jobs.append(
            SourcedJob(
                title=j.get("title", "").strip(),
                url=j.get("absolute_url", "").strip(),
                external_id=str(j.get("id")) if j.get("id") is not None else None,
                location=location,
                # Greenhouse HTML-entity-encodes the content field.
                description_text=html_to_text(j.get("content"), unescape=True),
                raw=j,
            )
        )
    log.info("greenhouse:%s -> %d jobs", token, len(jobs))
    return jobs
