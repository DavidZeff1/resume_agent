# Foreman — Autonomous Dev-Agent System — Build Plan

> A spec for Claude Code. Read this whole file before writing code. *"Foreman"* is a
> placeholder name — rename freely. This system is the deliverable; the repo it
> develops is **jobagent itself**, gated by jobagent's own test suite.
>
> The design choices here are deliberate — especially the guardrails in §2. They
> are the same choices `job-agent-build-plan.md` made, applied one level up: this
> is an agent system that builds software, governed by the principle the software
> it builds is governed by. Do not "improve" the plan by removing the human merge
> step or letting agents push to `main`.

---

## 1. Goal & Philosophy

Build a local, persistent **multi-agent system that performs the entire
software-development pipeline except the final merge.** Given an issue or task,
it plans the work, writes code on an isolated branch, runs the tests, reviews its
own diff, and parks a **ready-to-merge pull request** for a human. The human
reviews and merges.

The organizing principle is inherited verbatim from jobagent — the
**recoverable vs. unrecoverable asymmetry**:

- **Recoverable stages** (plan, edit, test, review, iterate): a mistake is a bad
  commit *on a branch* — `git worktree remove` and it never happened. No lasting
  harm. **Optimize these for autonomy and throughput.**
- **Unrecoverable stage** (merge to `main` / release): a botched merge enters
  shared history, breaks teammates, and may ship. **Do not optimize this for
  volume. Keep a human on it.**

The success metric is **not "PRs opened."** It is **human-touch rate** — the
fraction of prepared PRs that still need a human before merge. v1 keeps it at
100% (you review every merge). It is driven down later, per *proven-clean change
class* (docs-only, dependency bumps, …), never by removing the human up front
(§10).

**The truth signal is non-negotiable.** Agents iterate against an *objective*
gate — jobagent's `pytest` suite (62 tests today), plus type-check and lint — not
against an LLM's self-assessment. An LLM grading its own code is the exact place
this system would fabricate confidence; the test runner is what prevents it.

**The one human step in v1:** review a green, fully-prepared pull request, then
merge it.

---

## 2. Non-Goals & Guardrails (hard rules)

Invariants the code must enforce, not suggestions.

- [ ] **Never merge or push to `main` in v1.** Every change lands on a branch and
      surfaces as a PR (or a local branch + diff). There is **no merge tool and no
      `git push origin main` code path.** Merge is a human action.
- [ ] **Never run outside an isolated git worktree.** Each task gets its own
      worktree on its own branch. The agent's blast radius is that worktree.
- [ ] **A change is never "done" until the truth signal is green.** Tests +
      type-check + lint must pass before a PR is prepared. A red gate routes back
      to the implementer, not to the human.
