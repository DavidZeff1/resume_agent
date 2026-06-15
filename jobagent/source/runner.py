"""Sourcing runner: walk the active watchlist and upsert discovered jobs."""

from __future__ import annotations

import hashlib

from ..context import AgentContext
from ..events import log_event
from ..logging_setup import get_logger
from ..repo import upsert_job
from ..state import companies as companies_state
from ..util import to_json
from .base import AdapterError
from .http_client import PoliteClient, RobotsDisallowed
from .registry import get_adapter

log = get_logger("source.runner")


def run_source(ctx: AgentContext, only: str | None = None, force: bool = False) -> dict:
    """Source jobs from active companies. `only` filters by name substring."""
    conn = ctx.conn
    cfg = ctx.config
    companies = companies_state.list_companies(conn, active_only=True)
    if only:
        needle = only.lower()
        companies = [c for c in companies if needle in c["name"].lower()]

    raw_dir = cfg.paths.cache_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "companies": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0,
        "per_company": {},
    }

    with PoliteClient(cfg.sourcing, cfg.paths.cache_dir) as client:
        for company in companies:
            name = company["name"]
            summary["companies"] += 1
            stats = {"new": 0, "updated": 0, "skipped": 0, "error": None}
            try:
                adapter = get_adapter(
                    company["ats_type"], has_board_url=bool(company["board_url"])
                )
                sourced = adapter(client, dict(company))
            except (AdapterError, RobotsDisallowed) as exc:
                stats["error"] = str(exc)
                summary["errors"] += 1
                log.warning("source %s: %s", name, exc)
                summary["per_company"][name] = stats
                continue
            except Exception as exc:  # network/parse error: isolate per company
                stats["error"] = f"{type(exc).__name__}: {exc}"
                summary["errors"] += 1
                log.warning("source %s failed: %s", name, exc)
                summary["per_company"][name] = stats
                continue

            cap = cfg.sourcing.max_jobs_per_company
            seen_urls: set[str] = set()
            for sj in sourced:
                if cap and (stats["new"] + stats["updated"]) >= cap:
                    break
                if not sj.url or not sj.title:
                    stats["skipped"] += 1
                    continue
                if sj.url in seen_urls:  # dedup within this board response
                    continue
                seen_urls.add(sj.url)

                ref = _write_raw(raw_dir, name, sj)
                _, created = upsert_job(
                    conn,
                    company_id=int(company["id"]),
                    title=sj.title,
                    url=sj.url,
                    ats_type=company["ats_type"],
                    external_id=sj.external_id,
                    location=sj.location,
                    description_text=sj.description_text,
                    raw_payload_ref=ref,
                )
                if created:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1

            summary["new"] += stats["new"]
            summary["updated"] += stats["updated"]
            summary["skipped"] += stats["skipped"]
            summary["per_company"][name] = stats
            log.info(
                "source %s: %d new, %d updated", name, stats["new"], stats["updated"]
            )

    log_event(conn, "pipeline", None, "source_run", {k: summary[k] for k in
              ("companies", "new", "updated", "skipped", "errors")})
    return summary


def _write_raw(raw_dir, company_name: str, sj) -> str:
    slug = hashlib.sha1((company_name + "|" + sj.url).encode()).hexdigest()[:16]
    path = raw_dir / f"{slug}.json"
    path.write_text(to_json(sj.raw), encoding="utf-8")
    return str(path)
