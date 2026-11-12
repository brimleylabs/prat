"""Which model each agent role uses, and rough per-token prices.

Roles are resolved in this order, most specific first:

1. the project file's ``[models]`` table,
2. the installation settings (``models`` in ``config.toml`` or ``PRAT_MODELS``),
3. the defaults below.

Prices are used only to estimate spend while a session is running, so the
engine can stop a run that goes over its budget before the session reports
its own total. They are estimates: PRAT never sees a bill.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    BUILDER = "builder"
    PLANNER = "planner"
    CRITIC = "critic"
    SCOUT = "scout"
    DRAFTER = "drafter"
    SUMMARIZER = "summarizer"
    # Self-improvement web research; the only role with web tools.
    RESEARCHER = "researcher"
    # The second, independent review: does the change make PRAT less safe.
    SECURITY_REVIEWER = "security_reviewer"
    # Keeps project knowledge up to date during dreams; no tools.
    LIBRARIAN = "librarian"
    # Reads one module in full during a deep scan.
    READER = "reader"
    # Works out how to install, test and run a newly added project (the setup card).
    SETUP = "setup"


OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

DEFAULT_MODELS: Mapping[Role, str] = {
    Role.BUILDER: SONNET,
    Role.PLANNER: OPUS,
    Role.CRITIC: SONNET,
    # One session per project, once: the first look is worth the strongest model.
    Role.SCOUT: OPUS,
    Role.DRAFTER: HAIKU,
    Role.SUMMARIZER: HAIKU,
    Role.RESEARCHER: SONNET,
    Role.SECURITY_REVIEWER: SONNET,
    Role.LIBRARIAN: HAIKU,
    Role.READER: SONNET,
    # Figuring out an unknown stack once per project is worth the strongest model.
    Role.SETUP: OPUS,
}

ROLE_NAMES = frozenset(str(role) for role in Role)


def validate_role_map(values: Mapping[str, str]) -> dict[str, str]:
    """Check a ``{role: model}`` table from settings or a project file."""
    cleaned: dict[str, str] = {}
    for role, model in values.items():
        if role not in ROLE_NAMES:
            raise ValueError(f"unknown model role {role!r}; use one of {sorted(ROLE_NAMES)}")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"model for role {role!r} must be a non-empty string")
        cleaned[role] = model.strip()
    return cleaned


def resolve_model(
    role: Role | str,
    *,
    settings_models: Mapping[str, str] | None = None,
    project_models: Mapping[str, str] | None = None,
) -> str:
    key = str(Role(role))
    for table in (project_models, settings_models):
        if table and table.get(key):
            return table[key]
    return DEFAULT_MODELS[Role(key)]


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# First-party API list prices at the time of writing. Cache writes are
# estimated at 1.25 times the input price and cache reads at a tenth of it
# where no separate figure is published.
PRICES: Mapping[str, Price] = {
    OPUS: Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0),
    SONNET: Price(input=2.0, output=10.0, cache_read=0.20, cache_write=2.5),
    HAIKU: Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}
# Used for a model PRAT does not know, so an unknown model is never free.
FALLBACK_PRICE = PRICES[OPUS]


def price_for(model: str | None) -> Price:
    if not model:
        return FALLBACK_PRICE
    if model in PRICES:
        return PRICES[model]
    for known, price in PRICES.items():
        if model.startswith(known):
            return price
    return FALLBACK_PRICE


def _tokens(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, int | float) and value > 0 else 0


def estimate_cost(model: str | None, usage: Mapping[str, Any] | None) -> float:
    """Estimated USD for one response's ``usage`` block."""
    if not usage:
        return 0.0
    price = price_for(model)
    total = (
        _tokens(usage, "input_tokens") * price.input
        + _tokens(usage, "output_tokens") * price.output
        + _tokens(usage, "cache_read_input_tokens") * price.cache_read
        + _tokens(usage, "cache_creation_input_tokens") * price.cache_write
    )
    return total / 1_000_000
