"""Configuration: a single ``config.yaml`` plus environment/.env secrets.

Guardrails honored here:
  * Secrets come from the environment (or a gitignored .env) ONLY. The API key
    is read lazily from ``os.environ`` and is never stored on the object or
    logged.
  * The watchlist is finite and human-defined: companies are read from config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .util import deep_merge

DEFAULT_USER_AGENT = (
    "jobagent/0.1 (+local personal job-application assistant; contact via config)"
)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into the environment.

    Real environment variables always win (we use setdefault). Prefers
    python-dotenv if installed, with a tiny built-in fallback so the package
    has no hard dependency on it.
    """
    try:
        from dotenv import dotenv_values  # type: ignore

        for key, value in dotenv_values(path).items():
            if value is not None:
                os.environ.setdefault(key, value)
        return
    except Exception:
        pass

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Paths:
    root: Path
    data_dir: Path
    db_path: Path
    cache_dir: Path
    generated_dir: Path
    logs_dir: Path

    def ensure(self) -> None:
        for p in (self.data_dir, self.cache_dir, self.generated_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class ScoringConfig:
    backend: str = "auto"            # auto | heuristic | claude
    model: str = "claude-sonnet-4-6"
    shortlist_threshold: float = 0.6
    max_jobs_per_run: int = 200


@dataclass
class SourcingConfig:
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 20.0
    min_interval_seconds: float = 1.5   # politeness: per-host min gap
    max_retries: int = 3
    cache_ttl_seconds: int = 3600
    respect_robots: bool = True
    max_jobs_per_company: int = 0       # 0 = unlimited


@dataclass
class FollowupConfig:
    days_until_followup: int = 7


@dataclass
class CompanyConfig:
    name: str
    ats_type: str
    board_token: str | None = None
    board_url: str | None = None
    notes: str | None = None
    active: bool = True


@dataclass
class Config:
    paths: Paths
    scoring: ScoringConfig
    sourcing: SourcingConfig
    followup: FollowupConfig
    companies: list[CompanyConfig] = field(default_factory=list)
    log_level: str = "INFO"
    raw: dict = field(default_factory=dict)

    # --- secrets: read lazily from env, never persisted on the object ----------
    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def has_llm(self) -> bool:
        """True only if a key is present AND an LLM client is importable."""
        if not self.anthropic_api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except Exception:
            return False
        return True

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        overrides: dict | None = None,
    ) -> "Config":
        root = Path.cwd()
        config_path = Path(
            config_path or os.environ.get("JOBAGENT_CONFIG") or root / "config.yaml"
        )
        _load_dotenv(root / ".env")

        data: dict = {}
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if overrides:
            data = deep_merge(data, overrides)

        paths_cfg = data.get("paths", {}) or {}
        data_dir = Path(
            paths_cfg.get("data_dir")
            or os.environ.get("JOBAGENT_DATA_DIR")
            or root / "data"
        ).expanduser()
        paths = Paths(
            root=root,
            data_dir=data_dir,
            db_path=Path(paths_cfg.get("db_path") or data_dir / "jobagent.sqlite3"),
            cache_dir=Path(paths_cfg.get("cache_dir") or data_dir / "cache"),
            generated_dir=Path(paths_cfg.get("generated_dir") or data_dir / "generated"),
            logs_dir=Path(paths_cfg.get("logs_dir") or data_dir / "logs"),
        )
        paths.ensure()

        scoring = ScoringConfig(**_subset(data.get("scoring", {}), ScoringConfig))
        sourcing = SourcingConfig(**_subset(data.get("sourcing", {}), SourcingConfig))
        followup = FollowupConfig(**_subset(data.get("followup", {}), FollowupConfig))

        companies = [
            CompanyConfig(
                name=c["name"],
                ats_type=c["ats_type"],
                board_token=c.get("board_token"),
                board_url=c.get("board_url"),
                notes=c.get("notes"),
                active=bool(c.get("active", True)),
            )
            for c in (data.get("companies") or [])
            if c.get("name") and c.get("ats_type")
        ]

        return cls(
            paths=paths,
            scoring=scoring,
            sourcing=sourcing,
            followup=followup,
            companies=companies,
            log_level=str(data.get("log_level", "INFO")),
            raw=data,
        )


def _subset(d: dict, dc) -> dict:
    """Keep only keys that are real fields of dataclass `dc` (ignore extras)."""
    allowed = {f.name for f in dc.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in (d or {}).items() if k in allowed}
