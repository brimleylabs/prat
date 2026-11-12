from __future__ import annotations

from dataclasses import replace

import pytest

from prat.improve.guard import Evidence, GuardDecision, judge

HEAD = "a" * 40

GOOD = Evidence(
    branch_head=HEAD,
    verified_commit=HEAD,
    gates_passed=True,
    verify_findings=(),
    criteria_verdict="met",
    criteria_commit=HEAD,
    security_verdict="pass",
    security_commit=HEAD,
    merge_commit="b" * 40,
    trial_status="passed",
    trial_summary="trial passed",
)


def test_full_evidence_lands() -> None:
    result = judge(GOOD)
    assert result.decision is GuardDecision.LAND and result.lands
    assert result.reasons == ()


def test_the_guard_only_answers_land_or_refuse() -> None:
    assert {d.value for d in GuardDecision} == {"land", "refuse"}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"verified_commit": "c" * 40}, "did not run on the branch head"),
        ({"verified_commit": None}, "no verification"),
        ({"gates_passed": False}, "a required gate failed"),
        ({"verify_findings": ("secret in config.py",)}, "verification: secret in config.py"),
        ({"criteria_verdict": "partial"}, "partial, not met"),
        ({"criteria_commit": "c" * 40}, "met, not met"),
        ({"security_verdict": "concern"}, "concern, not pass"),
        ({"security_verdict": None}, "missing, not pass"),
        ({"security_commit": "c" * 40}, "pass, not pass"),
        ({"merge_conflict": "app.py"}, "no longer merges cleanly: app.py"),
        ({"trial_status": "failed", "trial_summary": "probe: push"}, "trial failed: probe: push"),
        ({"trial_status": None}, "trial did not run"),
    ],
)
def test_the_card_can_fix_these(changes: dict[str, object], reason: str) -> None:
    result = judge(replace(GOOD, **changes))  # type: ignore[arg-type]
    assert result.decision is GuardDecision.REFUSE
    assert not result.hold
    assert any(reason in item for item in result.reasons), result.reasons
    assert reason in result.feedback()


@pytest.mark.parametrize(
    "changes",
    [{"remote_diverged": True}, {"trial_status": "postponed", "trial_summary": "offline"}],
)
def test_the_card_cannot_fix_these_so_they_hold(changes: dict[str, object]) -> None:
    result = judge(replace(GOOD, **changes))  # type: ignore[arg-type]
    assert result.decision is GuardDecision.REFUSE
    assert result.hold


def test_a_card_problem_is_never_hidden_behind_a_hold() -> None:
    result = judge(replace(GOOD, remote_diverged=True, gates_passed=False))
    assert not result.hold
    assert len(result.reasons) == 2
