"""Phase 5: the never-autofill guardrail and the question classifier."""

import pytest

from jobagent.prep import fields
from jobagent.prep.greenhouse_questions import classify_questions


def test_build_prefilled_only_uses_profile_facts():
    profile = {"full_name": "A", "email": "a@x", "location": "Remote"}
    data = fields.build_prefilled(profile, None, None, None)
    assert data == {"full_name": "A", "email": "a@x", "location": "Remote"}
    # screening keys are never present
    assert "why_company" not in data and "salary_expectation" not in data


def test_default_unanswered_includes_screening_categories():
    items = fields.default_unanswered({"salary_expectation_notes": "open", "work_authorization": "citizen"})
    kinds = {i["kind"] for i in items}
    assert {"free_text_motivation", "salary", "work_auth"} <= kinds
    # work-auth carries a *suggested* value, never an auto-filled answer
    wa = next(i for i in items if i["kind"] == "work_auth")
    assert wa["suggested"] == "citizen" and "value" not in wa


def test_assert_guardrails_rejects_leaked_screening_key():
    with pytest.raises(fields.GuardrailViolation):
        fields.assert_guardrails({"why_company": "because"}, fields.default_unanswered({}))


def test_assert_guardrails_rejects_autofilled_unanswered():
    bad = [{"key": "salary_expectation", "kind": "salary", "value": "100k"}]
    with pytest.raises(fields.GuardrailViolation):
        fields.assert_guardrails({}, bad, require_core=False)


def test_assert_guardrails_requires_core_on_default_path():
    with pytest.raises(fields.GuardrailViolation):
        fields.assert_guardrails({}, [], require_core=True)  # nothing queued for human


def test_classify_real_questions():
    profile = {"full_name": "Ada Lovelace", "email": "ada@x", "work_authorization": "citizen",
               "salary_expectation_notes": "open"}
    questions = [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text"}]},
        {"label": "Resume", "required": True, "fields": [{"name": "resume", "type": "input_file"}]},
        {"label": "Cover Letter", "required": False, "fields": [{"name": "cover", "type": "textarea"}]},
        {"label": "Why do you want to work here?", "required": True, "fields": [{"name": "q1", "type": "textarea"}]},
        {"label": "Desired salary", "required": False, "fields": [{"name": "q2", "type": "input_text"}]},
        {"label": "Are you authorized to work in the US?", "required": True, "fields": [{"name": "q3", "type": "boolean"}]},
        {"label": "Gender", "required": False, "fields": [{"name": "q4", "type": "select"}]},
        {"label": "How did you hear about us?", "required": True, "fields": [{"name": "q5", "type": "input_text"}]},
    ]
    prefilled, unanswered = classify_questions(questions, profile)

    assert prefilled.get("first_name") == "Ada"
    assert prefilled.get("email") == "ada@x"
    by_kind = {}
    for u in unanswered:
        by_kind.setdefault(u["kind"], []).append(u["label"])

    # screening / sensitive fields all routed to the human
    assert any("salary" == k for k in by_kind)
    assert any("work_auth" == k for k in by_kind)
    assert any("demographic" == k for k in by_kind)
    assert any("free_text_motivation" == k for k in by_kind)
    # cover letter is NOT treated as a motivation question (we already attach it)
    assert "Cover Letter" not in [lbl for labels in by_kind.values() for lbl in labels]
    # no screening value was ever auto-filled
    fields.assert_guardrails(prefilled, unanswered, require_core=False)


def test_prep_runner_queues_without_leaking(ctx, tmp_path):
    from tests.conftest import seed_job, seed_min_state
    from jobagent.models import ApplicationStatus, JobStatus
    from jobagent.repo import list_applications_by_status, set_job_status
    from jobagent.tailor.runner import run_tailor
    from jobagent.prep.runner import run_prep
    from jobagent.repo import decode_application

    cid = seed_min_state(ctx, tmp_path)
    jid = seed_job(ctx, cid, "Backend Engineer", "Python APIs microservices postgres scalability")
    # walk to shortlisted, tailor, prep
    set_job_status(ctx.conn, jid, JobStatus.SCORED)
    set_job_status(ctx.conn, jid, JobStatus.SHORTLISTED)
    ctx.conn.execute("UPDATE jobs SET suggested_track='backend' WHERE id=?", (jid,))
    run_tailor(ctx)
    summary = run_prep(ctx, use_real_questions=False)  # offline default path
    assert summary["queued"] == 1

    queued = list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW)
    a = decode_application(queued[0])
    forbidden = {"why_company", "salary_expectation", "additional_info"}
    assert forbidden.isdisjoint(a["prefilled_data"])
