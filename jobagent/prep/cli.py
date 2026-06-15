"""`jobagent prep` subcommand."""

from __future__ import annotations

import argparse

from ..context import AgentContext
from .runner import run_prep


def cmd_prep(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        summary = run_prep(ctx, use_real_questions=not args.no_fetch_questions)
        print(
            f"Queued {summary['queued']} applications for review "
            f"({summary['used_real_questions']} used the ATS's real questions, "
            f"{summary['errors']} errors)."
        )
        print("Review them with:  jobagent review list")
    return 0


def register(sub) -> None:
    p = sub.add_parser("prep", help="pre-fill prepared applications and queue for review")
    p.add_argument(
        "--no-fetch-questions",
        action="store_true",
        help="don't fetch real ATS questions; use the standard field set",
    )
    p.set_defaults(func=cmd_prep)
