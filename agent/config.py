"""Provider configuration.

Everything is provider-agnostic: we only need a ``base_url``, a ``model``, and an
``api_key``. Pick a provider with ``AGENT_PROVIDER`` (default ``groq``) or override
any field directly with ``AGENT_BASE_URL`` / ``AGENT_MODEL`` / ``AGENT_API_KEY``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Sensible, free-first presets. Each is just an OpenAI-compatible endpoint.
PRESETS: dict[str, dict] = {
    # Free tier, very fast, good tool calling. Get a key at console.groq.com.
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
    },
    # $0 forever, fully offline. `ollama serve` + `ollama pull qwen2.5`.
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5",
        "key_env": None,  # Ollama ignores the key; we send a placeholder.
    },
    # Free tier, extremely fast.
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
        "key_env": "CEREBRAS_API_KEY",
    },
    # Paid, top-tier tool calling — drop-in when you want the best.
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
}

DEFAULT_PROVIDER = "groq"


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    model: str
    api_key: str
    max_steps: int = 12

    @classmethod
    def from_env(cls, provider: str | None = None, model: str | None = None) -> "Settings":
        _load_dotenv()
        provider = (provider or os.environ.get("AGENT_PROVIDER") or DEFAULT_PROVIDER).lower()
        preset = PRESETS.get(provider)
        if preset is None:
            raise SystemExit(
                f"Unknown provider {provider!r}. Known: {', '.join(PRESETS)} "
                f"(or set AGENT_BASE_URL/AGENT_MODEL/AGENT_API_KEY directly)."
            )

        base_url = os.environ.get("AGENT_BASE_URL") or preset["base_url"]
        model = model or os.environ.get("AGENT_MODEL") or preset["model"]

        key_env = preset["key_env"]
        api_key = (
            os.environ.get("AGENT_API_KEY")
            or (os.environ.get(key_env) if key_env else None)
            or "not-needed"  # local providers (Ollama) don't check it
        )
        if key_env and api_key == "not-needed":
            raise SystemExit(
                f"Provider {provider!r} needs an API key. Set {key_env} (or AGENT_API_KEY) "
                f"in your environment or .env file. It's free — see the README."
            )

        max_steps = int(os.environ.get("AGENT_MAX_STEPS", "12"))
        return cls(provider, base_url, model, api_key, max_steps)


def _load_dotenv() -> None:
    """Load a local .env if python-dotenv is installed (optional convenience)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
