# tiny-agent

A small, **agent-and-tool-centered** project: an LLM in a loop that calls tools
until your task is done. Built provider-agnostic, so it runs **for free** on
Groq's free tier or a fully offline local model (Ollama) — and swaps to paid
Claude/OpenAI by changing one environment variable.

```
your task ─► model ─► (wants a tool?) ─► run tool ─► feed result ─┐
                  ▲                                               │
                  └───────────────── loop ────────────────────────┘
                  └─► (no tool needed) ─► final answer
```

The whole thing is ~400 lines. The two ideas:

- **Tools are just Python functions.** Decorate a function with `@registry.tool`
  and its JSON schema is derived from the type hints + docstring. Adding a
  capability is writing a function (`agent/tools/`).
- **The loop speaks the OpenAI-compatible API** (`agent/loop.py`), so any
  provider that does — Groq, Ollama, Cerebras, OpenAI — works unchanged.

## Built-in tools

| Tool | What it does |
|------|--------------|
| `web_search` | Keyless DuckDuckGo search |
| `fetch_url` | GET a page, return readable text |
| `read_file` / `write_file` / `list_dir` | File I/O, sandboxed to a workspace dir |
| `run_python` | Execute Python and capture stdout |

## Run it for free

**Option A — Groq (free tier, fast, recommended):**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                     # or: pip install -r requirements.txt
cp .env.example .env                 # then paste a free key from console.groq.com

agent "find the latest stable Python version and write it to version.txt"
```

**Option B — Local Ollama ($0, offline, no key):**

```bash
ollama serve &           # in another terminal
ollama pull qwen2.5
AGENT_PROVIDER=ollama agent "what is 17 * 23? use a tool to be sure"
```

No arguments drops you into an interactive REPL:

```bash
agent
```

## Switching providers

Everything is env-driven (`agent/config.py`). Pick a preset:

```bash
AGENT_PROVIDER=groq      # default — free
AGENT_PROVIDER=ollama    # local, free, offline
AGENT_PROVIDER=cerebras  # free, very fast
AGENT_PROVIDER=openai    # paid
```

…or point at **any** OpenAI-compatible endpoint directly:

```bash
AGENT_BASE_URL=https://api.anthropic.com/v1   # Anthropic's OpenAI-compat layer
AGENT_MODEL=claude-... AGENT_API_KEY=sk-...
```

> Heads up: free tiers rate-limit, and small open models call tools less
> reliably than frontier models. Great for personal use and development; for
> heavy or high-stakes work, point the same agent at a paid model.

## Add your own tool

```python
# agent/tools/mytools.py
from . import registry

@registry.tool
def add(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: the first number
        b: the second number
    """
    return a + b
```

Then import it in `agent/tools/__init__.py`. That's the whole extension model.

## Layout

```
agent/
  config.py      provider presets + env resolution
  loop.py        run_agent(): the tool-use loop
  cli.py         `agent` entrypoint (one-shot + REPL)
  tools/
    base.py      Tool + ToolRegistry + @registry.tool (schema from signature)
    files.py     read/write/list, sandboxed to AGENT_WORKSPACE
    web.py       web_search, fetch_url
    python.py    run_python
tests/           offline tests (no network, no API key)
```

## Testing

```bash
pip install -e '.[dev]'
pytest          # offline: fakes the model, exercises tools + loop
```
