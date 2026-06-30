r"""The agent loop: an LLM that calls tools until the task is done.

One function, ``run_agent``. It speaks the OpenAI-compatible chat API, so the
same loop runs on Groq, Ollama, Cerebras, OpenAI, etc. The shape is the classic
tool-use loop:

    user task -> model -> (tool calls?) -> run tools -> feed results -> repeat
                                       \-> (no tool calls) -> final answer
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from .config import Settings
from .tools.base import ToolRegistry

DEFAULT_SYSTEM = (
    "You are a capable autonomous assistant with access to tools. "
    "Plan briefly, then use the tools to gather information and act. "
    "Prefer calling a tool over guessing. When you have enough to answer, "
    "stop calling tools and reply directly and concisely."
)


@dataclass
class AgentResult:
    answer: str
    steps: int
    stopped_early: bool


def build_client(settings: Settings) -> OpenAI:
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key)


def run_agent(
    task: str,
    *,
    registry: ToolRegistry,
    settings: Settings,
    client: OpenAI | None = None,
    system: str = DEFAULT_SYSTEM,
    on_event: Callable[[str, str], None] | None = None,
) -> AgentResult:
    """Run the agent on ``task`` and return its final answer.

    ``on_event(kind, text)`` is called for observability with kinds
    ``"think"``, ``"tool_call"``, ``"tool_result"``, and ``"answer"``.
    """
    client = client or build_client(settings)
    emit = on_event or (lambda kind, text: None)

    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    tool_schemas = registry.schemas()

    for step in range(1, settings.max_steps + 1):
        completion = client.chat.completions.create(
            model=settings.model,
            messages=messages,
            tools=tool_schemas or None,
            tool_choice="auto" if tool_schemas else None,
        )
        msg = completion.choices[0].message

        # Record the assistant turn in a portable, minimal shape.
        assistant: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:
            answer = msg.content or ""
            emit("answer", answer)
            return AgentResult(answer=answer, steps=step, stopped_early=False)

        if msg.content:
            emit("think", msg.content)

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            emit("tool_call", f"{name}({_fmt_args(args)})")

            tool = registry.get(name)
            if tool is None:
                result = f"Error: unknown tool {name!r}"
            else:
                try:
                    result = tool.run(**args)
                except Exception as exc:  # tools must never crash the loop
                    result = f"Error running {name}: {type(exc).__name__}: {exc}"
            emit("tool_result", result)

            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    emit("answer", "(stopped: reached max steps)")
    return AgentResult(
        answer="(stopped without a final answer: reached max steps)",
        steps=settings.max_steps,
        stopped_early=True,
    )


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)
