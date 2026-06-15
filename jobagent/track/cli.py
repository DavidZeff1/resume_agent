"""`jobagent track` / `followup` / `schedule` subcommands (post-submission)."""

from __future__ import annotations

import argparse

from ..cliutil import print_table
from ..context import AgentContext
from ..models import JobStatus
from . import followup as followup_mod
from . import schedule as schedule_mod
from . import tracking


def cmd_track_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        rows = tracking.list_tracked(ctx.conn)
        print_table(
            rows,
            [
                ("app_id", "app#"), ("title", "title"), ("job_status", "status"),
                ("submitted_at", "submitted"), ("follow_up_due", "follow-up due"),
            ],
        )
    return 0


def cmd_track_set_status(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        job_id = tracking.set_outcome(ctx, args.app_id, args.status)
        print(f"Set job #{job_id} (app #{args.app_id}) status -> {args.status}")
    return 0


def cmd_followup_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        due = followup_mod.find_due(ctx.conn)
        print(f"{len(due)} follow-up(s) due:")
        print_table(
            due,
            [("app_id", "app#"), ("title", "title"), ("follow_up_due", "due"), ("job_status", "status")],
        )
    return 0


def cmd_followup_run(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        summary = followup_mod.run_followups(ctx, rearm=not args.no_rearm)
        print(f"Drafted {summary['drafted']} follow-up(s).")
        for d in summary["drafts"]:
            print(f"  app #{d['app_id']}: {d['path']}")
        if summary["drafted"]:
            print("\nReview/edit each draft, then send it yourself.")
    return 0


def cmd_schedule_interview(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        result = schedule_mod.add_interview(
            ctx, args.app_id, when=args.when, duration_min=args.duration,
            location=args.location or "", notes=args.notes or "",
        )
        print(f"Interview scheduled: {result['summary']}")
        print(f"  {result['start']} -> {result['end']}")
        if result["location"]:
            print(f"  location: {result['location']}")
        print(f"  calendar file (import anywhere): {result['ics_path']}")
        print("  (the agent loop can also add this to Google Calendar via the connector)")
    return 0


def register(sub) -> None:
    # track
    grp = sub.add_parser("track", help="track submitted-application outcomes")
    grp = grp.add_subparsers(dest="subcommand", required=True)
    p = grp.add_parser("list", help="list submitted applications + status")
    p.set_defaults(func=cmd_track_list)
    p = grp.add_parser("set-status", help="set an outcome on an application")
    p.add_argument("app_id", type=int)
    p.add_argument("status", choices=list(JobStatus.OUTCOMES))
    p.set_defaults(func=cmd_track_set_status)

    # followup
    grp = sub.add_parser("followup", help="draft follow-ups for quiet applications")
    grp = grp.add_subparsers(dest="subcommand", required=True)
    p = grp.add_parser("list", help="show follow-ups currently due")
    p.set_defaults(func=cmd_followup_list)
    p = grp.add_parser("run", help="draft all due follow-ups")
    p.add_argument("--no-rearm", action="store_true", help="don't reschedule the next follow-up")
    p.set_defaults(func=cmd_followup_run)

    # schedule
    grp = sub.add_parser("schedule", help="schedule interviews (.ics)")
    grp = grp.add_subparsers(dest="subcommand", required=True)
    p = grp.add_parser("add-interview", help="create an interview event for an application")
    p.add_argument("app_id", type=int)
    p.add_argument("--when", required=True, help="ISO datetime, e.g. 2026-06-20T14:00")
    p.add_argument("--duration", type=int, default=45, help="minutes (default 45)")
    p.add_argument("--location", help="room / video link / address")
    p.add_argument("--notes", help="free-form notes for the event description")
    p.set_defaults(func=cmd_schedule_interview)
