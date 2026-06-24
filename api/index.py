"""Vercel serverless entrypoint.

Vercel's Python runtime statically detects a module-level ``app`` (an ASGI
application) and serves it. Unlike ``jobagent web`` there is no long-lived
uvicorn process here — Vercel invokes this per request.

The ``app`` binding below must stay a *direct, top-level* assignment
(``app = _build_app()``). Vercel finds the entrypoint by scanning the module
body for an ``app`` / ``application`` / ``handler`` name; if the binding is
nested (e.g. inside a ``try``/``except``) the static scan misses it and the
build fails with "Could not find a top-level 'app' …". So all the fallible
work lives inside ``_build_app`` and the module always ends with one plain
assignment.

Environment assumptions for the hosted path (set in the Vercel project, see
DEPLOY-VERCEL.md):
  * ``JOBAGENT_DATA_DIR`` -> a writable dir (``/tmp/...``); the local filesystem
    is otherwise read-only on Vercel. Defaulted below.
  * ``JOBAGENT_DB_URL`` (+ ``JOBAGENT_DB_AUTH_TOKEN``) -> a Turso/libSQL database
    so state persists across invocations.
  * ``JOBAGENT_WEB_PASSWORD`` -> gate the UI (it shows personal data).

If the application fails to build (a missing dependency, a bad DB config, …),
``_build_app`` returns a tiny pure-ASGI handler that serves the actual traceback,
so a broken deploy shows *why* in the browser instead of an opaque 500. That
fallback deliberately avoids importing anything (not even Starlette) so it still
works when the failure is an import error.
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

# Foreman is mounted alongside job·agent at /foreman (view/demo only — see
# _build_app). Its ledger must live on the one writable path (/tmp), and runs must
# be synchronous: Vercel freezes the function after the response, so Foreman's
# default background-thread worker would never make progress here.
os.environ.setdefault("FOREMAN_DATA_DIR", "/tmp/foreman")
os.environ.setdefault("FOREMAN_SYNC", "1")


def _diagnostic_app(startup_traceback: str):
    """A pure-ASGI handler (no imports) that reports why startup failed."""

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        body = (
            "jobagent failed to start.\n\n"
            "This is a deployment/configuration error, not a page bug. The most\n"
            "common causes are a dependency that did not install, or a bad\n"
            "JOBAGENT_DB_URL / JOBAGENT_DB_AUTH_TOKEN. Full traceback below.\n"
            "(Hit /healthz once this is fixed to confirm the database.)\n\n"
            f"{startup_traceback}"
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _seed_foreman(db_path: str) -> None:
    """Populate the Foreman ledger with one finished offline sample.

    The mounted Foreman UI is view/demo only on Vercel (no persistent DB, no git,
    no long-lived worker). Seeding one completed task at cold start means every
    instance serves a populated, navigable dashboard instead of an empty shell.

    The sample is fully offline: a temp workspace whose truth signal is
    ``python check.py`` (no pytest, no git, no network, no API key), so it runs to
    ``pr_ready`` even inside the serverless sandbox. Best-effort — an empty
    dashboard is an acceptable fallback, so this never raises into the build.
    """
    try:
        from foreman import repo as _frepo
        from foreman.db import get_conn

        if _frepo.list_tasks(get_conn(db_path)):
            return  # already seeded on this (warm) instance

        from foreman.demo import BUGGY_FILES, TASK
        from foreman.runner import run_task

        run_task(title=TASK["title"], description=TASK["description"], source="demo",
                 policy="mock", seed_files=BUGGY_FILES, db_path=db_path)
    except Exception:  # pragma: no cover - cosmetic seeding only
        pass


def _build_app():
    """Build the real ASGI app, or a diagnostic fallback if construction fails.

    The app is job·agent (served at ``/``) with the Foreman control panel mounted
    at ``/foreman``. Foreman's mount is inserted into job·agent's own router so
    job·agent's auth middleware and error handlers cover both with no behaviour
    change. Foreman runs view/demo only here (see ``_seed_foreman``); it is fully
    functional locally via ``Foreman.command`` / ``python -m foreman web``.

    All the fallible work is contained here so the module body can end in a
    single, statically-detectable ``app = _build_app()`` (see module docstring).
    """
    try:
        from pathlib import Path

        from starlette.routing import Mount

        from jobagent.web.app import create_app

        app = create_app()

        try:
            from foreman.web.app import create_app as create_foreman_app

            foreman_db = str(Path(os.environ["FOREMAN_DATA_DIR"]) / "foreman.sqlite3")
            _seed_foreman(foreman_db)
            app.router.routes.insert(
                0, Mount("/foreman", app=create_foreman_app(db_path=foreman_db, prefix="/foreman"))
            )
        except Exception:  # Foreman is additive — never let it break job·agent.
            pass

        return app
    except Exception:  # pragma: no cover - exercised only on a broken deploy
        return _diagnostic_app(traceback.format_exc())


# Top-level ASGI application Vercel serves. Keep this a plain assignment.
app = _build_app()
