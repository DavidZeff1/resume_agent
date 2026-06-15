"""Draft follow-up messages for applications that have gone quiet.

On a cadence (``followup.days_until_followup``), any submitted application with
no outcome yet gets a polite follow-up *draft* written to the generated dir for
the human to review and send. We never send mail automatically.
"""

from __future__ import annotations

import sqlite3

from ..context import AgentContext
from ..events import log_event
from ..logging_setup import get_logger
from ..state import companies as companies_state
from ..state import profile as profile_state
from ..util import iso_in_days, parse_iso, utcnow

log = get_logger("track.followup")


def find_due(conn: sqlite3.Connection, now=None) -> list[dict]:
    now = now or utcnow()
    rows = conn.execute(
        """
        SELECT a.id app_id, a.submitted_at, a.follow_up_due, a.notes,
               j.title, j.url, j.company_id, j.status job_status
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status = 'submitted' AND a.follow_up_due IS NOT NULL
        """
    ).fetchall()
    due = []
    for r in rows:
        when = parse_iso(r["follow_up_due"])
        if when and when <= now and r["job_status"] in ("submitted", "no_response"):
            due.append(dict(r))
    return due


def draft_text(profile: dict, company: str, role: str, submitted_at: str | None) -> str:
    name = profile.get("full_name", "")
    date = (submitted_at or "")[:10]
    when = f" on {date}" if date else ""
    return (
        f"Subject: Following up on my {role} application\n\n"
        f"Hi {company} team,\n\n"
        f"I recently applied for the {role} role{when} and wanted to follow up to "
        f"reiterate my interest. I'd be glad to share more about how my background "
        f"fits the team's needs, and I'm happy to provide anything else that would "
        f"be helpful.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Best regards,\n{name}\n{profile.get('email', '')}\n"
    )


def run_followups(ctx: AgentContext, rearm: bool = True) -> dict:
    conn = ctx.conn
    profile = profile_state.get_profile(conn) or {}
    due = find_due(conn)
    summary = {"drafted": 0, "drafts": []}

    for r in due:
        company = companies_state.get_company(conn, r["company_id"])
        company_name = company["name"] if company else "the"
        text = draft_text(profile, company_name, r["title"], r["submitted_at"])
        path = ctx.config.paths.generated_dir / f"followup_app{r['app_id']}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

        next_due = iso_in_days(ctx.config.followup.days_until_followup) if rearm else None
        if rearm:
            conn.execute(
                "UPDATE applications SET follow_up_due = ?, updated_at = ? WHERE id = ?",
                (next_due, utcnow().isoformat(), r["app_id"]),
            )
        log_event(conn, "application", r["app_id"], "followup_drafted",
                  {"path": str(path), "next_due": next_due})
        summary["drafted"] += 1
        summary["drafts"].append({"app_id": r["app_id"], "path": str(path)})
        log.info("drafted follow-up for app #%s -> %s", r["app_id"], path)

    log_event(conn, "pipeline", None, "followup_run", {"drafted": summary["drafted"]})
    return summary
