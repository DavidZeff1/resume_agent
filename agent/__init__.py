"""A small, provider-agnostic agent: an LLM in a loop that calls tools.

The whole thing is built around two ideas:

- **Tools** are plain Python functions registered with ``@tool`` (``agent.tools``).
  Their JSON schema is derived from the signature, so adding a capability is
  literally writing a function.
- **The loop** (``agent.loop.run_agent``) speaks the OpenAI-compatible chat API,
  so it runs on any provider that does — Groq (free), local Ollama ($0/offline),
  Cerebras, or paid Anthropic/OpenAI — by changing one env var.
"""

__version__ = "0.1.0"
