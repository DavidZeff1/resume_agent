"""`python -m foreman.demo` — the whole multi-agent loop, fully offline.

Seeds a tiny workspace with a deliberately-broken function and a failing test,
then runs the orchestrator with deterministic (Mock) policies:

    orchestrator -> implementer (edit -> run_tests, red -> fix -> green)
                 -> reviewer (approve) -> pr_ready  [human would merge]

No API key, no network — this exercises the architecture end to end. Swap the
MockPolicy for LLMPolicy to put Claude in the decision seat.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .agent import Action, Agent, Percept
from .agents import ImplementerAgent, Orchestrator, ReviewerAgent
from .blackboard import Blackboard
from .environment import Workspace
from .models import TaskStatus
from .policy import MockPolicy
from .tools import EditFile, ReadFile, RunTests, SubmitReview

# A workspace with a bug: add() subtracts. check.py is the truth signal.
BUGGY_FILES = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "check.py": (
        "from calc import add\n\n"
        "assert add(2, 3) == 5, f'add is broken: got {add(2, 3)}'\n"
        "print('ok')\n"
    ),
}

TASK = {
    "title": "Fix add() in calc.py",
    "description": "add(a, b) must return a + b; the test in check.py must pass.",
}


# -- deterministic policies (stand-ins for the LLM) ------------------------- #
def implementer_decide(agent: Agent, percept: Percept) -> list[Action]:
    """Reactive: establish the test state, and if red, apply the fix and re-verify."""
    tr = percept.data.get("test_result")
    if tr is None:
        return [Action("run_tests", {}, "see where we stand")]
    if not tr.get("passed"):
        return [
            Action("edit_file", {"path": "calc.py", "find": "a - b", "replace": "a + b"},
                   "the bug: subtraction should be addition"),
            Action("run_tests", {}, "verify the fix"),
        ]
    return []  # green — is_done() will stop the loop


def reviewer_decide(agent: Agent, percept: Percept) -> list[Action]:
    if not percept.data.get("diff"):
        return [Action("submit_review", {"verdict": "request_changes",
                                         "findings": ["no changes in the diff"]})]
    return [Action("submit_review", {"verdict": "approve", "findings": []},
                   "small, correct, tested change")]


def build_team() -> Orchestrator:
    implementer = ImplementerAgent(
        "implementer", MockPolicy(implementer_decide),
        [ReadFile(), EditFile(), RunTests()],   # note: no merge/push tool
    )
    reviewer = ReviewerAgent(
        "reviewer", MockPolicy(reviewer_decide),
        [ReadFile(), SubmitReview()],            # read-only + post a verdict
    )
    return Orchestrator(implementer, reviewer)


def seed_workspace(root) -> Workspace:
    return Workspace.seed(root, BUGGY_FILES)


def main() -> int:
    # An in-memory ledger so the demo also exercises persistence + analytics offline.
    from .analytics import compute_stats
    from .db import get_conn
    from .repo import create_task, list_runs

    with tempfile.TemporaryDirectory() as tmp:
        ws = seed_workspace(Path(tmp))
        conn = get_conn(":memory:")
        task_id = create_task(conn, title=TASK["title"], description=TASK["description"],
                              source="demo")
        bb = Blackboard()
        bb.set("task", TASK)
        bb.set("status", TaskStatus.QUEUED)

        print(f"workspace: {ws.root}")
        print(f"task #{task_id}: {TASK['title']}")
        final = build_team().run(ws, bb, conn=conn, task_id=task_id)

        tr = bb.get("test_result") or {}
        review = bb.get("review") or {}
        print(f"\ntests passed : {tr.get('passed')}")
        print(f"review       : {review.get('verdict')}")
        print(f"final status : {final}")
        print(f"runs recorded: {len(list_runs(conn, task_id))}  "
              f"events: {len(bb.events)}")
        print("\n--- diff prepared for the human ---")
        print(ws.diff() or "(none)")
        print("\n--- ledger analytics ---")
        for k, v in compute_stats(conn).items():
            print(f"  {k}: {v}")
        print("\n>>> a human would now review + merge this PR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
