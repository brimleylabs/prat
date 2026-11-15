"""Usage per model: tokens in and out, prompt cache reads and writes, and the estimate.

A :class:`UsageTally` collects one session's :class:`~prat.runtime.base.CostEvent` values as
they stream (each says which model answered and which agent, the lead or a helper, asked) and
takes the session's own per-model tally at the end (:class:`~prat.runtime.base.UsageEvent`),
which counts helpers exactly and replaces the running estimate. Steps keep
``tally.to_dict()`` in their detail; the Usage page and mission control's usage view add those
up. No page shows usage per card.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from prat.runtime.base import CostEvent, UsageEvent

FIELDS = ("input", "output", "cache_read", "cache_write", "usd")


def _empty() -> dict[str, float]:
    return dict.fromkeys(FIELDS, 0.0)


@dataclass
class UsageTally:
    models: dict[str, dict[str, float]] = field(default_factory=dict)
    # Spend by agent ("lead" or a helper's name), from the running events.
    agents: dict[str, float] = field(default_factory=dict)
    final: bool = False

    def add(self, event: CostEvent | UsageEvent) -> None:
        if isinstance(event, UsageEvent):
            if event.models:
                self.models = {m: {**_empty(), **dict(v)} for m, v in event.models.items()}
                self.final = True
            return
        self.agents[event.agent] = self.agents.get(event.agent, 0.0) + event.usd
        if self.final:
            return
        row = self.models.setdefault(event.model or "unknown", _empty())
        row["input"] += event.input_tokens
        row["output"] += event.output_tokens
        row["cache_read"] += event.cache_read_tokens
        row["cache_write"] += event.cache_write_tokens
        row["usd"] += event.usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": {
                m: {k: round(v, 6) for k, v in row.items()} for m, row in self.models.items()
            },
            "agents": {a: round(v, 6) for a, v in self.agents.items()},
        }


def merge(usages: Iterable[Mapping[str, Any] | None]) -> dict[str, dict[str, float]]:
    """Add up the ``models`` part of many step usages."""
    total: dict[str, dict[str, float]] = {}
    for usage in usages:
        models = (usage or {}).get("models") or {}
        if not isinstance(models, Mapping):
            continue
        for model, row in models.items():
            if not isinstance(row, Mapping):
                continue
            into = total.setdefault(str(model), _empty())
            for key in FIELDS:
                into[key] += float(row.get(key) or 0.0)
    return total


def cache_share(row: Mapping[str, float]) -> float | None:
    """The share of input tokens served from the prompt cache, or None with no input."""
    read = float(row.get("cache_read") or 0.0)
    total = read + float(row.get("input") or 0.0) + float(row.get("cache_write") or 0.0)
    return read / total if total else None
