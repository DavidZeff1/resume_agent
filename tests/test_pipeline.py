"""Phase 7: the recoverable pipeline end-to-end (offline, no sourcing)."""

from jobagent.models import ApplicationStatus, JobStatus
from jobagent.pipeline import run_once
from jobagent.repo import decode_application, list_applications_by_status, list_jobs_by_status
from tests.conftest import seed_job, seed_min_state


def test_run_once_offline_queues_review_items(ctx, tmp_path):
    cid = seed_min_state(ctx, tmp_path)
    seed_job(ctx, cid, "Senior Backend Engineer",
             "Python REST APIs, microservices, postgres, scalability, distributed systems")
    seed_job(ctx, cid, "Enterprise Account Executive",
             "Own sales quota, drive revenue, manage pipeline of customers")

    results = run_once(ctx, source=False, followup=False)

    assert results["score"]["scored"] == 2
    assert results["score"]["shortlisted"] >= 1
    assert results["queued_for_review"] >= 1

    # the strong backend role should have made it to the review queue
    queued = list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW)
    assert queued, "expected at least one application queued for review"

    # nothing is auto-submitted by the pipeline
    assert not list_jobs_by_status(ctx.conn, JobStatus.SUBMITTED)

    # guardrail holds across the whole run: no screening answer leaked into prefill
    for app in queued:
        a = decode_application(app)
        assert {"why_company", "salary_expectation"}.isdisjoint(a["prefilled_data"])
        kinds = {u["kind"] for u in a["unanswered_fields"]}
        assert {"free_text_motivation", "salary", "work_auth"} <= kinds


def test_run_once_is_idempotent_on_second_pass(ctx, tmp_path):
    cid = seed_min_state(ctx, tmp_path)
    seed_job(ctx, cid, "Backend Engineer", "python apis microservices scalability postgres")
    run_once(ctx, source=False, followup=False)
    before = len(list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW))
    # second pass: already-queued jobs are not re-shortlisted into duplicates
    run_once(ctx, source=False, followup=False)
    after = len(list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW))
    assert before == after
