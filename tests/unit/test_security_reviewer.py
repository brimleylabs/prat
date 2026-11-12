from __future__ import annotations

import json
from pathlib import Path

import pytest

from prat.engine.interfaces import ReviewRequest
from prat.engine.security import (
    SECURITY_ROLE,
    AgentSecurityReviewer,
    SecurityVerdict,
    parse_security,
    security_task_prompt,
)
from prat.model_registry import DEFAULT_MODELS, Role
from prat.policy import ToolCall
from prat.projects import ProjectFileError, parse_spec
from prat.runtime import READ_TOOLS, FakeRuntime


def request(tmp_path: Path, **kwargs: object) -> ReviewRequest:
    base: dict[str, object] = {
        "card_id": 1,
        "title": "Tidy the policy",
        "goal": "g",
        "acceptance_criteria": "a",
        "diff": "+x = 1\n",
        "diff_stats": {},
        "gates": [],
        "workdir": str(tmp_path),
    }
    base.update(kwargs)
    return ReviewRequest(**base)  # type: ignore[arg-type]


def answer(verdict: str, findings: list[dict[str, str]] | None = None) -> str:
    body = json.dumps({"verdict": verdict, "summary": "s", "findings": findings or []})
    return f"Looked at it.\n```json\n{body}\n```"


def test_pass_and_concern_are_read() -> None:
    passed = parse_security(answer("pass"))
    assert passed.verdict is SecurityVerdict.PASS and passed.passed and passed.parsed
    concern = parse_security(
        answer("concern", [{"file": "src/prat/policy/engine.py", "issue": "push rule removed"}])
    )
    assert concern.verdict is SecurityVerdict.CONCERN
    assert concern.findings[0].file == "src/prat/policy/engine.py"
    assert "push rule removed" in concern.render_fix_list()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Looks fine to me.",
        answer("met"),
        answer("approve"),
        '```json\n["pass"]\n```',
        '```json\n{"verdict": "pass"\n```',
    ],
)
def test_anything_unreadable_is_a_concern(text: str) -> None:
    result = parse_security(text)
    assert result.verdict is SecurityVerdict.CONCERN
    assert not result.parsed


def test_the_last_verdict_block_wins() -> None:
    text = answer("pass") + "\nOn reflection:\n" + answer("concern", [{"issue": "x"}])
    assert parse_security(text).verdict is SecurityVerdict.CONCERN


def test_the_prompt_names_what_to_look_for() -> None:
    for subject in ("isolation", "push block", "authentication", "verification", "untrusted"):
        assert subject in SECURITY_ROLE
    text = security_task_prompt(
        title="t",
        goal="g",
        diff="+y",
        diff_limit=10,
        changes={"risk": "high", "categories": {"security": "high"}, "files": [], "flags": []},
    )
    assert "Change classification" in text and "touches security" in text


def test_the_role_is_registered() -> None:
    assert Role.SECURITY_REVIEWER in DEFAULT_MODELS


def test_the_session_is_read_only(tmp_path: Path) -> None:
    task = AgentSecurityReviewer(FakeRuntime([])).task_for(request(tmp_path))
    assert task.allowed_tools == READ_TOOLS
    assert task.metadata["role"] == "security_reviewer"
    assert not task.policy(ToolCall.write(str(tmp_path / "x.py"), "x")).allowed
    assert task.system_prompt == SECURITY_ROLE


async def test_an_agent_review(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        [{"op": "cost", "usd": 0.02}, {"op": "finish", "message": answer("pass")}]
    )
    result = await AgentSecurityReviewer(runtime).review(request(tmp_path))
    assert result.passed
    assert result.cost_usd == pytest.approx(0.02)


@pytest.mark.parametrize(
    "script",
    [
        [{"op": "error", "message": "overloaded"}],
        [{"op": "exceed_budget"}, {"op": "finish", "message": answer("pass")}],
        [{"op": "block", "reason": "cannot tell"}],
    ],
)
async def test_a_review_that_does_not_finish_is_a_concern(
    tmp_path: Path, script: list[dict[str, object]]
) -> None:
    result = await AgentSecurityReviewer(FakeRuntime(script)).review(
        request(tmp_path, budget_usd=0.5)
    )
    assert result.verdict is SecurityVerdict.CONCERN


def test_projects_choose_reviewers() -> None:
    spec = parse_spec('name = "x"\n[review]\nreviewers = ["security", "criteria"]\n')
    assert spec.review.reviewers == ["criteria", "security"]
    assert parse_spec('name = "x"\n').review.reviewers == ["criteria"]
    with pytest.raises(ProjectFileError, match="cannot be turned off"):
        parse_spec('name = "x"\n[review]\nreviewers = ["security"]\n')
    with pytest.raises(ProjectFileError, match="unknown reviewers"):
        parse_spec('name = "x"\n[review]\nreviewers = ["criteria", "vibes"]\n')
