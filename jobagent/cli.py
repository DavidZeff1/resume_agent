"""Command-line interface for jobagent.

Run ``python -m jobagent --help``. Commands are grouped by pipeline stage;
Phase 1 covers state management (profile / resume / cover / company) plus
`init`, `status`, and `events`. Later phases register their own subcommands.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .cliutil import confirm, print_kv, print_table
from .context import AgentContext
from .events import recent_events
from .state import companies as companies_state
from .state import cover_letters as cover_state
from .state import profile as profile_state
from .state import resumes as resume_state


# --------------------------------------------------------------------------- #
# core: init / status / events
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        print(f"Initialized DB at {ctx.config.paths.db_path}")
        if args.sync_companies:
            result = companies_state.sync_from_config(ctx.conn, ctx.config)
            print(f"Synced {result['synced']} companies from config.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        conn = ctx.conn
        profile = profile_state.get_profile(conn)
        missing = profile_state.missing_core_fields(conn)

        print("== Profile ==")
        if profile and profile.get("full_name"):
            print(f"  {profile['full_name']} <{profile.get('email') or '?'}>")
            if missing:
                print(f"  ! missing core fields: {', '.join(missing)}")
            else:
                print("  core fields complete")
        else:
            print("  (not set — run: jobagent profile set --full-name ... --email ...)")

        counts = {
            "resume variants": len(resume_state.list_resumes(conn)),
            "cover letters": len(cover_state.list_cover_letters(conn)),
            "companies (active)": len(companies_state.list_companies(conn, active_only=True)),
            "companies (total)": len(companies_state.list_companies(conn)),
        }
        print("\n== State ==")
        print_kv(counts)

        jobs_by_status = conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY status"
        ).fetchall()
        print("\n== Jobs by status ==")
        print_table(jobs_by_status, [("status", "status"), ("n", "count")])

        apps_by_status = conn.execute(
            "SELECT status, COUNT(*) n FROM applications GROUP BY status ORDER BY status"
        ).fetchall()
        print("\n== Applications by status ==")
        print_table(apps_by_status, [("status", "status"), ("n", "count")])

        warn_missing_files(ctx)
    return 0


def warn_missing_files(ctx: AgentContext) -> None:
    missing_resumes = resume_state.missing_files(ctx.conn)
    missing_covers = cover_state.missing_files(ctx.conn)
    if missing_resumes or missing_covers:
        print("\n! Some referenced files are missing on disk:")
        for r in missing_resumes:
            print(f"    resume #{r['id']} ({r['track']}): {r['file_path']}")
        for c in missing_covers:
            print(f"    cover  #{c['id']} ({c['name']}): {c['file_path']}")


def cmd_events(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        rows = recent_events(ctx.conn, args.limit)
        print_table(
            rows,
            [
                ("ts", "ts"),
                ("entity_type", "entity"),
                ("entity_id", "id"),
                ("action", "action"),
                ("detail", "detail"),
            ],
        )
    return 0


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #
def cmd_profile_show(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        profile = profile_state.get_profile(ctx.conn)
        if not profile:
            print("(no profile set)")
            return 0
        print_kv(profile, title="Profile")
    return 0


def cmd_profile_set(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        languages = (
            [s.strip() for s in args.languages.split(",") if s.strip()]
            if args.languages is not None
            else None
        )
        extra_facts = None
        if args.fact:
            extra_facts = {}
            for item in args.fact:
                if "=" not in item:
                    print(f"! ignoring --fact {item!r} (expected key=value)")
                    continue
                k, v = item.split("=", 1)
                extra_facts[k.strip()] = v.strip()
        profile = profile_state.set_profile(
            ctx.conn,
            full_name=args.full_name,
            email=args.email,
            phone=args.phone,
            citizenship=args.citizenship,
            work_authorization=args.work_authorization,
            location=args.location,
            github_url=args.github,
            linkedin_url=args.linkedin,
            portfolio_url=args.portfolio,
            salary_expectation_notes=args.salary_notes,
            languages=languages,
            extra_facts=extra_facts,
        )
        print_kv(profile, title="Profile updated")
    return 0


# --------------------------------------------------------------------------- #
# resume
# --------------------------------------------------------------------------- #
def cmd_resume_add(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        rid = resume_state.add_resume(ctx.conn, args.track, args.file, args.notes)
        row = resume_state.get_resume(ctx.conn, rid)
        print(f"Added resume #{rid} ({row['track']}): {row['file_path']}")
        from pathlib import Path

        if not Path(row["file_path"]).exists():
            print("  ! warning: file does not exist yet at that path")
    return 0


def cmd_resume_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        print_table(
            resume_state.list_resumes(ctx.conn),
            [("id", "id"), ("track", "track"), ("file_path", "file"), ("notes", "notes")],
        )
    return 0


def cmd_resume_rm(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        resume_state.delete_resume(ctx.conn, args.id)
        print(f"Deleted resume #{args.id}")
    return 0


def cmd_resume_update(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        resume_state.update_resume(
            ctx.conn, args.id, track=args.track, file_path=args.file, notes=args.notes
        )
        print(f"Updated resume #{args.id}")
    return 0


# --------------------------------------------------------------------------- #
# cover letters
# --------------------------------------------------------------------------- #
def cmd_cover_add(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        cid = cover_state.add_cover_letter(
            ctx.conn, args.name, args.file, track=args.track, is_template=args.template
        )
        print(f"Added cover letter #{cid} ({args.name})")
    return 0


def cmd_cover_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        print_table(
            cover_state.list_cover_letters(ctx.conn),
            [
                ("id", "id"),
                ("name", "name"),
                ("track", "track"),
                ("is_template", "template"),
                ("file_path", "file"),
            ],
        )
    return 0


def cmd_cover_rm(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        cover_state.delete_cover_letter(ctx.conn, args.id)
        print(f"Deleted cover letter #{args.id}")
    return 0


# --------------------------------------------------------------------------- #
# companies
# --------------------------------------------------------------------------- #
def cmd_company_add(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        cid = companies_state.add_company(
            ctx.conn,
            name=args.name,
            ats_type=args.ats,
            board_token=args.token,
            board_url=args.url,
            notes=args.notes,
            active=not args.inactive,
        )
        print(f"Upserted company #{cid} ({args.name})")
    return 0


def cmd_company_list(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        print_table(
            companies_state.list_companies(ctx.conn, active_only=args.active),
            [
                ("id", "id"),
                ("name", "name"),
                ("ats_type", "ats"),
                ("board_token", "token"),
                ("active", "active"),
            ],
        )
    return 0


def cmd_company_rm(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        companies_state.delete_company(ctx.conn, args.id)
        print(f"Deleted company #{args.id}")
    return 0


def cmd_company_sync(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        result = companies_state.sync_from_config(ctx.conn, ctx.config)
        print(f"Synced {result['synced']} companies from config.")
    return 0


def cmd_company_activate(args: argparse.Namespace) -> int:
    with AgentContext.create(args.config) as ctx:
        companies_state.set_active(ctx.conn, args.id, active=not args.off)
        print(f"Company #{args.id} active={not args.off}")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def _register_core(sub) -> None:
    p = sub.add_parser("init", help="initialize the database")
    p.add_argument("--sync-companies", action="store_true", help="also load watchlist from config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="show a state + pipeline overview")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("events", help="show the recent audit log")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_events)


def _register_profile(sub) -> None:
    grp = sub.add_parser("profile", help="manage the profile").add_subparsers(
        dest="subcommand", required=True
    )

    p = grp.add_parser("show", help="print the profile")
    p.set_defaults(func=cmd_profile_show)

    p = grp.add_parser("set", help="create/update the profile (partial updates ok)")
    p.add_argument("--full-name")
    p.add_argument("--email")
    p.add_argument("--phone")
    p.add_argument("--citizenship")
    p.add_argument("--work-authorization", dest="work_authorization")
    p.add_argument("--location")
    p.add_argument("--github", dest="github")
    p.add_argument("--linkedin", dest="linkedin")
    p.add_argument("--portfolio", dest="portfolio")
    p.add_argument("--salary-notes", dest="salary_notes")
    p.add_argument("--languages", help="comma-separated list (replaces existing)")
    p.add_argument("--fact", action="append", help="extra fact as key=value (repeatable)")
    p.set_defaults(func=cmd_profile_set)


def _register_resume(sub) -> None:
    grp = sub.add_parser("resume", help="manage resume variants").add_subparsers(
        dest="subcommand", required=True
    )

    p = grp.add_parser("add", help="add a resume variant")
    p.add_argument("--track", required=True, help="e.g. backend, data_scientist")
    p.add_argument("--file", required=True, help="path to the resume file")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_resume_add)

    p = grp.add_parser("list")
    p.set_defaults(func=cmd_resume_list)

    p = grp.add_parser("rm")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_resume_rm)

    p = grp.add_parser("update")
    p.add_argument("id", type=int)
    p.add_argument("--track")
    p.add_argument("--file")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_resume_update)


def _register_cover(sub) -> None:
    grp = sub.add_parser("cover", help="manage cover letters").add_subparsers(
        dest="subcommand", required=True
    )

    p = grp.add_parser("add")
    p.add_argument("--name", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--track")
    p.add_argument("--template", action="store_true", help="mark as a Jinja2 template")
    p.set_defaults(func=cmd_cover_add)

    p = grp.add_parser("list")
    p.set_defaults(func=cmd_cover_list)

    p = grp.add_parser("rm")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_cover_rm)


def _register_company(sub) -> None:
    grp = sub.add_parser("company", help="manage the company watchlist").add_subparsers(
        dest="subcommand", required=True
    )

    p = grp.add_parser("add")
    p.add_argument("--name", required=True)
    p.add_argument("--ats", required=True, help="greenhouse|lever|workable|comeet|other")
    p.add_argument("--token", help="ATS board slug (preferred)")
    p.add_argument("--url", help="explicit board URL")
    p.add_argument("--notes")
    p.add_argument("--inactive", action="store_true")
    p.set_defaults(func=cmd_company_add)

    p = grp.add_parser("list")
    p.add_argument("--active", action="store_true", help="only active companies")
    p.set_defaults(func=cmd_company_list)

    p = grp.add_parser("rm")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_company_rm)

    p = grp.add_parser("sync", help="load the watchlist from config.yaml")
    p.set_defaults(func=cmd_company_sync)

    p = grp.add_parser("activate")
    p.add_argument("id", type=int)
    p.add_argument("--off", action="store_true", help="deactivate instead")
    p.set_defaults(func=cmd_company_activate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobagent",
        description="Autonomous job-application preparation agent (human submits).",
    )
    parser.add_argument("--version", action="version", version=f"jobagent {__version__}")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    _register_core(sub)
    _register_profile(sub)
    _register_resume(sub)
    _register_cover(sub)
    _register_company(sub)
    # Later phases register here:
    _register_optional(sub)
    return parser


def _register_optional(sub) -> None:
    """Register subcommands from later phases if those modules are present.

    Keeps the CLI working even while phases are still being built.
    """
    for module_name, attr in [
        ("jobagent.source.cli", "register"),
        ("jobagent.score.cli", "register"),
        ("jobagent.tailor.cli", "register"),
        ("jobagent.prep.cli", "register"),
        ("jobagent.review.cli", "register"),
        ("jobagent.track.cli", "register"),
        ("jobagent.pipeline", "register_cli"),
    ]:
        try:
            mod = __import__(module_name, fromlist=[attr])
            getattr(mod, attr)(sub)
        except ImportError:
            continue


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # surface clean errors to the CLI user
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
