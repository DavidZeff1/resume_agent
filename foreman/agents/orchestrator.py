"""Orchestrator — the deterministic control agent.

It drives a task through the state machine, dispatching the implementer and the
reviewer, running the bounded review-cycle loop, and (when given a ledger
connection) persisting every status change, run, and event. No LLM here — you
don't need a model to manage a queue and a state machine, and keeping it plain
code is what makes the irreversible boundary (no merge) trivially enforceable.
"""

from __future__ import annotations

from .. import repo as _repo
from ..blackboard import Blackboard
from ..environment import Workspace
from ..models import TaskStatus, validate_transition


class Orchestrator:
    name = "orchestrator"
    role = "control"

    def __init__(self, implementer, reviewer, *, max_steps: int = 12,
                 max_review_cycles: int = 2) -> None:
        self.implementer = implementer
        self.reviewer = reviewer
        self.max_steps = max_steps
        self.max_review_cycles = max_review_cycles
        self._conn = None
        self._task_id = None

    # -- ledger-aware state transition -------------------------------------- #
    def _to(self, bb: Blackboard, target: str) -> None:
        current = bb.get("status", TaskStatus.QUEUED)
        validate_transition(current, target)        # raises on an illegal jump
        bb.set("status", target)
        bb.log(self.name, "status", {"from": current, "to": target})
        if self._conn is not None and self._task_id is not None:
            # Write through to the ledger immediately so the UI shows live progress.
            _repo.set_task_status(self._conn, self._task_id, target)
            _repo.log_event(self._conn, task_id=self._task_id, actor=self.name,
                            action="status", detail={"to": target})

    def _record_run(self, agent, bb: Blackboard, role: str, turns: int,
                    verdict: str | None = None) -> None:
        if self._conn is None:
            return
        usage = bb.get("last_usage") or {}
        tr = bb.get("test_result") or {}
        _repo.add_run(
            self._conn, task_id=self._task_id, role=role,
            model=usage.get("model", getattr(agent.policy, "model", "mock")),
            turns=turns,
            tests_passed=tr.get("passed") if role == "implementer" else None,
            verdict=verdict,
            input_tokens=usage.get("input", 0), output_tokens=usage.get("output", 0),
            cost_usd=usage.get("cost", 0.0),
        )
        _repo.log_event(self._conn, task_id=self._task_id, actor=role, action="run",
                        detail={"role": role, "turns": turns, "verdict": verdict})
        bb.set("last_usage", None)

    # -- the loop ----------------------------------------------------------- #
    def run(self, env: Workspace, bb: Blackboard, *, conn=None, task_id=None) -> str:
        self._conn, self._task_id = conn, task_id
        if bb.get("status") is None:
            bb.set("status", TaskStatus.QUEUED)

        self._to(bb, TaskStatus.PLANNING)
        self._to(bb, TaskStatus.IMPLEMENTING)

        cycle = 0
        while True:
            if conn is not None:
                _repo.bump_attempts(conn, task_id)
            turns = self.implementer.run(env, bb, max_steps=self.max_steps)
            self._record_run(self.implementer, bb, "implementer", turns)

            self._to(bb, TaskStatus.TESTING)
            if not (bb.get("test_result") or {}).get("passed"):
                self._to(bb, TaskStatus.NEEDS_HUMAN)      # still red, budget spent
                break

            self._to(bb, TaskStatus.REVIEWING)
            bb.set("review", None)                        # re-review from scratch
            rturns = self.reviewer.run(env, bb, max_steps=self.max_steps)
            review = bb.get("review") or {}
            self._record_run(self.reviewer, bb, "reviewer", rturns,
                             verdict=review.get("verdict"))

            if review.get("verdict") == "approve":
                self._to(bb, TaskStatus.PR_READY)          # ready for the human
                break

            cycle += 1
            if cycle >= self.max_review_cycles:
                self._to(bb, TaskStatus.NEEDS_HUMAN)
                break
            bb.set("review_findings", review.get("findings"))
            self._to(bb, TaskStatus.IMPLEMENTING)          # address the findings

        return bb.get("status")
