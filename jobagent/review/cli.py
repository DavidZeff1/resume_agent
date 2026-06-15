"""`jobagent review` subcommands: list / show / submit (the human gate)."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from ..cliutil import confirm, print_table
from ..context import AgentContext
from ..models import ApplicationStatus, JobStatus
from ..repo import (
    decode_application,
    get_application,
    get_job,
    list_applications_by_status,
    mark_application_submitted,
    set_job_status,
)
from ..state import companies as companies_state
from ..state import resumes as resume_state
from ..util import iso_in_days


def _company_name(ctx, job) -> str:
    c = companies_state.get_company(ctx.conn, int(job["company_id"]))
    return c["name"] if c else "?"


def cmd_review_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        apps = list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW)
        rows = []
        for app in apps:
            a = decode_application(app)
            job = get_job(ctx.conn, a["job_id"])
            required_left = sum(1 for u in a["unanswered_fields"] if u.get("required"))
            rows.append({
                "app": a["id"],
                "score": f"{(job['score'] or 0):.2f}" if job else "-",
                "company": _company_name(ctx, job) if job else "?",
                "title": job["title"] if job else "?",
                "to_answer": len(a["unanswered_fields"]),
                "required": required_left,
            })
        print(f"{len(rows)} application(s) queued for review:\n")
        print_table(
            rows,
            [
                ("app", "app#"), ("score", "score"), ("company", "company"),
                ("title", "title"), ("to_answer", "to-answer"), ("required", "required"),
            ],
        )
        if rows:
            print("\nInspect one with:  jobagent review show <app#>")
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        app = get_application(ctx.conn, args.id)
        if app is None:
            print(f"No application #{args.id}")
            return 1
        a = decode_application(app)
        job = get_job(ctx.conn, a["job_id"])
        resume = (
            resume_state.get_resume(ctx.conn, a["resume_variant_id"])
            if a["resume_variant_id"] else None
        )

        bar = "=" * 70
        print(bar)
        print(f"APPLICATION #{a['id']}   (status: {a['status']})")
        print(bar)
        if job:
            print(f"Role     : {job['title']}")
            print(f"Company  : {_company_name(ctx, job)}")
            print(f"Location : {job['location'] or '-'}")
            print(f"Score    : {(job['score'] or 0):.2f}  —  {job['score_rationale'] or ''}")
            print(f"Apply at : {job['url']}")
        print(f"\nResume   : {resume['file_path'] if resume else '(none)'}"
              f"{'  [MISSING FILE]' if resume and not Path(resume['file_path']).exists() else ''}")

        print("\n-- Cover letter ----------------------------------------------------")
        _print_cover(a)

        print("\n-- Pre-filled (from your stored profile) --------------------------")
        if a["prefilled_data"]:
            for key, value in a["prefilled_data"].items():
                print(f"  ✓ {key:16s}: {value}")
        else:
            print("  (nothing could be pre-filled)")

        print("\n-- YOU MUST COMPLETE THESE (the agent will not answer them) -------")
        if not a["unanswered_fields"]:
            print("  (none)")
        for u in a["unanswered_fields"]:
            tag = "REQUIRED" if u.get("required") else "optional"
            print(f"  ▸ [{tag}] {u['label']}")
            print(f"      kind: {u['kind']}  —  {u.get('reason','')}")
            if u.get("suggested"):
                print(f"      suggested (from your profile, verify): {u['suggested']}")

        print("\nWhen done on the real site:  "
              f"jobagent review submit {a['id']}")
        if args.open and job:
            webbrowser.open(job["url"])
            print(f"(opened {job['url']} in your browser)")
    return 0


def _print_cover(a: dict) -> None:
    path = a.get("cover_letter_rendered")
    if path and Path(path).exists():
        text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines()[:24]:
            print(f"  {line}")
    else:
        print("  (cover letter file referenced via the application; none rendered inline)")


def cmd_review_submit(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        app = get_application(ctx.conn, args.id)
        if app is None:
            print(f"No application #{args.id}")
            return 1
        a = decode_application(app)
        if a["status"] == ApplicationStatus.SUBMITTED:
            print(f"Application #{a['id']} is already submitted.")
            return 0
        job = get_job(ctx.conn, a["job_id"])
        required_left = [u for u in a["unanswered_fields"] if u.get("required")]

        print(f"About to mark application #{a['id']} ({job['title'] if job else '?'}) as SUBMITTED.")
        print(f"Apply URL: {job['url'] if job else '?'}")
        if required_left:
            print(f"\n! {len(required_left)} REQUIRED field(s) were left for you to answer on the site:")
            for u in required_left:
                print(f"    - {u['label']}")
        print("\nThis only RECORDS that *you* submitted on the real site. "
              "The agent does not submit.")
        if not confirm("Did you submit it and want to record that?", assume_yes=args.yes):
            print("Not recorded.")
            return 0

        follow_up = iso_in_days(ctx.config.followup.days_until_followup)
        mark_application_submitted(ctx.conn, a["id"], follow_up)
        set_job_status(ctx.conn, a["job_id"], JobStatus.SUBMITTED)
        print(f"Recorded as submitted. Follow-up due {follow_up[:10]}.")
    return 0


def register(sub) -> None:
    grp = sub.add_parser("review", help="review & submit prepared applications (human gate)")
    grp = grp.add_subparsers(dest="subcommand", required=True)

    p = grp.add_parser("list", help="list applications queued for review")
    p.set_defaults(func=cmd_review_list)

    p = grp.add_parser("show", help="show one queued application in detail")
    p.add_argument("id", type=int)
    p.add_argument("--open", action="store_true", help="open the apply URL in a browser")
    p.set_defaults(func=cmd_review_show)

    p = grp.add_parser("submit", help="record that YOU submitted (agent never submits)")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_review_submit)
