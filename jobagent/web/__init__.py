"""Optional local web UI for jobagent (Starlette + Jinja2, served by uvicorn).

The UI is a thin view over the same data layer the CLI uses — it never adds a
submit-to-site code path (the human still records their own submission), so
every guardrail in the README holds here too.
"""
