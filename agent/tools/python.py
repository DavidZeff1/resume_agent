"""A code-execution tool.

Lets the agent compute, transform data, or test ideas by running Python and
capturing stdout. Runs in-process on your machine, so treat it like a local
REPL — capable and unsandboxed.
"""

from __future__ import annotations

import contextlib
import io
import traceback

from . import registry


@registry.tool
def run_python(code: str) -> str:
    """Execute Python code and return whatever it prints to stdout.

    Use ``print(...)`` to surface results. Imports and multi-line code are fine.

    Args:
        code: the Python source to execute
    """
    buf = io.StringIO()
    sandbox: dict = {"__name__": "__agent__"}
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<agent>", "exec"), sandbox)  # noqa: S102
    except Exception:
        return buf.getvalue() + "\n" + traceback.format_exc(limit=3)
    out = buf.getvalue().strip()
    return out or "(ran successfully; no stdout)"
