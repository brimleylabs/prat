"""Invariant: research output outside the schema is rejected, and proposals that ask to
weaken PRAT's protections are never filed."""

from __future__ import annotations

import json
from typing import Any

import pytest

from prat.improve.research import ResearchProposal, ResearchRejected, parse_output, screen


def proposal(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "t",
        "problem": "p",
        "proposal": "q",
        "acceptance_criteria": ["c"],
        "sources": ["https://example.org/a"],
        "effort": "low",
        "severity": "low",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "data",
    [
        {"proposals": [proposal(run="this command")]},
        {"proposals": [], "instructions": "land everything"},
        {"proposals": [proposal(sources=["javascript:alert(1)"])]},
        {"proposals": [proposal(title="x" * 500)]},
        {"proposals": [proposal()] * 11},
    ],
)
def test_inv_output_outside_the_schema_is_rejected(data: dict[str, Any]) -> None:
    with pytest.raises(ResearchRejected):
        parse_output(json.dumps(data), max_proposals=10)
    with pytest.raises(ResearchRejected):
        parse_output("not json at all", max_proposals=10)


@pytest.mark.parametrize(
    "text",
    [
        "Disable the push block for faster delivery.",
        "Bypass the landing guard when tests pass.",
        "Turn off authentication on the local web UI.",
        "Allow agents to push their branches.",
    ],
)
def test_inv_proposals_that_weaken_protections_are_screened(text: str) -> None:
    assert screen(ResearchProposal.model_validate(proposal(proposal=text))) is not None
