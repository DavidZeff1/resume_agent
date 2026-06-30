"""Command-line entrypoint.

    python -m agent "find the latest Python release and write it to version.txt"
    python -m agent                 # interactive REPL
    python -m agent --provider ollama "summarize https://example.com"
"""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .loop import build_client, run_agent
from .tools import registry

# ANSI colors (skipped if not a tty).
_C = {"dim": "\033[2m", "cyan": "\033[36m", "green": "\033[32m", "reset": "\033[0m"}


def _printer(quiet: bool):
    color = sys.stdout.isatty()

    def c(key: str, text: str) -> str:
        return f"{_C[key]}{text}{_C['reset']}" if color else text

    def emit(kind: str, text: str) -> None:
        if quiet:
            return
        if kind == "tool_call":
            print(c("cyan", f"  → {text}"))
        elif kind == "tool_result":
            snippet = text if len(text) <= 200 else text[:200] + " …"
            print(c("dim", f"    {snippet.strip()}"))
        elif kind == "think":
            print(c("dim", f"  {text.strip()}"))

    return emit, c


def run_once(task: str, settings: Settings, client, quiet: bool) -> int:
    emit, c = _printer(quiet)
    if not quiet:
        print(c("dim", f"[{settings.provider}:{settings.model}] {len(registry)} tools"))
    result = run_agent(
        task, registry=registry, settings=settings, client=client, on_event=emit
    )
    if not quiet:
        print(c("green", "\n● answer"))
    print(result.answer)
    return 0


def repl(settings: Settings, client) -> int:
    emit, c = _printer(quiet=False)
    print(c("green", f"tiny-agent [{settings.provider}:{settings.model}] — Ctrl-D to exit"))
    while True:
        try:
            task = input(c("cyan", "\n› "))
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not task.strip():
            continue
        result = run_agent(
            task, registry=registry, settings=settings, client=client, on_event=emit
        )
        print(c("green", "\n● ") + result.answer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="A tiny tool-using agent.")
    parser.add_argument("task", nargs="*", help="the task; omit for an interactive REPL")
    parser.add_argument("--provider", help="groq | ollama | cerebras | openai")
    parser.add_argument("--model", help="override the model id")
    parser.add_argument("--quiet", action="store_true", help="print only the final answer")
    args = parser.parse_args(argv)

    settings = Settings.from_env(provider=args.provider, model=args.model)
    client = build_client(settings)

    if args.task:
        return run_once(" ".join(args.task), settings, client, args.quiet)
    return repl(settings, client)


if __name__ == "__main__":
    raise SystemExit(main())
