"""Status tracking for submitted applications (manual marking is fine in v1)."""

from __future__ import annotations

import sqlite3

from ..context import AgentContext
from ..models import JobStatus
from ..repo import get_application, get_job, set_job_status


def set_outcome(ctx: AgentContext, app_id: int, status: str) -> int:
    """Set a post-submission outcome on the job behind an application.

    Validated by the state machine (e.g. submitted -> interview is allowed,
    skipped -> interview is not).
    """
    if status not in JobStatus.OUTCOMES:
        raise ValueError(
            f"status must be one of {JobStatus.OUTCOMES}, got {status!r}"
        )
    app = get_application(ctx.conn, app_id)
    if app is None:
        raise ValueError(f"no application #{app_id}")
    job_id = int(app["job_id"])
    set_job_status(ctx.conn, job_id, status)
    return job_id


def list_tracked(conn: sqlite3.Connection) -> list[dict]:
    """Submitted (and beyond) applications with their current job status."""
    rows = conn.execute(
        """
        SELECT a.id app_id, a.status app_status, a.submitted_at, a.follow_up_due,
               j.id job_id, j.title, j.status job_status, j.url
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.submitted_at IS NOT NULL
        ORDER BY a.submitted_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]
