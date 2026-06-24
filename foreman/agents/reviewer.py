"""ReviewerAgent — fresh eyes on the diff.

A read-only agent: it perceives the diff (and the green test result) and decides
a verdict — ``approve`` or ``request_changes`` with findings — which it posts to
the blackboard via its one actuator, ``submit_review``. A separate-context
reviewer catches more than implementer self-critique, so it is its own agent.
"""

from __future__ import annotations

from ..agent import Agent, Percept


class ReviewerAgent(Agent):
    role = "reviewer"

    def perceive(self, env, bb) -> Percept:
        return Percept(self.role, {
            "task": bb.get("task"),
            "diff": env.diff(),
            "test_result": bb.get("test_result"),
        })

    def is_done(self, env, bb) -> bool:
        return bb.get("review") is not None

    def system_prompt(self) -> str:
        return (
            "You are a code reviewer. Review the diff against the task. Report every "
            "issue you find with a severity; a downstream step filters. Then call "
            "submit_review with 'approve' or 'request_changes'. You cannot edit or merge."
        )

    def initial_prompt(self, percept: Percept) -> str:
        task = percept.data.get("task") or {}
        return (f"TASK: {task.get('title','')}\n\nDIFF:\n{percept.data.get('diff','')}\n\n"
                "Review it, then submit_review.")
