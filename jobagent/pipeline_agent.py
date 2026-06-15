"""Optional Agent-SDK loop (build plan §3: orchestration via Claude Agent SDK).

Each recoverable stage is exposed as an in-process SDK tool and Claude decides
how to run them. The guardrail is enforced at the tool surface: there is NO
submit tool, so the agent *cannot* submit even if it wanted to — review & submit
stays a human action (§2, §6.5).

This path needs the SDK plus working credentials. The deterministic
``pipeline.run_once`` is the always-available equivalent and the one used in
tests; this wrapper is for when you want the model to drive.
"""

from __future__ import annotations

from .context import AgentContext
from .logging_setup import get_logger
from .prep.runner import run_prep
from .score.runner import run_score
from .source.runner import run_source
from .tailor.runner import run_tailor
from .track.followup import run_followups

log = get_logger("pipeline.agent")

TOOL_NAMES = ["source", "score", "tailor", "prep", "followup", "status"]

SYSTEM_PROMPT = """\
You orchestrate a LOCAL job-application PREPARATION pipeline. You have tools to:
  source  - fetch new roles from the human's company watchlist
  score   - rank discovered jobs; shortlist or skip
  tailor  - attach the best stored resume + cover letter to shortlisted jobs
  prep    - pre-fill applications from stored profile data; queue them for review
  followup- draft follow-ups for applications that have gone quiet
  status  - read current pipeline counts

HARD RULES (do not violate):
- You must NEVER submit an application. There is deliberately no submit tool.
  Review and submission are the HUMAN's job.
- Never invent facts about the candidate. The tools only use stored state.
Run the recoverable stages in a sensible order (source, score, tailor, prep,
then followup), then report how many applications are queued for human review.
Be concise.
"""

DRIVE_PROMPT = (
    "Run the recoverable job-application pipeline now, then tell me how many "
    "applications are queued for review. Do not submit anything."
)


def _build_tools(ctx: AgentContext):
    from claude_agent_sdk import tool

    def _text(payload) -> dict:
        return {"content": [{"type": "text", "text": str(payload)}]}

    @tool("source", "Fetch new roles from the configured watchlist.", {"only": str})
    async def source_tool(args):
        return _text(run_source(ctx, only=args.get("only") or None))

    @tool("score", "Score discovered jobs and shortlist or skip them.", {})
    async def score_tool(args):
        return _text(run_score(ctx))

    @tool("tailor", "Attach the best resume + cover letter to shortlisted jobs.", {})
    async def tailor_tool(args):
        return _text(run_tailor(ctx))

    @tool("prep", "Pre-fill prepared applications and queue them for review.", {})
    async def prep_tool(args):
        return _text(run_prep(ctx))

    @tool("followup", "Draft follow-ups for applications that have gone quiet.", {})
    async def followup_tool(args):
        return _text(run_followups(ctx))

    @tool("status", "Return current pipeline counts by status.", {})
    async def status_tool(args):
        rows = ctx.conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status"
        ).fetchall()
        return _text({r["status"]: r["n"] for r in rows})

    return [source_tool, score_tool, tailor_tool, prep_tool, followup_tool, status_tool]


def build_server(ctx: AgentContext):
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server("jobagent", "0.1", tools=_build_tools(ctx))


def build_options(ctx: AgentContext):
    from claude_agent_sdk import ClaudeAgentOptions

    server = build_server(ctx)
    return ClaudeAgentOptions(
        mcp_servers={"jobagent": server},
        allowed_tools=[f"mcp__jobagent__{name}" for name in TOOL_NAMES],
        disallowed_tools=["Bash", "Write", "Edit"],  # belt and suspenders
        system_prompt=SYSTEM_PROMPT,
        model=ctx.config.scoring.model,
        permission_mode="default",
        max_turns=20,
    )


def run_agent_loop(ctx: AgentContext) -> int:
    """Drive the pipeline with the Agent SDK. Returns a process exit code."""
    try:
        import anyio
        from claude_agent_sdk import (
            AssistantMessage,
            CLINotFoundError,
            ResultMessage,
            TextBlock,
        )
    except ImportError as exc:
        print(f"Agent SDK not available: {exc}. Use `jobagent run` (no --agent).")
        return 1

    async def _go() -> None:
        from claude_agent_sdk import query

        options = build_options(ctx)
        async for message in query(prompt=DRIVE_PROMPT, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        print(block.text)
            elif isinstance(message, ResultMessage):
                print("\n[agent loop complete]")

    try:
        anyio.run(_go)
        return 0
    except CLINotFoundError:
        print(
            "The Claude Code CLI was not found, which the Agent SDK needs.\n"
            "Use the deterministic pipeline instead:  jobagent run"
        )
        return 1
    except Exception as exc:  # don't crash the CLI on agent/transport errors
        log.error("agent loop failed: %s", exc)
        print(f"Agent loop failed ({exc}). Falling back is easy:  jobagent run")
        return 1
