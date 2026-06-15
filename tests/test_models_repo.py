"""State machine + jobs/applications repository."""

import pytest

from jobagent.models import InvalidTransition, JobStatus, can_transition, validate_transition
from jobagent.repo import (
    get_application_by_job,
    set_job_status,
    upsert_application,
    upsert_job,
)


def test_state_machine_allows_legal_path():
    path = [
        (JobStatus.DISCOVERED, JobStatus.SCORED),
        (JobStatus.SCORED, JobStatus.SHORTLISTED),
        (JobStatus.SHORTLISTED, JobStatus.PREPARED),
        (JobStatus.PREPARED, JobStatus.QUEUED_FOR_REVIEW),
        (JobStatus.QUEUED_FOR_REVIEW, JobStatus.SUBMITTED),
        (JobStatus.SUBMITTED, JobStatus.INTERVIEW),
        (JobStatus.INTERVIEW, JobStatus.OFFER),
    ]
    for cur, nxt in path:
        validate_transition(cur, nxt)  # should not raise


def test_state_machine_blocks_illegal():
    assert not can_transition(JobStatus.DISCOVERED, JobStatus.SUBMITTED)
    assert not can_transition(JobStatus.SKIPPED, JobStatus.INTERVIEW)
    with pytest.raises(InvalidTransition):
        validate_transition(JobStatus.DISCOVERED, JobStatus.SUBMITTED)
    with pytest.raises(InvalidTransition):
        validate_transition("scored", "bogus_status")


def test_upsert_job_dedup_by_url(ctx):
    cid = ctx.conn.execute(
        "INSERT INTO companies (name, ats_type) VALUES ('C','lever')"
    ).lastrowid
    a, created_a = upsert_job(ctx.conn, cid, title="Eng", url="http://x/1", ats_type="lever")
    b, created_b = upsert_job(ctx.conn, cid, title="Eng (updated)", url="http://x/1", ats_type="lever")
    assert a == b
    assert created_a is True and created_b is False
    row = ctx.conn.execute("SELECT title FROM jobs WHERE id=?", (a,)).fetchone()
    assert row["title"] == "Eng (updated)"  # refreshed, not duplicated


def test_set_job_status_enforced(ctx):
    cid = ctx.conn.execute("INSERT INTO companies (name, ats_type) VALUES ('C','lever')").lastrowid
    jid, _ = upsert_job(ctx.conn, cid, title="Eng", url="http://x/2", ats_type="lever")
    set_job_status(ctx.conn, jid, JobStatus.SCORED)
    with pytest.raises(InvalidTransition):
        set_job_status(ctx.conn, jid, JobStatus.SUBMITTED)  # skips the machine


def test_application_upsert_by_job(ctx):
    cid = ctx.conn.execute("INSERT INTO companies (name, ats_type) VALUES ('C','lever')").lastrowid
    jid, _ = upsert_job(ctx.conn, cid, title="Eng", url="http://x/3", ats_type="lever")
    a1 = upsert_application(ctx.conn, jid, resume_variant_id=None, cover_letter_id=None,
                            prefilled_data={"email": "a@x"})
    a2 = upsert_application(ctx.conn, jid, resume_variant_id=None, cover_letter_id=None,
                            prefilled_data={"email": "b@x"})
    assert a1 == a2
    app = get_application_by_job(ctx.conn, jid)
    assert app is not None
