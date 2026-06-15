"""`jobagent score` subcommand."""

from __future__ import annotations

import argparse

from ..cliutil import print_table
from ..context import AgentContext
from ..models import JobStatus
from ..repo import list_jobs_by_status
from .runner import run_score


def cmd_score(args: argparse.Namespace) -> int:
    overrides = {"scoring": {"backend": args.backend}} if args.backend else None
    with AgentContext.create(args.config, overrides=overrides) as ctx:
        summary = run_score(ctx, rescore=args.rescore, limit=args.limit)
        print(
            f"Scored {summary['scored']} jobs via '{summary['backend']}': "
            f"{summary['shortlisted']} shortlisted, {summary['skipped']} skipped, "
            f"{summary['errors']} errors."
        )
        shortlisted = list_jobs_by_status(ctx.conn, JobStatus.SHORTLISTED)
        shortlisted.sort(key=lambda r: (r["score"] or 0), reverse=True)
        print("\nTop shortlisted:")
        print_table(
            [
                {
                    "id": r["id"],
                    "score": f"{(r['score'] or 0):.2f}",
                    "track": r["suggested_track"],
                    "title": r["title"],
                }
                for r in shortlisted[:15]
            ],
            [("id", "id"), ("score", "score"), ("track", "track"), ("title", "title")],
        )
    return 0


def register(sub) -> None:
    p = sub.add_parser("score", help="score discovered jobs; shortlist or skip")
    p.add_argument("--rescore", action="store_true", help="also re-score skipped/scored jobs")
    p.add_argument("--limit", type=int, help="max jobs to score this run")
    p.add_argument("--backend", choices=["auto", "heuristic", "claude"], help="override config")
    p.set_defaults(func=cmd_score)
