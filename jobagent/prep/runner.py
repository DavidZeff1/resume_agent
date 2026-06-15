"""Prep runner: fill what's safe, queue the rest for the human, never submit."""

from __future__ import annotations

from ..context import AgentContext
from ..events import log_event
from ..logging_setup import get_logger
from ..models import ApplicationStatus, JobStatus
from ..repo import (
    get_job,
    list_applications_by_status,
    mark_application_queued,
    set_job_status,
    upsert_application,
)
from ..source.http_client import PoliteClient
from ..state import companies as companies_state
from ..state import cover_letters as cover_state
from ..state import profile as profile_state
from ..state import resumes as resume_state
from . import fields
from .greenhouse_questions import classify_questions, fetch_questions

log = get_logger("prep.runner")


def run_prep(ctx: AgentContext, use_real_questions: bool = True) -> dict:
    conn = ctx.conn
    profile = profile_state.get_profile(conn) or {}
    apps = list_applications_by_status(conn, ApplicationStatus.PREPARED)
    summary = {"queued": 0, "errors": 0, "used_real_questions": 0}

    client: PoliteClient | None = None
    try:
        for app in apps:
            app_id = int(app["id"])
            job = get_job(conn, int(app["job_id"]))
            if job is None:
                continue
            resume = (
                resume_state.get_resume(conn, app["resume_variant_id"])
                if app["resume_variant_id"] else None
            )
            cover = (
                cover_state.get_cover_letter(conn, app["cover_letter_id"])
                if app["cover_letter_id"] else None
            )

            prefilled = fields.build_prefilled(
                profile, resume, cover, app["cover_letter_rendered"]
            )
            unanswered: list[dict] | None = None
            require_core = True

            if use_real_questions and job["ats_type"] == "greenhouse" and job["external_id"]:
                try:
                    if client is None:
                        client = PoliteClient(ctx.config.sourcing, ctx.config.paths.cache_dir)
                    company = companies_state.get_company(conn, int(job["company_id"]))
                    questions = fetch_questions(client, dict(company), job["external_id"])
                    if questions:
                        mapped, asked = classify_questions(questions, profile)
                        prefilled = {**prefilled, **mapped}
                        unanswered = asked
                        require_core = False
                        summary["used_real_questions"] += 1
                except Exception as exc:
                    log.warning("job #%s: real-question fetch failed (%s); using defaults",
                                int(job["id"]), exc)

            if unanswered is None:
                unanswered = fields.default_unanswered(profile)

            try:
                fields.assert_guardrails(prefilled, unanswered, require_core=require_core)
            except fields.GuardrailViolation as exc:
                log.error("GUARDRAIL violation on app #%s: %s", app_id, exc)
                summary["errors"] += 1
                continue

            upsert_application(
                conn,
                int(job["id"]),
                resume_variant_id=app["resume_variant_id"],
                cover_letter_id=app["cover_letter_id"],
                cover_letter_rendered=app["cover_letter_rendered"],
                prefilled_data=prefilled,
                unanswered_fields=unanswered,
                status=ApplicationStatus.PREPARED,
            )
            mark_application_queued(conn, app_id)
            set_job_status(conn, int(job["id"]), JobStatus.QUEUED_FOR_REVIEW)
            summary["queued"] += 1
            log.info(
                "prepped app #%s: %d prefilled, %d for human",
                app_id, len(prefilled), len(unanswered),
            )
    finally:
        if client is not None:
            client.close()

    log_event(conn, "pipeline", None, "prep_run", summary)
    return summary
