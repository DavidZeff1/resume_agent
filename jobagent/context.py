"""AgentContext — the bundle of config + DB connection + logger.

Every CLI command and every pipeline stage takes one of these so they share a
single configured connection and logging setup.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from logging import Logger

from .config import Config
from .db import get_conn
from .logging_setup import setup_logging


@dataclass
class AgentContext:
    config: Config
    conn: sqlite3.Connection
    log: Logger

    @classmethod
    def create(
        cls,
        config_path: str | None = None,
        overrides: dict | None = None,
    ) -> "AgentContext":
        config = Config.load(config_path, overrides)
        log = setup_logging(config.paths.logs_dir, config.log_level)
        conn = get_conn(config)
        return cls(config=config, conn=conn, log=log)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "AgentContext":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
