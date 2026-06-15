"""Phase 7 — the autonomous loop, tied together.

``run_once`` runs every *recoverable* stage in order (source -> score -> tailor
-> prep -> follow-up maintenance) and parks ready applications in the review
queue. The one unrecoverable stage — review & submit — is intentionally NOT
here; that is the human gate (build plan §6.5).

For the LLM-driven variant described in §3, ``run_agent_loop`` wraps these same
stage functions as Agent-SDK tools and lets Claude orchestrate them (still with
no submit tool). It is optional and used only when the SDK + credentials exist.
"""

from __future__ import annotations

import argparse

from .context import AgentContext
from .events import log_event
from .logging_setup import get_logger
from .models import ApplicationStatus
from .prep.runner import run_prep
from .repo import list_applications_by_status
from .score.runner import run_score
from .source.runner import run_source
from .tailor.runner import run_tailor
from .track.followup import run_followups

log = get_logger("pipeline")


def run_once(
    ctx: AgentContext,
    *,
    source: bool = True,
    score: bool = True,
    tailor: bool = True,
    prep: bool = True,
    followup: bool = True,
    only: str | None = None,
    rescore: bool = False,
) -> dict:
    """Run the recoverable pipeline once. Returns a per-stage summary."""
    results: dict = {}
    if source:
        results["source"] = run_source(ctx, only=only)
    if score:
        results["score"] = run_score(ctx, rescore=rescore)
    if tailor:
        results["tailor"] = run_tailor(ctx)
    if prep:
        results["prep"] = run_prep(ctx)
    if followup:
        results["followup"] = run_followups(ctx)

    queued = len(list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW))
    results["queued_for_review"] = queued
    log_event(ctx.conn, "pipeline", None, "run_once", _condense(results))
    return results


def _condense(results: dict) -> dict:
    out = {}
    for stage, summary in results.items():
        if isinstance(summary, dict):
            out[stage] = {k: v for k, v in summary.items() if isinstance(v, (int, float, str))}
        else:
            out[stage] = summary
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    if args.agent:
        return _cmd_run_agent(args)

    overrides = {"scoring": {"backend": args.backend}} if args.backend else None
    with AgentContext.create(args.config, overrides=overrides) as ctx:
        results = run_once(
            ctx,
            source=not args.no_source,
            followup=not args.no_followup,
            only=args.only,
            rescore=args.rescore,
        )
        _print_summary(results)
    return 0


def _print_summary(results: dict) -> None:
    print("\n=== Pipeline run complete ===")
    s = results.get("source")
    if s:
        print(f"  source : {s['new']} new, {s['updated']} updated, {s['errors']} errors")
    sc = results.get("score")
    if sc:
        print(f"  score  : {sc['scored']} scored ({sc['backend']}) -> "
              f"{sc['shortlisted']} shortlisted, {sc['skipped']} skipped")
    t = results.get("tailor")
    if t:
        print(f"  tailor : {t['prepared']} prepared")
    p = results.get("prep")
    if p:
        print(f"  prep   : {p['queued']} queued for review "
              f"({p['used_real_questions']} via real ATS questions)")
    f = results.get("followup")
    if f:
        print(f"  follow : {f['drafted']} follow-up draft(s)")
    print(f"\n  >>> {results.get('queued_for_review', 0)} application(s) waiting in the "
          f"review queue (the human gate).")
    print("  Next:  jobagent review list")


def _cmd_run_agent(args: argparse.Namespace) -> int:
    from .pipeline_agent import run_agent_loop

    overrides = {"scoring": {"backend": args.backend}} if args.backend else None
    with AgentContext.create(args.config, overrides=overrides) as ctx:
        return run_agent_loop(ctx)


def register_cli(sub) -> None:
    p = sub.add_parser("run", help="run the whole recoverable pipeline once (human still submits)")
    p.add_argument("--no-source", action="store_true", help="skip sourcing (use existing jobs)")
    p.add_argument("--no-followup", action="store_true", help="skip follow-up drafting")
    p.add_argument("--rescore", action="store_true", help="also re-score skipped/scored jobs")
    p.add_argument("--only", help="only source companies whose name contains this string")
    p.add_argument("--backend", choices=["auto", "heuristic", "claude"], help="scoring backend")
    p.add_argument("--agent", action="store_true",
                   help="drive the loop with the Claude Agent SDK (needs SDK + credentials)")
    p.set_defaults(func=cmd_run)
