"""Vercel serverless entrypoint.

Vercel's Python runtime serves the module-level ``app`` (an ASGI application).
Unlike ``jobagent web``, there is no long-lived uvicorn process here — Vercel
invokes this per request.

Two environment assumptions for the hosted path (set in the Vercel project, see
DEPLOY-VERCEL.md):
  * ``JOBAGENT_DATA_DIR`` -> a writable dir (``/tmp/...``); the local filesystem
    is otherwise read-only on Vercel.
  * ``JOBAGENT_DB_URL`` (+ ``JOBAGENT_DB_AUTH_TOKEN``) -> a Turso/libSQL database
    so state persists across invocations.
  * ``JOBAGENT_WEB_PASSWORD`` -> gate the UI (it shows personal data).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo root importable so `import jobagent` works inside the function.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Default state to a writable location; real persistence comes from JOBAGENT_DB_URL.
os.environ.setdefault("JOBAGENT_DATA_DIR", "/tmp/jobagent")

from jobagent.web.app import create_app  # noqa: E402

app = create_app()
