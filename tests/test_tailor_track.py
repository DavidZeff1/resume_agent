"""Phase 4 & 6: selection/rendering, follow-ups, and scheduling."""

from datetime import datetime

from jobagent.tailor import documents
from jobagent.tailor.runner import select_cover_letter, select_resume
from jobagent.track import followup, schedule
from jobagent.state import cover_letters as cover_state
from jobagent.state import resumes as resume_state


def test_select_resume_fallbacks(ctx, tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("x")
    # no resumes -> None
    r, reason = select_resume(ctx.conn, "backend")
    assert r is None
    resume_state.add_resume(ctx.conn, "general", str(f))
    r, reason = select_resume(ctx.conn, "backend")
    assert r is not None and "general" in reason  # fell back to general
    resume_state.add_resume(ctx.conn, "backend", str(f))
    r, reason = select_resume(ctx.conn, "backend")
    assert r["track"] == "backend"


def test_select_cover_prefers_track_template(ctx, tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("x")
    cover_state.add_cover_letter(ctx.conn, "Generic", str(f), is_template=False)
    cover_state.add_cover_letter(ctx.conn, "BackendTmpl", str(f), track="backend", is_template=True)
    chosen, reason = select_cover_letter(ctx.conn, "backend")
    assert chosen["name"] == "BackendTmpl"


def test_render_template_fills_slots(tmp_path):
    p = tmp_path / "t.j2"
    p.write_text("Dear {{ company }}, re {{ role }}. — {{ applicant_name }}\n")
    out = documents.render_template(str(p), {
        "company": "Acme", "role": "Backend Eng", "applicant_name": "Ada",
    })
    assert "Dear Acme, re Backend Eng. — Ada" in out


def test_followup_draft_text_is_clean():
    text = followup.draft_text({"full_name": "Ada", "email": "a@x"}, "Acme", "Backend Eng", "2026-06-01T00:00:00")
    assert "Acme" in text and "Backend Eng" in text and "Ada" in text
    assert "2026-06-01" in text


def test_make_ics_format_and_escaping():
    ics = schedule.make_ics(
        "uid@local", "Interview: Eng, Backend @ Acme",
        datetime(2026, 6, 20, 14, 0, 0), 45, description="line1\nline2", location="Meet",
    )
    assert "BEGIN:VEVENT" in ics and "DTSTART:20260620T140000" in ics
    assert "DTEND:20260620T144500" in ics
    assert "SUMMARY:Interview: Eng\\, Backend @ Acme" in ics  # comma escaped
    assert "DESCRIPTION:line1\\nline2" in ics                  # newline escaped


def test_add_interview_transitions_to_interview(ctx, tmp_path):
    from tests.conftest import seed_job, seed_min_state
    from jobagent.models import JobStatus
    from jobagent.repo import get_job, set_job_status, upsert_application
    from jobagent.util import iso_in_days
    from jobagent.repo import mark_application_submitted

    cid = seed_min_state(ctx, tmp_path)
    jid = seed_job(ctx, cid, "Backend Engineer", "python apis")
    for s in (JobStatus.SCORED, JobStatus.SHORTLISTED, JobStatus.PREPARED,
              JobStatus.QUEUED_FOR_REVIEW, JobStatus.SUBMITTED):
        set_job_status(ctx.conn, jid, s)
    app_id = upsert_application(ctx.conn, jid, resume_variant_id=None, cover_letter_id=None,
                               status="submitted")
    mark_application_submitted(ctx.conn, app_id, iso_in_days(7))
    result = schedule.add_interview(ctx, app_id, "2026-06-20T14:00", 30, location="Meet")
    assert get_job(ctx.conn, jid)["status"] == JobStatus.INTERVIEW
    assert result["ics_path"].endswith(".ics")
