"""Foreman CLI.  Run ``python -m foreman --help``.

    foreman demo                 run the offline mock loop + analytics
    foreman run --title ...      run a task (use --policy llm + --repo for real work)
    foreman tasks                list tasks in the ledger
    foreman stats                analytics over the ledger
    foreman web                  launch the review-queue UI
"""

from __future__ import annotations

import argparse
import shlex
import sys

from .analytics import compute_stats
from .db import get_conn
from .demo import main as demo_main
from .repo import list_runs, list_tasks
from .runner import default_db_path, run_task


def cmd_demo(args: argparse.Namespace) -> int:
    return demo_main()


def cmd_run(args: argparse.Namespace) -> int:
    conn, task_id, status = run_task(
        title=args.title, description=args.description or "",
        target_repo=args.repo,
        test_cmd=shlex.split(args.test_cmd) if args.test_cmd else None,
        policy=args.policy, model=args.model,
        db_path=args.db or default_db_path(),
    )
    print(f"task #{task_id}: {status}")
    runs = list_runs(conn, task_id)
    for r in runs:
        print(f"  {r['role']:11} turns={r['turns']} "
              f"tests={r['tests_passed']} verdict={r['verdict']} "
              f"cost=${r['cost_usd']:.4f}")
    if status == "pr_ready":
        print("  >>> a prepared PR is waiting for a human to review + merge.")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    conn = get_conn(args.db or default_db_path())
    rows = list_tasks(conn, status=args.status)
    if not rows:
        print("(no tasks yet)")
        return 0
    for t in rows:
        print(f"#{t['id']:<4} {t['status']:<16} {t['title']}  "
              f"[{t['pr_ref'] or '-'}]")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = get_conn(args.db or default_db_path())
    for k, v in compute_stats(conn).items():
        print(f"{k}: {v}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The web UI needs uvicorn + starlette: pip install starlette uvicorn")
        return 1
    from .web.app import create_app
    app = create_app(db_path=str(args.db or default_db_path()))
    url = f"http://{args.host}:{args.port}"
    print(f"Foreman control panel: {url}   (ledger: {args.db or default_db_path()})")
    print("Open that link in your browser. Press Ctrl-C here to stop.")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="foreman",
                                description="Multi-agent dev system (human merges).")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("demo", help="run the offline mock loop + analytics")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("run", help="run a task end to end")
    sp.add_argument("--title", required=True)
    sp.add_argument("--description")
    sp.add_argument("--repo", help="path to a git repo to develop in (else a temp dir)")
    sp.add_argument("--test-cmd", help="shell command that is the truth signal")
    sp.add_argument("--policy", choices=["mock", "llm"], default="mock")
    sp.add_argument("--model", default="claude-opus-4-8")
    sp.add_argument("--db")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("tasks", help="list tasks in the ledger")
    sp.add_argument("--status")
    sp.add_argument("--db")
    sp.set_defaults(func=cmd_tasks)

    sp = sub.add_parser("stats", help="analytics over the ledger")
    sp.add_argument("--db")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("web", help="launch the browser control panel")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8011)
    sp.add_argument("--db")
    sp.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    sp.set_defaults(func=cmd_web)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # surface clean errors
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
