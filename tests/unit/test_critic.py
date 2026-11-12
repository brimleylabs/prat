"""Critic verdict parsing and the agent reviewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prat.domain import Verdict
from prat.engine.critic import UNREADABLE, AgentReviewer, normalise_verdict, parse_review
from prat.engine.interfaces import ReviewRequest
from prat.engine.prompts import critic_task_prompt
from prat.runtime import FakeRuntime


def request(tmp_path: Path, **kwargs: object) -> ReviewRequest:
    base: dict[str, object] = {
        "card_id": 1,
        "title": "Add slugify",
        "goal": "Add slugify(text)",
        "acceptance_criteria": "- slugify lowercases\n- tests exist",
        "diff": "+def slugify(text):\n+def test_slugify():\n",
        "diff_stats": {},
        "gates": [{"name": "test", "passed": True, "command": "pytest", "output_tail": "3 passed"}],
        "summary": "1 file(s) changed",
        "workdir": str(tmp_path),
    }
    base.update(kwargs)
    return ReviewRequest(**base)  # type: ignore[arg-type]


FULL = {
    "verdict": "not_met",
    "summary": "Tests are missing.",
    "criteria": [
        {"criterion": "slugify lowercases", "status": "met", "evidence": "slug.py:3"},
        {"criterion": "tests exist", "status": "not met", "evidence": "no test file"},
    ],
    "fix_list": ["Add tests/test_slug.py covering lowercase and spaces"],
    "confidence": 85,
}


def test_fenced_json_block() -> None:
    text = "I read the diff.\n\n```json\n" + json.dumps(FULL) + "\n```\n"
    result = parse_review(text)
    assert result.verdict is Verdict.NOT_MET and result.needs_rework
    assert result.summary == "Tests are missing."
    assert [f.status for f in result.findings] == ["met", "not_met"]
    assert result.fix_list == ["Add tests/test_slug.py covering lowercase and spaces"]
    assert result.confidence == pytest.approx(0.85)
    assert result.parsed


def test_last_json_block_wins() -> None:
    first = json.dumps({"verdict": "wrong", "summary": "draft"})
    second = json.dumps({"verdict": "met", "summary": "final"})
    result = parse_review(f"```json\n{first}\n```\nthen\n```json\n{second}\n```")
    assert result.verdict is Verdict.MET and result.summary == "final"


def test_bare_json_object_without_fence() -> None:
    text = 'Verdict follows. {"verdict": "partial", "summary": "half", "fix_list": "- a\\n- b"}'
    result = parse_review(text)
    assert result.verdict is Verdict.PARTIAL and result.fix_list == ["a", "b"]


def test_text_fallback() -> None:
    result = parse_review("Overall verdict: NOT MET. Tests are missing.")
    assert result.verdict is Verdict.NOT_MET and result.concerns


def test_unreadable_answer_goes_to_a_person() -> None:
    result = parse_review("I could not decide.")
    assert result.verdict is Verdict.PARTIAL and not result.parsed
    assert result.summary == UNREADABLE and not result.needs_rework


def test_met_contradicted_by_findings_is_downgraded() -> None:
    data = {"verdict": "met", "summary": "ok", "criteria": FULL["criteria"], "fix_list": ["x"]}
    result = parse_review(json.dumps(data))
    assert result.verdict is Verdict.PARTIAL and result.concerns


def test_met_has_no_fix_list() -> None:
    result = parse_review(json.dumps({"verdict": "complete", "summary": "s", "fix_list": ["x"]}))
    assert result.verdict is Verdict.MET and result.fix_list == []


@pytest.mark.parametrize(
    ("word", "verdict"),
    [
        ("met", Verdict.MET),
        ("Complete", Verdict.MET),
        ("partial", Verdict.PARTIAL),
        ("needs_human_eyes", Verdict.PARTIAL),
        ("not met", Verdict.NOT_MET),
        ("NOT-MET", Verdict.NOT_MET),
        ("wrong", Verdict.WRONG),
        ("maybe", None),
    ],
)
def test_verdict_aliases(word: str, verdict: Verdict | None) -> None:
    assert normalise_verdict(word) is verdict


def test_render_includes_findings_and_fix_list() -> None:
    result = parse_review(json.dumps(FULL))
    text = result.render()
    assert "Verdict: not_met" in text and "[not met] tests exist" in text
    assert "Fix list:" in result.render_fix_list()


def test_prompt_contains_the_evidence() -> None:
    text = critic_task_prompt(
        title="Add slugify",
        goal="g",
        acceptance_criteria="- tests exist",
        summary="1 file(s) changed",
        gates=[{"name": "test", "passed": False, "exit_code": 1, "command": "pytest"}],
        diff="+def test_slugify():\n" + "x" * 100,
        diff_limit=50,
    )
    assert "test_slugify" in text and "failed (exit 1)" in text
    assert "only the first 50 are shown" in text and "1 file(s) changed" in text
    assert "Change classification" not in text


def test_prompt_shows_the_change_classification() -> None:
    changes = {
        "risk": "high",
        "categories": {"code": "low", "security": "high", "tests": "low"},
        "files": [
            {
                "path": "app/auth/session.py",
                "categories": {"security": "high", "code": "low"},
                "reasons": ["path segment 'auth'"],
            },
            {"path": "tests/test_session.py", "categories": {"tests": "low"}, "reasons": []},
        ],
        "flags": ["test functions dropped: 2 removed, 0 added"],
    }
    text = critic_task_prompt(
        title="t",
        goal="g",
        acceptance_criteria="- a",
        summary="s",
        gates=[],
        diff="",
        diff_limit=50,
        changes=changes,
    )
    section = text.split("## Change classification", 1)[1].split("## Gate results", 1)[0]
    assert "Overall risk: high" in section
    assert "security (high)" in section
    assert "- app/auth/session.py: code, security (path segment 'auth')" in section
    assert "Flag: test functions dropped" in section
    assert "touches security, tests" in section


def test_agent_reviewer_passes_the_classification_on(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "finish", "message": "```json\n" + json.dumps(FULL) + "\n```"}])
    changes = {"risk": "low", "categories": {"docs": "low"}, "files": [], "flags": []}
    task = AgentReviewer(runtime).task_for(request(tmp_path, changes=changes))
    assert "## Change classification" in task.prompt


async def test_agent_reviewer_parses_the_final_answer(tmp_path: Path) -> None:
    answer = "```json\n" + json.dumps(FULL) + "\n```"
    runtime = FakeRuntime([{"op": "cost", "usd": 0.03}, {"op": "finish", "message": answer}])
    result = await AgentReviewer(runtime).review(request(tmp_path))
    assert result.verdict is Verdict.NOT_MET
    assert result.cost_usd == pytest.approx(0.03)


async def test_agent_reviewer_failure_does_not_approve(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "error", "message": "api down"}])
    result = await AgentReviewer(runtime).review(request(tmp_path))
    assert result.verdict is Verdict.PARTIAL and not result.parsed and not result.needs_rework
    assert "api down" in result.summary


async def test_agent_reviewer_budget(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "cost", "usd": 0.5}, {"op": "finish", "message": "{}"}])
    result = await AgentReviewer(runtime).review(request(tmp_path, budget_usd=0.1))
    assert result.verdict is Verdict.PARTIAL and "budget" in result.summary


async def test_empty_diff_is_wrong(tmp_path: Path) -> None:
    runtime = FakeRuntime([])
    result = await AgentReviewer(runtime).review(request(tmp_path, diff=""))
    assert result.verdict is Verdict.WRONG and runtime.sessions == []


def test_the_reviewer_sees_what_the_builder_ran_as_prat_recorded_it(tmp_path: Path) -> None:
    from prat.engine.critic import executed_commands
    from prat.engine.prompts import critic_task_prompt
    from prat.journal import Journal

    journal = Journal(tmp_path / "run.jsonl")
    journal.append(
        "runtime.tool_call", {"call_id": "a", "tool": "Bash", "input": {"command": "ls"}}
    )
    journal.append("runtime.tool_result", {"call_id": "a", "ok": True, "output": "app.py"})
    journal.append(
        "runtime.tool_call",
        {"call_id": "b", "tool": "Bash", "input": {"command": "curl https://example.com"}},
    )
    journal.append("runtime.tool_result", {"call_id": "b", "denied": True, "output": ""})
    journal.append("runtime.tool_call", {"call_id": "c", "tool": "Read", "input": {"path": "x"}})
    journal.append("runtime.tool_result", {"call_id": "c", "ok": True, "output": "secret-ish"})
    journal.append(
        "runtime.tool_call",
        {"call_id": "d", "tool": "Bash", "input": {"command": "curl -s 127.0.0.1:8611/health"}},
    )
    journal.append("runtime.tool_result", {"call_id": "d", "ok": True, "output": '{"ok":true}'})

    commands = executed_commands(str(journal.path))
    assert commands == [
        {"command": "ls", "output": "app.py"},
        {"command": "curl -s 127.0.0.1:8611/health", "output": '{"ok":true}'},
    ]
    prompt = critic_task_prompt(
        title="t",
        goal="g",
        acceptance_criteria="- show /health",
        summary="s",
        gates=[],
        diff="",
        diff_limit=1000,
        commands=commands,
    )
    assert "recorded by PRAT" in prompt and '$ curl -s 127.0.0.1:8611/health\n{"ok":true}' in prompt
    assert executed_commands(None) == [] and executed_commands(str(tmp_path / "none")) == []
