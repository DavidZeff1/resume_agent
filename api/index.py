"""Vercel serverless entrypoint.

Vercel's Python runtime serves the module-level ``app`` (an ASGI application).
Unlike ``jobagent web``, there is no long-lived uvicorn process here — Vercel
invokes this per request.

Environment assumptions for the hosted path (set in the Vercel project, see
DEPLOY-VERCEL.md):
  * ``JOBAGENT_DATA_DIR`` -> a writable dir (``/tmp/...``); the local filesystem
    is otherwise read-only on Vercel. Defaulted below.
  * ``JOBAGENT_DB_URL`` (+ ``JOBAGENT_DB_AUTH_TOKEN``) -> a Turso/libSQL database
    so state persists across invocations.
  * ``JOBAGENT_WEB_PASSWORD`` -> gate the UI (it shows personal data).

If the application fails to build (a missing dependency, a bad DB config, …),
we fall back to a tiny pure-ASGI handler that returns the actual traceback, so a
broken deploy shows *why* in the browser instead of an opaque 500. That fallback
deliberately avoids importing anything (not even Starlette) so it still works
when the failure is an import error.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Make the repo root importable so `import jobagent` works inside the function.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Default state to a writable location; real persistence comes from JOBAGENT_DB_URL.
os.environ.setdefault("JOBAGENT_DATA_DIR", "/tmp/jobagent")

try:
    from jobagent.web.app import create_app

    app = create_app()
except Exception:  # pragma: no cover - exercised only on a broken deploy
    _STARTUP_TRACEBACK = traceback.format_exc()

    async def app(scope, receive, send):  # type: ignore[no-redef]
        """Pure-ASGI diagnostic handler shown when startup failed."""
        if scope["type"] != "http":
            return
        body = (
            "jobagent failed to start.\n\n"
            "This is a deployment/configuration error, not a page bug. The most\n"
            "common causes are a dependency that did not install, or a bad\n"
            "JOBAGENT_DB_URL / JOBAGENT_DB_AUTH_TOKEN. Full traceback below.\n"
            "(Hit /healthz once this is fixed to confirm the database.)\n\n"
            f"{_STARTUP_TRACEBACK}"
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})
