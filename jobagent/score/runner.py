"""Scoring runner: score discovered jobs, then shortlist or skip by threshold."""

from __future__ import annotations

from ..context import AgentContext
from ..events import log_event
from ..logging_setup import get_logger
from ..models import JobStatus
from ..repo import list_jobs_by_status, set_job_status, update_job_score
from ..state import profile as profile_state
from ..state import resumes as resume_state
from .base import get_scorer
from .heuristic import HeuristicScorer

log = get_logger("score.runner")


def run_score(ctx: AgentContext, rescore: bool = False, limit: int | None = None) -> dict:
    conn = ctx.conn
    cfg = ctx.config
    scorer = get_scorer(ctx)
    fallback = scorer if scorer.backend_name == "heuristic" else HeuristicScorer()
    threshold = cfg.scoring.shortlist_threshold

    profile = profile_state.get_profile(conn) or {}
    available_tracks = sorted({r["track"] for r in resume_state.list_resumes(conn)})

    statuses = [JobStatus.DISCOVERED]
    if rescore:
        statuses += [JobStatus.SKIPPED, JobStatus.SCORED]
    jobs = [j for st in statuses for j in list_jobs_by_status(conn, st)]
    jobs = jobs[: (limit or cfg.scoring.max_jobs_per_run)]

    summary = {
        "backend": scorer.backend_name, "scored": 0,
        "shortlisted": 0, "skipped": 0, "errors": 0,
    }

    for job in jobs:
        job_id = int(job["id"])
        try:
            result = scorer.score(dict(job), profile, available_tracks)
        except Exception as exc:  # transient LLM/parse error: fall back per-job
            log.warning("scorer failed for job #%s (%s); using heuristic", job_id, exc)
            try:
                result = fallback.score(dict(job), profile, available_tracks)
            except Exception as exc2:
                log.error("fallback also failed for job #%s: %s", job_id, exc2)
                summary["errors"] += 1
                continue

        update_job_score(conn, job_id, result.score, result.rationale, result.suggested_track)
        set_job_status(conn, job_id, JobStatus.SCORED)
        if result.score >= threshold:
            set_job_status(conn, job_id, JobStatus.SHORTLISTED)
            summary["shortlisted"] += 1
        else:
            set_job_status(conn, job_id, JobStatus.SKIPPED)
            summary["skipped"] += 1
        summary["scored"] += 1

    log_event(conn, "pipeline", None, "score_run", summary)
    log.info(
        "scored %d via %s: %d shortlisted, %d skipped",
        summary["scored"], summary["backend"], summary["shortlisted"], summary["skipped"],
    )
    return summary