- [ ] **No destructive shell.** The `bash` tool runs behind an **allowlist** of
      executables and rejects shell operators (`&&`, `|`, `;`, `` ` ``, `$()`),
      `rm -rf`, `git push`, network installs to global env, and anything outside
      the worktree. Blocklists are insufficient — allowlist.
- [ ] **File edits are confined to the worktree root.** Resolve every
      model-supplied path to canonical form and reject `..`, symlinks, and
      absolute paths that escape the root.
- [ ] **Bounded loops and bounded cost.** Every agentic loop has a hard
      `max_turns` and a token/$ budget. Exhausting it parks the task as
      `needs_human`, never an infinite spin.
- [ ] **Secrets via environment only.** `ANTHROPIC_API_KEY` (and any git token)
      come from env/`.env` (gitignored). Never committed, never logged, never
      passed to a tool result.
- [ ] **All state is inspectable.** A single SQLite file (the task ledger) plus an
      append-only `events` table. Every tool call, test run, and review verdict is
      auditable after the fact.

---

## 3. Architecture Overview

### The loop

```
[Human: file an issue / task — title + description + acceptance notes]
                              │
                              ▼
  ┌──────────── ORCHESTRATOR (deterministic Python, no LLM) ───────────┐
  │  1. PLAN     turn the task into 1+ work items; record in SQLite     │
  │  2. ISOLATE  create a git worktree + branch per work item           │
  │  3. drive the recoverable loop below per work item                  │
  └───────────────────────────────┬────────────────────────────────────┘
                                   ▼
  ┌──────────── IMPLEMENTER (model loop: Messages API + tool use) ──────┐
  │  edit code (text_editor) · run commands (bash) · run_tests          │
  │  iterate until the truth signal is GREEN or the budget is spent     │
  └───────────────────────────────┬────────────────────────────────────┘
                                   ▼ tests green
  ┌──────────── REVIEWER (model loop: read-only tools + the diff) ──────┐
  │  critique the diff vs. the task + repo guardrails                   │
  │  request_changes → back to implementer ↺   |   approve → continue   │
  └───────────────────────────────┬────────────────────────────────────┘
                                   ▼ approved + green
                         prepare PR / branch + summary
                                   │
              [Human: review + merge]  ◀── the only human step
```

The **orchestrator is plain code**, exactly like jobagent's `pipeline.run_once`
— you don't need a model to manage a queue, a worktree, and a state machine, and
keeping it deterministic makes the whole system testable and cheap. (An optional
LLM-planner variant can replace step 1 later, mirroring jobagent's
`run_agent_loop` — but it is not v1.)

The **implementer and reviewer are model loops**: a hand-rolled agentic loop over
the **Messages API + tool use** (your chosen foundation). Each is its own
`messages.create` loop with a role-specialized system prompt and a scoped tool
set.

### Tech stack & rationale

- **Language: Python 3.10+.** Matches jobagent; one repo, one toolchain.
- **Model access: the official `anthropic` SDK, Messages API + tool use.** A
  hand-rolled agentic loop (no Agent SDK, no Managed Agents) — you own the loop,
  which is the point of the project.
- **Code manipulation: Anthropic-defined `bash` + `text_editor` tools.** These
  are schema-less, Anthropic-defined, *client-executed* tools — you declare them
  by `type`/`name` and run the actions locally. You do **not** design a file API.
- **Truth signal: `pytest` (+ `mypy`/`ruff`) run in the worktree**, exposed as one
  dedicated `run_tests` tool so the harness controls and parses it.
- **Isolation: `git worktree`.** One worktree + branch per task.
- **State: SQLite** (single file, inspectable) — the same choice, and much of the
  same `repo.py`/`events.py` shape, as jobagent.

Keep it a clean, modular package — one module per concern, each independently
testable, mirroring jobagent's layout.

---

## 4. Data Model (SQLite)

**`tasks`** (one per unit of work)
`id, source (issue|manual), title, description, acceptance_notes, branch,
worktree_path, status, attempts, last_error, pr_ref, created_at, updated_at`

**`runs`** (one per implementer/reviewer invocation — the cost/latency ledger)
`id, task_id (fk), role (implementer|reviewer), model, turns, input_tokens,
output_tokens, cost_usd, tests_passed (bool), verdict, started_at, ended_at`

**`events`** (append-only audit log)
`id, ts, task_id, actor (orchestrator|implementer|reviewer|human), action, detail (json)`

### Task status state machine

```
queued → planning → implementing → testing → (red → implementing | green → reviewing)
reviewing → (changes_requested → implementing | approved → pr_ready)
pr_ready → merged            (HUMAN action)
any → needs_human            (budget/turns exhausted, or unrecoverable error)
```

`merged` is reachable only by a human action. `needs_human` is the safe sink for
anything the recoverable loop couldn't finish. Enforce transitions through one
`validate_transition()` (copy the pattern from jobagent's `models.py`) so illegal
jumps raise instead of corrupting state.

---

## 5. The Tool Surface

The implementer gets exactly these; the reviewer gets the read-only subset. The
absence of a merge/push tool is the guardrail — the agent *cannot* do the
irreversible thing because the capability does not exist (the same trick jobagent
uses by having no submit tool).

| Tool | Kind | Who | Notes |
|------|------|-----|-------|
| `bash` | Anthropic-defined (`bash_20250124`) | implementer | Allowlisted executables; reject shell operators / network / out-of-worktree. |
| `text_editor` | Anthropic-defined (`text_editor_20250728`, name `str_replace_based_edit_tool`) | implementer | `view`/`create`/`str_replace`/`insert`; path confined to worktree; back up on overwrite. |
| `run_tests` | custom (you define) | implementer | Runs `pytest` (+ optional `mypy`/`ruff`) in the worktree; returns `{passed, summary, failures}`. **The truth signal.** |
| `read_file` / `grep` | custom, read-only | reviewer | So the reviewer can inspect context around the diff. |
| `get_diff` | custom, read-only | reviewer | Returns the worktree diff vs. the base branch. |

Promote `run_tests`/`get_diff` to **dedicated tools** (rather than letting the
model just `bash pytest`) so the harness owns the gate: it parses pass/fail,
records it in `runs`, and the reviewer can't be handed an unverified diff.

**Security checks live in the tool executors, in one place** — the analog of
jobagent's `PoliteClient` and `assert_guardrails()`. A unit test should assert
that no merge/push capability is reachable and that path-escape + destructive
commands are rejected.

---

## 6. The Manual Agentic Loop (implementer & reviewer)

The core mechanic, hand-rolled (this is the part you're choosing to own):

1. `messages.create(model=…, tools=[…], messages=…)`.
2. If `stop_reason == "end_turn"` → the role is done; return its result.
3. If `stop_reason == "tool_use"` → execute each `tool_use` block, append the
   assistant message **including the tool_use blocks**, then append one user
   message with all `tool_result` blocks (each carrying the matching
   `tool_use_id`); loop.
4. If `stop_reason == "pause_turn"` → re-send to resume (server-side tool pacing).
5. If `stop_reason == "refusal"` → stop and park `needs_human` (do not retry the
   same prompt). Check `stop_reason` **before** reading `content`.
6. Enforce `max_turns` and the token budget every iteration; exhaustion →
   `needs_human`.

**Models & params:**
- Default `claude-opus-4-8` for the **reviewer** and the orchestrator's judgment
  calls — it's the strongest at finding real bugs.
- The **implementer** can run `claude-opus-4-8` (best quality) or
  `claude-sonnet-4-6` (cheaper, faster, the high-volume role) — a deliberate
  architectural split, your call.
- `output_config={"effort": "xhigh"}` for coding/agentic turns; adaptive thinking
  on. Stream when `max_tokens` is large.

**Cost control — prompt caching.** Put the stable context (repo map, coding
conventions, the test command, the guardrails, the task's acceptance notes) in a
**cached system block** so every iteration re-reads it at ~0.1× instead of full
price. This is the single biggest lever in a multi-turn, multi-agent loop. Verify
with `usage.cache_read_input_tokens`.

**Review contract.** The reviewer returns a small structured verdict —
`{verdict: approve|request_changes, findings: [...]}` — via a forced tool call or
structured output, so the orchestrator can branch on it deterministically. Prompt
it to *report everything with confidence + severity* and let the orchestrator (or
a second pass) filter — recent models follow "only high-severity" too literally
and under-report.

---

## 7. Roles (detail)

- **Orchestrator (code).** Owns the SQLite ledger, worktree lifecycle, the state
  machine, the budget, and the implementer→test→review→PR flow. No model calls of
  its own in v1.
- **Implementer (model loop).** Gets the task + acceptance notes + repo map, edits
  in its worktree, runs `run_tests`, iterates to green. Stops when green or
  budget-exhausted.
- **Reviewer (model loop).** Gets the diff + task; read-only tools. Approves or
  requests changes with specific findings. A fresh-context reviewer catches more
  than implementer self-critique — keep them separate.
- **Human (the gate).** Reviews the prepared PR — the diff, the test result, the
  reviewer's findings — and merges on the real remote. The only human step.

---

## 8. Build Phases (each independently runnable)

**Phase 0 — One implementer, one truth signal.** Package scaffold, SQLite init,
the `bash` + `text_editor` executors with their security checks, and the
`run_tests` tool. Done when: given a worktree with one deliberately-failing test,
the implementer loop edits code and drives `run_tests` to green.

**Phase 1 — The gate.** Wire `run_tests` (+ `mypy`/`ruff`) as the hard gate; red
routes back to the implementer; budget exhaustion → `needs_human`. Done when a
task only reaches "green" through a real passing suite.

**Phase 2 — Reviewer.** Add the read-only reviewer loop and the structured
verdict; `request_changes` loops back to the implementer. Done when a diff must be
*approved and green* to proceed.

**Phase 3 — Orchestrator + ledger.** SQLite tasks/runs/events, the state machine,
and the deterministic driver. Done when one command takes a task from `queued` to
`pr_ready` and the whole run is auditable in `events`.

**Phase 4 — Worktree isolation + parallelism.** One worktree/branch per task; run
independent tasks concurrently. Done when two tasks proceed without touching each
other or `main`.

**Phase 5 — Human gate.** Prepare a PR (or a clean branch + diff + summary) and
park it for review. Done when a human can open it, see the diff + green tests +
findings, and merge.

**Phase 6 — Analytics (your evaluation section).** Per-task success rate,
iterations-to-green, cost per task, human-touch rate over time. This is the data
that makes the write-up.

---

## 9. Definition of Done (v1)

- One command takes a human-filed task through plan → implement → test → review
  and parks a green, ready-to-merge PR.
- The truth signal (tests + types + lint) gates every "done"; nothing reaches
  review unverified.
- Agents operate only in isolated worktrees; there is no merge/push capability.
- The whole run is a plain, inspectable SQLite ledger + append-only event log.
- No merge happens without a human. No destructive shell. No secrets logged.

---

## 10. Future — Earned Autonomy (out of scope for v1)

Shrink the human-touch rate deliberately, per *proven-clean change class*:

- **Auto-merge whitelist.** Start with the lowest-risk classes (docs-only,
  formatting, dependency bumps with green tests) — opt-in, never for a diff that
  touches the guardrail tests or `main` build config.
- **LLM planner** replacing the deterministic decomposition (the `run_agent_loop`
  analog).
- **Self-improving prompts / skills** the system updates from what it learns.

Each is additive and must preserve every guardrail in §2.

---

### A note on philosophy, for whoever builds this

The temptation will be to let it merge on green and call it autonomous. Resist it.
A green test suite proves the change does what its tests say — not that the tests
say the right thing, and not that the change is what the human wanted. The merge
is the one place a confident wrong answer enters shared history and ships. This
whole architecture exists to give the model maximum leverage on the recoverable
work while keeping the human on the irreversible step — the same line jobagent
draws for job applications, drawn again for the code that writes jobagent. Build
to that line.
