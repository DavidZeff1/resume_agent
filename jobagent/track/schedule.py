"""Interview scheduling.

Produces a standard .ics file (no dependencies) that imports into any calendar,
and moves the job to the `interview` state. The autonomous agent loop may also
push the same event to Google Calendar via the connector when one is available.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..context import AgentContext
from ..events import log_event
from ..models import JobStatus
from ..repo import get_application, get_job, set_job_status
from ..state import companies as companies_state
from ..util import utcnow


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;")
        .replace(",", "\\,").replace("\n", "\\n")
    )


def make_ics(
    uid: str,
    summary: str,
    start: datetime,
    duration_min: int,
    description: str = "",
    location: str = "",
) -> str:
    end = start + timedelta(minutes=duration_min)
    fmt = "%Y%m%dT%H%M%S"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//jobagent//interview//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start.strftime(fmt)}",
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(location)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def add_interview(
    ctx: AgentContext,
    app_id: int,
    when: str,
    duration_min: int = 45,
    location: str = "",
    notes: str = "",
) -> dict:
    conn = ctx.conn
    app = get_application(conn, app_id)
    if app is None:
        raise ValueError(f"no application #{app_id}")
    job = get_job(conn, int(app["job_id"]))
    company = companies_state.get_company(conn, int(job["company_id"])) if job else None
    company_name = company["name"] if company else "?"
    role = job["title"] if job else "?"

    start = datetime.fromisoformat(when)  # accepts 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DD HH:MM'
    summary = f"Interview: {role} @ {company_name}"
    description = (notes + ("\n" if notes else "") + f"Apply URL: {job['url']}").strip() if job else notes
    uid = f"jobagent-app{app_id}-{int(start.timestamp())}@local"

    ics = make_ics(uid, summary, start, duration_min, description, location)
    path = ctx.config.paths.generated_dir / f"interview_app{app_id}.ics"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ics, encoding="utf-8")

    set_job_status(conn, int(app["job_id"]), JobStatus.INTERVIEW)
    log_event(conn, "application", app_id, "interview_scheduled",
              {"when": when, "duration_min": duration_min, "ics": str(path)})
    return {
        "ics_path": str(path),
        "summary": summary,
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=duration_min)).isoformat(),
        "location": location,
    }
