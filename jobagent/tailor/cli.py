"""`jobagent tailor` subcommand."""

from __future__ import annotations

import argparse

from ..context import AgentContext
from .runner import run_tailor


def cmd_tailor(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        summary = run_tailor(ctx)
        print(
            f"Tailored {summary['prepared']} applications "
            f"({summary['skipped_no_resume']} skipped for no resume, "
            f"{summary['errors']} render errors)."
        )
    return 0


def register(sub) -> None:
    p = sub.add_parser("tailor", help="select resume + cover letter for shortlisted jobs")
    p.set_defaults(func=cmd_tailor)
