"""Tailor runner: attach the best resume + cover letter to each shortlisted job."""

from __future__ import annotations

import sqlite3

from ..context import AgentContext
from ..events import log_event
from ..logging_setup import get_logger
from ..models import ApplicationStatus, JobStatus
from ..repo import (
    get_job,
    list_jobs_by_status,
    set_application_notes,
    set_job_status,
    upsert_application,
)
from ..state import companies as companies_state
from ..state import cover_letters as cover_state
from ..state import profile as profile_state
from ..state import resumes as resume_state
from . import documents

log = get_logger("tailor.runner")


def select_resume(conn: sqlite3.Connection, track: str | None) -> tuple[sqlite3.Row | None, str]:
    """Pick the best stored resume for a track, with graceful fallbacks."""
    if track:
        matches = resume_state.resumes_for_track(conn, track)
        if matches:
            return matches[0], f"track '{track}'"
    general = resume_state.resumes_for_track(conn, "general")
    if general:
        return general[0], "fallback to 'general'"
    everything = resume_state.list_resumes(conn)
    if everything:
        return everything[0], "fallback to first available"
    return None, "no resume variants exist"


def select_cover_letter(
    conn: sqlite3.Connection, track: str | None
) -> tuple[sqlite3.Row | None, str]:
    """Prefer a track-matching letter (template first), then any template, then any."""
    if track:
        matches = cover_state.cover_letters_for_track(conn, track)
        if matches:
            templated = [c for c in matches if c["is_template"]]
            chosen = templated[0] if templated else matches[0]
            return chosen, f"track '{track}'" + (" template" if chosen["is_template"] else "")
    all_letters = cover_state.list_cover_letters(conn)
    templates = [c for c in all_letters if c["is_template"]]
    if templates:
        return templates[0], "generic template"
    if all_letters:
        return all_letters[0], "first available"
    return None, "no cover letters exist"


def run_tailor(ctx: AgentContext) -> dict:
    conn = ctx.conn
    profile = profile_state.get_profile(conn) or {}
    jobs = list_jobs_by_status(conn, JobStatus.SHORTLISTED)
    summary = {"prepared": 0, "skipped_no_resume": 0, "errors": 0}

    for job in jobs:
        job_id = int(job["id"])
        track = job["suggested_track"]
        resume, resume_reason = select_resume(conn, track)
        if resume is None:
            log.warning("job #%s: %s — cannot prepare", job_id, resume_reason)
            summary["skipped_no_resume"] += 1
            continue
        cover, cover_reason = select_cover_letter(conn, track)

        rendered_path = None
        if cover is not None and cover["is_template"]:
            try:
                rendered_path = _render_cover(ctx, job, profile, cover, track)
            except Exception as exc:
                log.warning("job #%s: cover template render failed: %s", job_id, exc)
                summary["errors"] += 1

        app_id = upsert_application(
            conn,
            job_id,
            resume_variant_id=int(resume["id"]),
            cover_letter_id=int(cover["id"]) if cover else None,
            cover_letter_rendered=rendered_path,
            prefilled_data={},          # filled by the prep stage
            unanswered_fields=[],       # filled by the prep stage
            status=ApplicationStatus.PREPARED,
        )
        note = (
            f"resume: #{resume['id']} ({resume['track']}, {resume_reason}); "
            f"cover: " + (f"#{cover['id']} ({cover['name']}, {cover_reason})" if cover else "none")
        )
        set_application_notes(conn, app_id, note)
        set_job_status(conn, job_id, JobStatus.PREPARED)
        summary["prepared"] += 1
        log.info("tailored job #%s -> app #%s [%s]", job_id, app_id, note)

    log_event(conn, "pipeline", None, "tailor_run", summary)
    return summary


def _render_cover(ctx, job, profile, cover, track) -> str:
    company = get_job(ctx.conn, int(job["id"]))
    company_row = companies_state.get_company(ctx.conn, int(job["company_id"]))
    context = {
        "company": company_row["name"] if company_row else "",
        "role": job["title"],
        "applicant_name": profile.get("full_name", ""),
        "applicant_email": profile.get("email", ""),
        "track_label": documents.track_label(track),
    }
    text = documents.render_template(cover["file_path"], context)
    stem = f"cover_job{int(job['id'])}"
    txt_path = ctx.config.paths.generated_dir / f"{stem}.txt"
    documents.write_text(txt_path, text)
    try:
        documents.write_docx(ctx.config.paths.generated_dir / f"{stem}.docx", text)
    except Exception as exc:  # docx is a nice-to-have; .txt is the source of truth
        log.debug("docx write skipped for job #%s: %s", int(job["id"]), exc)
    return str(txt_path)
