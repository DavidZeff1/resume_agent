"""Prep: pre-fill an application from stored state, queue the rest for the human.

The guardrail-critical stage. Anything the agent can safely fill from the
profile goes into ``prefilled_data``; everything it must NOT answer — free-text
screening questions, salary, work-authorization explanations, anything not in
state — goes into ``unanswered_fields`` for the human (guardrail §2). Prep never
submits.
"""
