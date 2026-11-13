from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from prat.evals import (
    CaseError,
    CaseResult,
    EvalResult,
    builtin_suite,
    compare,
    find,
    load_case,
    load_cases,
    step_rates,
    summarize,
)
from prat.evals.export import toml_string
from prat.evals.results import Attempt, CheckOutcome, GateOutcome, evals_dir, save


def attempt(
    number: int, card: str, *, check: bool | None, verify: str = "succeeded", **kwargs: Any
) -> Attempt:
    return Attempt(
        number=number,
        card_status=card,
        steps={"prepare": "succeeded", "verify": verify},
        check=None if check is None else CheckOutcome(passed=check, exit_code=0 if check else 1),
        **kwargs,
    )


def case(name: str, *attempts: Attempt, has_check: bool = True) -> CaseResult:
    return CaseResult(
        name=name, title=name, project="p", has_check=has_check, attempts=list(attempts)
    )


def result(eval_id: str, *cases: CaseResult) -> EvalResult:
    return EvalResult(
        id=eval_id,
        created_at="2026-11-12T00:00:00+00:00",
        prat_version="0",
        suite="test",
        runtime="scripted",
        reviewer="scripted",
        cases=list(cases),
    )


def test_each_attempt_is_scored_against_the_hidden_check() -> None:
    assert attempt(1, "review", check=True).call == "passed"
    assert attempt(1, "review", check=False).call == "missed"
    assert attempt(1, "rework", check=False).call == "caught"
    assert attempt(1, "rework", check=True).call == "false_alarm"
    assert attempt(1, "failed", check=False, verify="failed").call == "caught"
    assert attempt(1, "failed", check=True, verify="failed").call == "false_alarm"
    # An agent error or a block is not a judgement on the work.
    assert attempt(1, "failed", check=False, verify="skipped").call is None
    assert attempt(1, "blocked", check=False).call is None
    assert attempt(1, "review", check=None).call is None


def test_case_outcomes_compare_the_first_attempt_with_what_was_handed_over() -> None:
    assert case("a", attempt(1, "review", check=True)).outcome == "kept"
    fixed = case("b", attempt(1, "rework", check=False), attempt(2, "review", check=True))
    assert fixed.outcome == "fixed"
    stopped = case(
        "c",
        attempt(1, "failed", check=False, verify="failed"),
        attempt(2, "failed", check=False, verify="failed"),
    )
    assert stopped.outcome == "stopped"
    lost = case("d", attempt(1, "rework", check=True), attempt(2, "rework", check=True))
    assert lost.outcome == "lost"
    assert case("e", attempt(1, "review", check=False)).outcome == "missed"
    unchecked = case("f", attempt(1, "review", check=None), has_check=False)
    assert unchecked.outcome == "handed_over"
    assert CaseResult(name="g", title="g", error="boom").outcome == "error"


def test_summary_counts_the_review_layer() -> None:
    cases = [
        case("kept", attempt(1, "review", check=True)),
        case("fixed", attempt(1, "rework", check=False), attempt(2, "review", check=True)),
        case("missed", attempt(1, "review", check=False)),
        case("stopped", attempt(1, "failed", check=False, verify="failed")),
        CaseResult(name="broken", title="broken", error="no repo"),
    ]
    summary = summarize(cases)
    assert summary.cases == 5
    assert summary.errors == 1
    assert summary.checked == 4
    assert summary.baseline_pass == 1
    assert summary.baseline_bad == 3
    assert summary.delivered == 3
    assert summary.delivered_pass == 2
    assert summary.delivered_bad == 1
    assert summary.calls == {"passed": 2, "caught": 2, "missed": 1}
    assert summary.catch_rate == pytest.approx(2 / 3)
    assert summary.false_alarm_rate == 0


def test_step_rates_leave_skipped_steps_out_of_the_rate() -> None:
    cases = [
        case("a", attempt(1, "review", check=True)),
        case("b", attempt(1, "failed", check=False, verify="failed")),
    ]
    rates = step_rates(cases)
    assert rates["verify"].succeeded == 1
    assert rates["verify"].failed == 1
    assert rates["verify"].rate == 0.5
    assert rates["prepare"].rate == 1.0
    assert "critique" not in rates


def test_results_are_stored_and_found(tmp_path: Path) -> None:
    home = tmp_path / "home"
    older = result("20261101-000000-aaaa", case("a", attempt(1, "review", check=True)))
    newer = result("20261102-000000-bbbb-label", case("a", attempt(1, "review", check=False)))
    for item in (older, newer):
        save(item, evals_dir(home) / item.id)
    assert find(home, "latest").id == newer.id
    assert find(home, "previous").id == older.id
    assert find(home, "20261101").id == older.id
    assert find(home, str(evals_dir(home) / older.id)).id == older.id
    loaded = find(home, "latest")
    assert loaded.cases[0].attempts[0].call == "missed"
    with pytest.raises(LookupError):
        find(home, "2026")
    with pytest.raises(LookupError):
        find(home, "nope")


