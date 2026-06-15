"""`jobagent source` subcommand."""

from __future__ import annotations

import argparse

from ..cliutil import print_table
from ..context import AgentContext
from .runner import run_source


def cmd_source(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        summary = run_source(ctx, only=args.only, force=args.force)
        print(
            f"Sourced {summary['companies']} companies: "
            f"{summary['new']} new, {summary['updated']} updated, "
            f"{summary['errors']} errors."
        )
        rows = [
            {"company": name, **stats}
            for name, stats in summary["per_company"].items()
        ]
        print_table(
            rows,
            [
                ("company", "company"),
                ("new", "new"),
                ("updated", "updated"),
                ("skipped", "skipped"),
                ("error", "error"),
            ],
        )
    return 0


def register(sub) -> None:
    p = sub.add_parser("source", help="fetch new roles from the watchlist")
    p.add_argument("--only", help="only companies whose name contains this string")
    p.add_argument("--force", action="store_true", help="bypass the response cache")
    p.set_defaults(func=cmd_source)
