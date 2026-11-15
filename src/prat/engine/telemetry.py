"""What a run's agents are doing and what they used, kept on the run row as it happens.

The engine passes every runtime event of a run here, whichever role's session it came from.
:class:`RunTelemetry` keeps two things:

* ``usage``: per role (builder, planner, critic, ...) the models used, tokens in and out,
  prompt cache reads and writes, and the spend estimate per agent (prat.usage). The Usage page
  and mission control's usage view add these up; nothing shows them per card.
* ``live``: what the lead is doing right now, for mission control: its task list, how full its
  context is, how often it compacted, its helpers (running and done), the skills it loaded,
  and the last rate limit pressure the service reported.

Writes are throttled: the row is updated when something a person would see changes, at most
every ``min_seconds``, and always at the end of a session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from prat.runtime.base import (
    CompactEvent,
    ContextEvent,
    CostEvent,
    HelperEvent,
    PressureEvent,
    RuntimeEvent,
    SessionStarted,
    SkillEvent,
    TasksEvent,
    ThoughtEvent,
    UsageEvent,
)
from prat.usage import UsageTally

MAX_HELPERS = 12


@dataclass
class RunTelemetry:
    min_seconds: float = 2.0
    roles: dict[str, UsageTally] = field(default_factory=dict)
    live: dict[str, Any] = field(default_factory=dict)
    _dirty: bool = False
    _written: float = 0.0

    @classmethod
    def load(cls, usage: dict[str, Any] | None, live: dict[str, Any] | None) -> RunTelemetry:
        found = cls(live=dict(live or {}))
        for role, data in ((usage or {}).get("roles") or {}).items():
            tally = UsageTally()
            tally.models = {m: dict(v) for m, v in (data.get("models") or {}).items()}
            tally.agents = dict(data.get("agents") or {})
            found.roles[str(role)] = tally
        return found

    def observe(self, role: str, event: RuntimeEvent) -> bool:
        """Take one event; True when the row should be written now."""
        urgent = False
        if isinstance(event, CostEvent | UsageEvent):
            tally = self.roles.setdefault(role, UsageTally())
            if isinstance(event, UsageEvent) or event.model or event.agent != "lead":
                tally.add(event)
            elif not tally.final:
                # A reconciling delta with no model: credit the role's first model, if any.
                tally.agents[event.agent] = tally.agents.get(event.agent, 0.0) + event.usd
            self._dirty = True
            urgent = isinstance(event, UsageEvent)
        if role not in ("builder", "setup"):
            return self._due(urgent)
        if isinstance(event, SessionStarted):
            self.live["model"] = event.model
            self.live["role"] = role
            self._dirty = True
        elif isinstance(event, TasksEvent):
            self.live["tasks"] = [{"subject": t.subject, "status": t.status} for t in event.tasks]
            self._dirty = urgent = True
        elif isinstance(event, ContextEvent):
            self.live["context"] = {"tokens": event.tokens, "window": event.window}
            self._dirty = True
        elif isinstance(event, ThoughtEvent):
            self.live["thought"] = " ".join(event.text.split())[:300]
            self._dirty = True
        elif isinstance(event, CompactEvent):
            self.live["compactions"] = int(self.live.get("compactions") or 0) + 1
            self._dirty = urgent = True
        elif isinstance(event, HelperEvent):
            self._helper(event)
            self._dirty = urgent = True
        elif isinstance(event, SkillEvent):
            skills = list(self.live.get("skills") or [])
            entry = f"{event.name}" if event.agent == "lead" else f"{event.name} ({event.agent})"
            if entry not in skills:
                skills.append(entry)
            self.live["skills"] = skills
            self._dirty = urgent = True
        elif isinstance(event, PressureEvent):
            self.live["pressure"] = {
                "status": event.status,
                "utilization": event.utilization,
                "resets_at": event.resets_at,
                "kind": event.kind,
            }
            self._dirty = True
        return self._due(urgent)

    def _helper(self, event: HelperEvent) -> None:
        helpers = list(self.live.get("helpers") or [])
        if event.phase == "started":
            helpers.append(
                {
                    "call_id": event.call_id,
                    "name": event.name,
                    "state": "running",
                    "prompt": event.prompt,
                    "summary": "",
                    "started": time.time(),
                }
            )
        else:
            for helper in helpers:
                if helper.get("call_id") == event.call_id:
                    helper["state"] = "done" if event.ok else "failed"
                    helper["summary"] = event.summary
                    helper["finished"] = time.time()
        self.live["helpers"] = helpers[-MAX_HELPERS:]

    def _due(self, urgent: bool) -> bool:
        if not self._dirty:
            return False
        now = time.monotonic()
        if urgent or now - self._written >= self.min_seconds:
            self._written = now
            self._dirty = False
            return True
        return False

    def set_dirty(self) -> bool:
        """Something changed outside an event (a tool's report); True when due for a write."""
        self._dirty = True
        return self._due(False)

    def flush(self) -> bool:
        """True when there is something not yet written."""
        if not self._dirty:
            return False
        self._dirty = False
        self._written = time.monotonic()
        return True

    def usage(self) -> dict[str, Any]:
        return {"roles": {role: tally.to_dict() for role, tally in self.roles.items()}}

    def role_usage(self, role: str) -> dict[str, Any] | None:
        tally = self.roles.get(role)
        return tally.to_dict() if tally is not None else None
