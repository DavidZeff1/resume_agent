"""Optional HTTP Basic auth for the web UI.

The UI exposes personal profile data, so any *public* deployment must be
protected. This middleware activates ONLY when ``JOBAGENT_WEB_PASSWORD`` is set
in the environment — so local use stays open and friction-free, while a hosted
deployment (e.g. Vercel) becomes password-gated by setting that one variable.
"""

from __future__ import annotations

import base64
import os
import secrets

from starlette.responses import PlainTextResponse


def auth_credentials() -> tuple[str, str] | None:
    """Return (username, password) if auth is configured, else None."""
    password = os.environ.get("JOBAGENT_WEB_PASSWORD")
    if not password:
        return None
    username = os.environ.get("JOBAGENT_WEB_USER", "admin")
    return username, password


class BasicAuthMiddleware:
    """Pure-ASGI HTTP Basic auth guard."""

    def __init__(self, app, username: str, password: str):
        self.app = app
        self.username = username
        self.password = password

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header = dict(scope.get("headers") or {}).get(b"authorization")
        if self._authorized(header):
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="jobagent"'},
        )
        await response(scope, receive, send)

    def _authorized(self, header: bytes | None) -> bool:
        if not header or not header.startswith(b"Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except Exception:
            return False
        # constant-time compares to avoid leaking length/timing
        return secrets.compare_digest(user, self.username) and secrets.compare_digest(
            pw, self.password
        )
