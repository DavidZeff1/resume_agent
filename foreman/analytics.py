"""Analytics over the ledger — the project's evaluation surface.

Computes the metrics the build plan calls for: success rate, iterations-to-green,
cost, and the headline metric — human-touch rate (the share of tasks that still
needed a human, i.e. didn't sail through to a merge on their own).
"""

from __future__ import annotations

import sqlite3

from .models import TaskStatus
from .repo import status_counts


def compute_stats(conn: sqlite3.Connection) -> dict:
    counts = status_counts(conn)
    total = sum(counts.values())

    reached_pr = counts.get(TaskStatus.PR_READY, 0) + counts.get(TaskStatus.MERGED, 0)
    needed_human = counts.get(TaskStatus.NEEDS_HUMAN, 0)
    merged = counts.get(TaskStatus.MERGED, 0)

    impl = conn.execute(
        "SELECT AVG(turns) a, COUNT(*) n FROM runs WHERE role = 'implementer'"
    ).fetchone()
    cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) c FROM runs").fetchone()["c"]

    def pct(num: int) -> float:
        return round(100.0 * num / total, 1) if total else 0.0

    return {
        "tasks_total": total,
        "by_status": counts,
        "reached_pr_ready": reached_pr,
        "success_rate_pct": pct(reached_pr),         # got to a ready/merged PR
        "needed_human": needed_human,
        # Human-touch rate: in v1 every prepared PR needs a human to merge, so a
        # task is "human-touched" unless it merged autonomously (none do in v1).
        "human_touch_rate_pct": pct(total - merged) if total else 0.0,
        "avg_implementer_turns": round(impl["a"], 2) if impl["a"] is not None else None,
        "implementer_runs": impl["n"],
        "total_cost_usd": round(cost, 4),
    }