def test_comparison_lines_up_the_cases_both_ran() -> None:
    before = result(
        "a",
        case("one", attempt(1, "review", check=False)),
        case("two", attempt(1, "review", check=True)),
        case("old", attempt(1, "review", check=True)),
    )
    after = result(
        "b",
        case("one", attempt(1, "rework", check=False), attempt(2, "review", check=True)),
        case("two", attempt(1, "review", check=True)),
        case("new", attempt(1, "review", check=True)),
    )
    comparison = compare(before, after)
    rows = {row.label: row for row in comparison.review}
    assert rows["bad work handed over"].a == "1"
    assert rows["bad work handed over"].b == "0"
    assert rows["PRAT hands over work that passes"].changed
    cases = {row.label: row for row in comparison.cases}
    assert cases["one"].a.startswith("missed")
    assert cases["one"].b.startswith("fixed")
    assert not cases["two"].changed
    assert comparison.only_a == ["old"]
    assert comparison.only_b == ["new"]


def test_the_attempt_trail_says_what_stopped_it() -> None:
    gate = attempt(1, "failed", check=None, verify="failed")
    gate.gates = [GateOutcome(name="test", passed=False)]
    assert gate.trail() == "test failed"
    judged = attempt(1, "rework", check=None, verdict="not_met")
    assert judged.trail() == "not_met"


def test_the_builtin_suite_loads() -> None:
    cases = load_cases(builtin_suite())
    assert [c.name for c in cases] == ["chunks", "clamp", "duration", "greet", "roman", "slugify"]
    for item in cases:
        assert item.spec.check is not None, item.name
        assert item.attempts, item.name
        assert (item.repo / ".prat" / "project.toml").is_file()
        assert (item.hidden / "hidden_check.py").is_file()
    assert [c.name for c in load_cases(builtin_suite(), ["greet"])] == ["greet"]
    with pytest.raises(CaseError, match="no such case"):
        load_cases(builtin_suite(), ["nothing"])


def write_case(folder: Path, toml: str = 'title = "T"\n', attempts: tuple[str, ...] = ()) -> Path:
    (folder / "repo").mkdir(parents=True)
    (folder / "case.toml").write_text(toml, encoding="utf-8")
    for name in attempts:
        (folder / "attempts").mkdir(exist_ok=True)
        (folder / "attempts" / name).write_text('[{"op": "finish"}]', encoding="utf-8")
    return folder


def test_scripted_reviews_repeat_the_last_and_default_to_met(tmp_path: Path) -> None:
    plain = load_case(write_case(tmp_path / "plain"))
    assert plain.review_for(3).verdict == "met"
    toml = (
        'title = "T"\n'
        '[[scripted_review]]\nverdict = "not_met"\n'
        '[[scripted_review]]\nverdict = "wrong"\n'
    )
    reviewed = load_case(write_case(tmp_path / "reviewed", toml))
    assert [reviewed.review_for(i).verdict for i in range(3)] == ["not_met", "wrong", "wrong"]


def test_bad_cases_are_refused(tmp_path: Path) -> None:
    with pytest.raises(CaseError, match="letters"):
        load_case(write_case(tmp_path / "Bad Name"))
    with pytest.raises(CaseError, match="numbered"):
        load_case(write_case(tmp_path / "gap", attempts=("1.json", "3.json")))
    with pytest.raises(CaseError, match=r"case\.toml"):
        load_case(write_case(tmp_path / "unknown", 'title = "T"\nsurprise = 1\n'))
    (tmp_path / "norepo").mkdir()
    (tmp_path / "norepo" / "case.toml").write_text('title = "T"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="repo/"):
        load_case(tmp_path / "norepo")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CaseError, match="no cases"):
        load_cases(empty)


@pytest.mark.parametrize(
    "text",
    ["plain", 'quote " and \\ backslash', "lines\nand\ttabs\r\n", "bell \x07 del \x7f", "naïve 🙂"],
)
def test_toml_strings_round_trip(text: str) -> None:
    assert tomllib.loads(f"value = {toml_string(text)}\n")["value"] == text


def test_the_plain_prompt_carries_what_prats_builder_gets(tmp_path: Path) -> None:
    from prat.evals.plain import plain_prompt
    from prat.projects import ProjectSpec

    toml = 'title = "Fix it"\ngoal = "It breaks."\nacceptance_criteria = "- it works"\n'
    case = load_case(write_case(tmp_path / "plain", toml))
    prompt = plain_prompt(case, ProjectSpec(name="p", notes="Run the tests with make test."))
    assert prompt.startswith("# Fix it\n\nIt breaks.")
    assert "- it works" in prompt
    assert "make test" in prompt


def test_a_source_needs_a_commit_id(tmp_path: Path) -> None:
    toml = 'title = "T"\n[source]\nrepository = "https://example.org/r.git"\ncommit = "main"\n'
    with pytest.raises(CaseError):
        load_case(write_case(tmp_path / "bad-source", toml))
    toml = 'title = "T"\n[source]\nrepository = "--upload-pack=x"\ncommit = "abc1234"\n'
    with pytest.raises(CaseError):
        load_case(write_case(tmp_path / "option-source", toml))


def test_the_shipped_benchmark_cases_load() -> None:
    root = Path(__file__).resolve().parents[2] / "evals"
    rich = load_cases(root / "rich")
    assert [c.name for c in rich] == ["ellipsis-width", "filesize-boundary", "ratio-columns"]
    for case in rich:
        assert case.spec.source is not None
        assert case.spec.check is not None
        assert case.project_file.is_file()
        assert list(case.hidden.rglob("test_*.py"))
    tinyshop = load_cases(root / "tinyshop")
    assert [c.name for c in tinyshop] == ["partial-refund", "weekend-delivery"]
    for case in tinyshop:
        assert (case.repo / ".prat" / "project.toml").is_file()
        assert (case.hidden / "hidden_check.py").is_file()
