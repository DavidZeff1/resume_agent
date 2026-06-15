"""State layer: CRUD over the human-authored inputs.

These are the inputs the agent's quality depends on (build plan §5): the
profile, per-track resume variants, the cover-letter library, and the company
watchlist. Everything here is plain SQLite the human can also edit by hand.
"""

from . import companies, cover_letters, profile, resumes  # noqa: F401
