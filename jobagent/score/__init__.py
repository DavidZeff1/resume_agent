"""Scoring: rank each discovered job against the profile + available tracks.

This stage is fully autonomous and risk-free: a wrong score only skips or
shortlists a job, both recoverable (build plan §6.2). The scorer is pluggable —
a deterministic heuristic by default, Claude when configured.
"""
