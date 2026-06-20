"""`jobagent demo` — seed a realistic, fully offline example end-to-end.

This wires up everything needed to *see the agent work* without any network
access or API key: it sets a profile, registers the two sample resumes and the
sample cover-letter template, adds one demo company, inserts a handful of
sample roles directly into the DB, then runs the recoverable pipeline
(score -> tailor -> prep) so applications land in the review queue.

It deliberately does NOT source from real ATS boards (that needs the network)
and it never submits (the human gate is untouched). Re-running is safe: jobs
dedup by URL and the profile/company upsert.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .context import AgentContext
from .pipeline import run_once
from .repo import upsert_job
from .state import companies as companies_state
from .state import cover_letters as cover_state
from .state import profile as profile_state
from .state import resumes as resume_state

# Sample materials shipped with the repo (placeholders the human would replace).
_SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# A demo company. ats_type "lever" keeps prep fully offline (the real-question
# fetch only runs for greenhouse jobs with an external id).
_COMPANY = {
    "name": "Demo Co",
    "ats_type": "lever",
    "board_token": "democo",
    "notes": "Seeded by `jobagent demo` — replace with real targets.",
}

# Sample roles. Titles + descriptions are written so the offline heuristic
# scorer shortlists the two that match the seeded resumes (backend,
# data_scientist) and skips the others — so the demo shows real filtering.
_JOBS = [
    {
        "title": "Senior Backend Engineer",
        "location": "Remote (US)",
        "external_id": "demo-be-1",
        "description_text": (
            "We are hiring a senior backend engineer to design and scale our "
            "REST and gRPC APIs. You will work on distributed systems in "
            "Python, own PostgreSQL data models, and improve scalability and "
            "reliability of server-side services."
        ),
    },
    {
        "title": "Backend Software Engineer, Platform",
        "location": "New York, NY",
        "external_id": "demo-be-2",
        "description_text": (
            "Join our platform team building microservices and internal APIs. "
            "Strong Python and SQL skills, experience with Docker and "
            "API design for high-scalability backend systems."
        ),
    },
    {
        "title": "Data Scientist, Experimentation",
        "location": "Remote",
        "external_id": "demo-ds-1",
        "description_text": (
            "Drive product decisions with data science and machine learning. "
            "You will run A/B tests and experimentation, build predictive "
            "models with pandas and scikit-learn, and apply statistics to "
            "messy data."
        ),
    },
    {
        "title": "Senior Frontend Engineer",
        "location": "San Francisco, CA",
        "external_id": "demo-fe-1",
        "description_text": (
            "Build delightful web app experiences in React, TypeScript and "
            "CSS. A UI engineer role focused on the front-end."
        ),
    },
    {
        "title": "Account Executive, Enterprise Sales",
        "location": "Austin, TX",
        "external_id": "demo-sales-1",
        "description_text": (
            "Own the full sales cycle for enterprise accounts. Quota-carrying "
            "account executive role; no engineering required."
        ),
    },
]


def seed_demo(ctx: AgentContext, *, run: bool = True) -> dict:
    """Seed profile + materials + company + sample jobs; optionally run pipeline.

    Returns a summary dict describing what was created and (if run) the
    per-stage pipeline results.
    """
    conn = ctx.conn

    # 1) Profile — the only facts the agent may use about the human.
    profile_state.set_profile(
        conn,
        full_name="David Zeff",
        email="dpzeff@gmail.com",
        location="Remote",
        work_authorization="Authorized to work in the US",
        languages=["English"],
        extra_facts={"skills": "python,sql,api design", "years_experience": "6"},
    )

    # 2) Resume variants — the agent only selects these; it never writes them.
    existing_tracks = {r["track"] for r in resume_state.list_resumes(conn)}
    resume_files = {
        "backend": _SAMPLES / "resume_backend.txt",
        "data_scientist": _SAMPLES / "resume_data_scientist.txt",
    }
    resumes_added = 0
    for track, path in resume_files.items():
        if track not in existing_tracks:
            resume_state.add_resume(conn, track, str(path), notes="seeded by demo")
            resumes_added += 1

    # 3) Cover-letter template (the agent fills company/role slots only).
    covers_added = 0
    if not any(c["name"] == "Demo Template" for c in cover_state.list_cover_letters(conn)):
        cover_state.add_cover_letter(
            conn,
            name="Demo Template",
            file_path=str(_SAMPLES / "cover_letter_template.txt.j2"),
            is_template=True,
        )
        covers_added = 1

    # 4) One active demo company to attach the sample jobs to.
    company_id = companies_state.add_company(
        conn,
        name=_COMPANY["name"],
        ats_type=_COMPANY["ats_type"],
        board_token=_COMPANY["board_token"],
        notes=_COMPANY["notes"],
        active=True,
    )

    # 5) Seed sample jobs directly (no network). Dedup by URL on re-run.
    jobs_created = 0
    for j in _JOBS:
        url = f"https://jobs.lever.co/{_COMPANY['board_token']}/{j['external_id']}"
        _, created = upsert_job(
            conn,
            company_id=company_id,
            title=j["title"],
            url=url,
            ats_type=_COMPANY["ats_type"],
            external_id=j["external_id"],
            location=j["location"],
            description_text=j["description_text"],
        )
        jobs_created += int(created)

    summary = {
        "resumes_added": resumes_added,
        "cover_letters_added": covers_added,
        "company_id": company_id,
        "jobs_created": jobs_created,
    }

    # 6) Run the recoverable pipeline offline (no sourcing, no submit).
    if run:
        summary["pipeline"] = run_once(ctx, source=False, followup=True)

    return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_demo(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        result = seed_demo(ctx, run=not args.no_run)
        print("Seeded demo data:")
        print(f"  resumes added       : {result['resumes_added']}")
        print(f"  cover letters added : {result['cover_letters_added']}")
        print(f"  sample jobs created : {result['jobs_created']}")
        p = result.get("pipeline")
        if p:
            sc = p.get("score", {})
            print(
                f"  scored {sc.get('scored', 0)} job(s) ({sc.get('backend', '?')}): "
                f"{sc.get('shortlisted', 0)} shortlisted, {sc.get('skipped', 0)} skipped"
            )
            print(f"  >>> {p.get('queued_for_review', 0)} application(s) now in the review queue.")
        print("\nNext:  jobagent review list      (or)   jobagent web")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "demo",
        help="seed a fully offline example (profile, resumes, jobs) and run the pipeline",
    )
    p.add_argument(
        "--no-run",
        action="store_true",
        help="only seed data; do not run the score/tailor/prep pipeline",
    )
    p.set_defaults(func=cmd_demo)
