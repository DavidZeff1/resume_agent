"""Sourcing: read the human-defined watchlist of ATS boards for new roles.

Guardrails (§2) enforced in this package:
  * Official JSON APIs preferred (Greenhouse, Lever) over HTML scraping.
  * robots.txt respected for HTML scraping; all fetches rate-limited + cached.
  * Only the configured watchlist is read — never the open web, never LinkedIn.
"""
