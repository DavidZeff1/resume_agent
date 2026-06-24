"""ImplementerAgent — writes code until the truth signal is green.

A goal-based, reactive agent: it perceives the task and the latest test result,
decides on edits (via its Policy), acts through the file/test tools, and repeats.
Its goal — and its ``is_done`` condition — is a passing test suite.
"""

from __future__ import annotations

from ..agent import Agent, Percept


class ImplementerAgent(Agent):
    role = "implementer"

    def perceive(self, env, bb) -> Percept:
        return Percept(self.role, {
            "task": bb.get("task"),
            "test_result": bb.get("test_result"),
            "files": env.list_files(),
            "diff": env.diff(),
        })

    def is_done(self, env, bb) -> bool:
        tr = bb.get("test_result")
        return bool(tr and tr.get("passed"))

    # -- LLM policy hooks ---------------------------------------------------- #
    def system_prompt(self) -> str:
        return (
            "You are an implementer agent. Make the task's tests pass by editing "
            "files in the workspace. Use run_tests to verify. Work only within the "
            "workspace; you have no ability to merge or push. Stop when tests pass."
        )

    def initial_prompt(self, percept: Percept) -> str:
        task = percept.data.get("task") or {}
        return (f"TASK: {task.get('title','')}\n{task.get('description','')}\n\n"
                f"Files: {percept.data.get('files')}\n"
                "Make the tests pass.")
