"""Data access for the `jobs` and `applications` tables.

Centralized so every stage shares the same dedup and status-transition logic.
Status changes go through ``set_job_status`` which validates against the state
machine in models.py — illegal transitions raise instead of corrupting state.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import transaction
from .events import log_event
from .models import ApplicationStatus, JobStatus, validate_transition
from .util import from_json, now_iso, to_json


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def upsert_job(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    title: str,
    url: str,
    ats_type: str,
    external_id: str | None = None,
    location: str | None = None,
    description_text: str | None = None,
    raw_payload_ref: str | None = None,
) -> tuple[int, bool]:
    """Insert a new job or refresh an existing one (dedup by url).

    Returns (job_id, created). An existing job keeps its pipeline status; only
    its descriptive fields and last_seen are refreshed.
    """
    now = now_iso()
    with transaction(conn):
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if existing:
            job_id = int(existing["id"])
            conn.execute(
                """
                UPDATE jobs SET title = ?, location = ?, description_text = ?,
                    external_id = COALESCE(?, external_id), last_seen = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, location, description_text, external_id, now, now, job_id),
            )
            return job_id, False

        cur = conn.execute(
            """
            INSERT INTO jobs (company_id, external_id, title, url, ats_type, location,
                description_text, raw_payload_ref, date_found, first_seen, last_seen,
                status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id, external_id, title, url, ats_type, location,
                description_text, raw_payload_ref, now, now, now,
                JobStatus.DISCOVERED, now,
            ),
        )
        job_id = int(cur.lastrowid)
        log_event(conn, "job", job_id, "discovered", {"title": title, "url": url})
        return job_id, True


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs_by_status(
    conn: sqlite3.Connection, status: str, limit: int | None = None
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs WHERE status = ? ORDER BY id"
    params: tuple = (status,)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    return list(conn.execute(sql, params).fetchall())


def update_job_score(
    conn: sqlite3.Connection,
    job_id: int,
    score: float,
    rationale: str,
    suggested_track: str | None,
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE jobs SET score = ?, score_rationale = ?, suggested_track = ?, "
            "updated_at = ? WHERE id = ?",
            (score, rationale, suggested_track, now_iso(), job_id),
        )


def set_job_status(conn: sqlite3.Connection, job_id: int, target: str) -> None:
    with transaction(conn):
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"no job #{job_id}")
        current = row["status"]
        validate_transition(current, target)  # raises InvalidTransition on illegal jumps
        if current != target:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (target, now_iso(), job_id),
            )
            log_event(conn, "job", job_id, "status", {"from": current, "to": target})


# --------------------------------------------------------------------------- #
# applications
# --------------------------------------------------------------------------- #
def upsert_application(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    resume_variant_id: int | None,
    cover_letter_id: int | None,
    cover_letter_rendered: str | None = None,
    prefilled_data: dict | None = None,
    unanswered_fields: list | None = None,
    status: str = ApplicationStatus.PREPARED,
) -> int:
    now = now_iso()
    with transaction(conn):
        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            app_id = int(existing["id"])
            conn.execute(
                """
                UPDATE applications SET resume_variant_id = ?, cover_letter_id = ?,
                    cover_letter_rendered = ?, prefilled_data = ?, unanswered_fields = ?,
                    status = ?, updated_at = ? WHERE id = ?
                """,
                (
                    resume_variant_id, cover_letter_id, cover_letter_rendered,
                    to_json(prefilled_data or {}), to_json(unanswered_fields or []),
                    status, now, app_id,
                ),
            )
            log_event(conn, "application", app_id, "updated", {"status": status})
            return app_id

        cur = conn.execute(
            """
            INSERT INTO applications (job_id, resume_variant_id, cover_letter_id,
                cover_letter_rendered, prefilled_data, unanswered_fields, status,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, resume_variant_id, cover_letter_id, cover_letter_rendered,
                to_json(prefilled_data or {}), to_json(unanswered_fields or []),
                status, now, now,
            ),
        )
        app_id = int(cur.lastrowid)
        log_event(conn, "application", app_id, "created", {"job_id": job_id, "status": status})
        return app_id


def get_application(conn: sqlite3.Connection, app_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()


def get_application_by_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()


def list_applications_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM applications WHERE status = ? ORDER BY id", (status,)
        ).fetchall()
    )


def decode_application(row: sqlite3.Row) -> dict:
    """Row -> dict with prefilled_data / unanswered_fields decoded from JSON."""
    d = dict(row)
    d["prefilled_data"] = from_json(d.get("prefilled_data"), {})
    d["unanswered_fields"] = from_json(d.get("unanswered_fields"), [])
    return d


def mark_application_queued(conn: sqlite3.Connection, app_id: int) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE applications SET status = ?, queued_at = ?, updated_at = ? WHERE id = ?",
            (ApplicationStatus.QUEUED_FOR_REVIEW, now_iso(), now_iso(), app_id),
        )
        log_event(conn, "application", app_id, "queued_for_review")


def mark_application_submitted(
    conn: sqlite3.Connection, app_id: int, follow_up_due: str | None
) -> None:
    now = now_iso()
    with transaction(conn):
        conn.execute(
            "UPDATE applications SET status = ?, submitted_at = ?, follow_up_due = ?, "
            "updated_at = ? WHERE id = ?",
            (ApplicationStatus.SUBMITTED, now, follow_up_due, now, app_id),
        )
        log_event(conn, "application", app_id, "submitted", {"follow_up_due": follow_up_due})


def set_application_notes(conn: sqlite3.Connection, app_id: int, notes: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE applications SET notes = ?, updated_at = ? WHERE id = ?",
            (notes, now_iso(), app_id),
        )
