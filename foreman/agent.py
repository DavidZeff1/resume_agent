"""Agent — the autonomous perceive -> decide -> act entity.

Every worker agent in Foreman is an instance of this base class. One ``step`` is
one turn of the classic agent cycle:

    perceive(env, blackboard) -> Percept     # sense the world + shared state
    decide(percept)           -> [Action]    # via the pluggable Policy
    act(actions)              -> [Observation]  # invoke tools (actuators)

``run`` repeats the cycle until the agent's goal is reached (``is_done``) or a
step budget is hit. Agents are autonomous (they own their loop), reactive (they
respond to test results in the percept), proactive (they keep acting toward the
goal), and social (they read/write the shared Blackboard).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from .blackboard import Blackboard
from .environment import Workspace
from .tools import Tool, ToolResult


@dataclass
class Percept:
    """What an agent senses on one cycle."""
    role: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    """An intended tool invocation (an actuator call)."""
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Observation:
    action: Action
    result: ToolResult


@dataclass
class StepResult:
    percept: Percept
    actions: list[Action]
    observations: list[Observation]


class Agent(abc.ABC):
    role: str = "agent"

    def __init__(self, name: str, policy, tools: list[Tool]) -> None:
        self.name = name
        self.policy = policy
        self.tools: dict[str, Tool] = {t.name: t for t in tools}

    # -- sense / decide / act ----------------------------------------------- #
    @abc.abstractmethod
    def perceive(self, env: Workspace, bb: Blackboard) -> Percept:
        ...

    def decide(self, percept: Percept) -> list[Action]:
        return self.policy.decide(self, percept)

    def act(self, actions: list[Action], env: Workspace, bb: Blackboard) -> list[Observation]:
        obs: list[Observation] = []
        for a in actions:
            tool = self.tools.get(a.tool)
            if tool is None:
                res = ToolResult(False, f"unknown tool: {a.tool}")
            else:
                res = tool.run(env, bb, **a.args)
            bb.log(self.name, f"act:{a.tool}", {"args": a.args, "ok": res.ok})
            obs.append(Observation(a, res))
        return obs

    def step(self, env: Workspace, bb: Blackboard) -> StepResult:
        percept = self.perceive(env, bb)
        actions = self.decide(percept)
        observations = self.act(actions, env, bb)
        return StepResult(percept, actions, observations)

    def is_done(self, env: Workspace, bb: Blackboard) -> bool:
        return False

    def run(self, env: Workspace, bb: Blackboard, max_steps: int = 12) -> int:
        """Run the agent to its goal. Returns the number of steps/turns taken."""
        if getattr(self.policy, "episodic", False):
            return self.policy.run(self, env, bb)
        steps = 0
        while steps < max_steps and not self.is_done(env, bb):
            self.step(env, bb)
            steps += 1
        return steps

    # -- hooks the LLM policy uses (overridden by concrete agents) ----------- #
    def system_prompt(self) -> str:
        return f"You are the {self.role} agent."

    def initial_prompt(self, percept: Percept) -> str:
        return str(percept.data)
