"""Blackboard — the shared-memory coordination mechanism.

In a blackboard multi-agent architecture, agents don't call each other directly;
they read and write a common workspace and are triggered by changes to it. The
orchestrator and the worker agents here coordinate entirely through this object.

It also carries an append-only ``events`` log: every status change and every
tool call is recorded, so a whole run is auditable after the fact (the same
inspectable-state guarantee jobagent makes with its ``events`` table).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    ts: float
    actor: str
    action: str
    detail: dict | None = None


@dataclass
class Blackboard:
    _data: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    # -- shared state -------------------------------------------------------- #
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    def snapshot(self) -> dict:
        return dict(self._data)

    # -- audit log ----------------------------------------------------------- #
    def log(self, actor: str, action: str, detail: dict | None = None) -> Event:
        ev = Event(ts=time.time(), actor=actor, action=action, detail=detail)
        self.events.append(ev)
        return ev
