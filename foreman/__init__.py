"""Foreman — a multi-agent system that develops software (target: jobagent).

This package is the *agent architecture* made real. It implements the classic
building blocks of an intelligent multi-agent system:

  * Agent          — an autonomous entity running a perceive -> decide -> act loop
                     (sense-plan-act). Foreman's agents are goal-based and
                     deliberative (they pursue an explicit goal), reactive (they
                     respond to test results), proactive (they keep working toward
                     the goal), and social (they coordinate through a shared
                     Blackboard).
  * Environment    — the world the agents perceive and act on: a code workspace
                     (files + a test runner). See ``environment.Workspace``.
  * Blackboard     — the shared-memory coordination mechanism (blackboard
                     architecture). Agents communicate by reading/writing it; an
                     append-only event log makes every action auditable.
  * Tool           — a typed actuator an agent may invoke (read/edit files, run
                     tests). The set of tools *is* the agent's action space — and
                     the deliberate absence of a merge/push tool is the guardrail.
  * Policy         — the pluggable decision function. ``MockPolicy`` is a
                     deterministic, offline policy used by the tests/demo;
                     ``LLMPolicy`` is the Claude Messages-API integration seam.

The control loop and coordination run today with the deterministic policy
(``foreman.demo`` / the tests). The LLM policy is where Claude plugs in.
"""

from __future__ import annotations

__version__ = "0.0.1"
