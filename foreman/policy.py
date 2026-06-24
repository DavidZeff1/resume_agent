"""Policy — the pluggable decision function of an agent.

The architecture (perceive -> decide -> act, coordination, environment, tools,
state machine) is independent of *how* an agent decides. That decision is a
strategy:

  * ``MockPolicy`` — deterministic, offline. Drives the whole multi-agent loop
    without a network or API key, so it is fully unit-tested (the same way
    jobagent's heuristic scorer keeps the pipeline testable).
  * ``LLMPolicy``  — Claude in the decision seat. Runs the manual agentic loop
    over the Messages API (build prompt -> call -> branch on stop_reason ->
    execute tool calls -> feed results back), with the agent's tools exposed as
    tool-use schemas. Needs the ``anthropic`` SDK + credentials, so it is the one
    part not exercised offline.
"""

from __future__ import annotations

from typing import Callable

from .agent import Action, Agent, Percept

# Per-million-token prices (input, output). Used to estimate run cost.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


class Policy:
    #: episodic policies run their own loop via ``run`` instead of single-step ``decide``.
    episodic: bool = False
    model: str = "mock"

    def decide(self, agent: "Agent", percept: "Percept") -> list["Action"]:
        raise NotImplementedError

    def run(self, agent: "Agent", env, bb) -> int:  # for episodic policies
        raise NotImplementedError


class MockPolicy(Policy):
    """Wraps a plain ``decide(agent, percept) -> [Action]`` function."""

    model = "mock"

    def __init__(self, decide_fn: Callable[["Agent", "Percept"], list["Action"]]) -> None:
        self._fn = decide_fn

    def decide(self, agent, percept):
        return self._fn(agent, percept)


class LLMPolicy(Policy):
    """Claude-driven decision policy: the manual agentic loop, hardened.

    Records token usage + estimated cost onto the blackboard (``last_usage``) so
    the orchestrator can write it to the ledger. Needs ``anthropic`` + a key.
    """

    episodic = True

    def __init__(self, model: str = "claude-opus-4-8", *, system: str = "",
                 effort: str = "xhigh", max_turns: int = 24, max_tokens: int = 8000,
                 client=None) -> None:
        self.model = model
        self.system = system
        self.effort = effort
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._client = client

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only with creds
            raise RuntimeError(
                "LLMPolicy needs the 'anthropic' SDK (pip install anthropic) and "
                "ANTHROPIC_API_KEY. Use MockPolicy for the offline demo/tests."
            ) from exc
        self._client = anthropic.Anthropic()
        return self._client

    def run(self, agent, env, bb) -> int:  # pragma: no cover - needs credentials
        client = self._ensure_client()
        tools = [t.schema() for t in agent.tools.values()]
        # Stable context (task, conventions) goes in a cached system block so each
        # turn re-reads it at ~0.1x cost.
        system = [{
            "type": "text",
            "text": self.system or agent.system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }]
        percept = agent.perceive(env, bb)
        messages = [{"role": "user", "content": agent.initial_prompt(percept)}]

        in_tok = out_tok = turns = 0
        try:
            while turns < self.max_turns and not agent.is_done(env, bb):
                turns += 1
                resp = client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    system=system, tools=tools, messages=messages,
                )
                u = resp.usage
                in_tok += (getattr(u, "input_tokens", 0)
                           + getattr(u, "cache_read_input_tokens", 0)
                           + getattr(u, "cache_creation_input_tokens", 0))
                out_tok += getattr(u, "output_tokens", 0)

                if resp.stop_reason == "refusal":
                    bb.log(agent.name, "refusal", None)
                    break
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in resp.content
                             if getattr(b, "type", None) == "tool_use"]
                if not tool_uses:                       # end_turn
                    break
                results = []
                for b in tool_uses:
                    obs = agent.act([Action(tool=b.name, args=dict(b.input))], env, bb)[0]
                    results.append({
                        "type": "tool_result", "tool_use_id": b.id,
                        "content": obs.result.output or ("ok" if obs.result.ok else "error"),
                        "is_error": not obs.result.ok,
                    })
                messages.append({"role": "user", "content": results})
        finally:
            bb.set("last_usage", {
                "model": self.model, "input": in_tok, "output": out_tok,
                "cost": estimate_cost(self.model, in_tok, out_tok),
            })
        return turns


def make_policy(name: str, **kwargs) -> Policy:
    """Factory for the LLM policy. (Mock policies are built with a decide_fn.)"""
    if name == "llm":
        return LLMPolicy(**kwargs)
    raise ValueError(f"unknown policy {name!r} (use 'llm', or build MockPolicy directly)")
