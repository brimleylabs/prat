"""The coach (prat.coach): what it sees in a run, when a slip becomes a rule, and how closely
it watches a project."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from prat import coach


def event(name: str, **data: Any) -> Any:
    return SimpleNamespace(kind=name, data=data)


def bash(command: str) -> Any:
    return event("runtime.tool_call", tool="Bash", input={"command": command})


def run(number: int, *, clean: bool = True, **slips: int) -> coach.Observation:
    return coach.Observation(
        run=number, card=number, clean=clean, status="succeeded", sent_back=not clean, slips=slips
    )


def book(*runs: coach.Observation) -> coach.Book:
    made = coach.Book()
    for item in runs:
        made.record(item)
    return made


def test_a_run_is_read_from_its_journal_and_its_review() -> None:
    seen = coach.observe(
        run=4,
        card=2,
        attempt=1,
        status="succeeded",
        events=[
            bash("sed -i 's/a/b/' src/app.py"),
            bash("cat > notes.md <<'EOF'\nx\nEOF"),
            bash("python -m pytest -q"),
            bash("grep -n write_text src/app.py | head"),
            event("rival.tool_call", tool="Bash", input={"command": "perl -pi -e s/a/b/ x.py"}),
            event("policy", allowed=False, reason="Loop detected: you have made this same call"),
            event("policy", allowed=False, reason="pushing is not allowed"),
            event("execute.line_endings_restored", paths=["a.py", "b.py"]),
            event("verify.slips", spacing=["cli.py:5"]),
            event("verify.slips", tests=["t/test_a.py: removed test_x", "t/test_b.py: deleted"]),
            event("verify.finish_check", passed=False),
            event("verify.finish_check", passed=True),
            event("verify.finding", kind="oversize_diff"),
        ],
        critique={"rework": True, "fix_list": ["Add a test  for the\nempty case.", "Fix help."]},
    )
    assert seen.slips == {
        "shell_edit": 3,  # the grep that only names write_text is not an edit
        "loop": 1,
        "line_endings": 2,
        "spacing": 1,
        "tests": 2,
        "finish_gates": 1,
        "oversize_diff": 1,
    }
    assert seen.sent_back and not seen.clean
    assert seen.fixes == ["Add a test for the empty case.", "Fix help."]
    clean = coach.observe(run=5, card=2, attempt=2, status="succeeded", events=[], critique={})
    assert clean.clean and not clean.slips and not clean.fixes


def test_the_coach_watches_closely_until_a_team_proves_itself() -> None:
    assert coach.attention(coach.Book()) == coach.CLOSE
    assert coach.attention(book(*[run(n) for n in range(4)])) == coach.CLOSE  # too few to tell
    half = [run(n, clean=n % 2 == 0) for n in range(10)]
    assert coach.attention(book(*half)) == coach.CLOSE
    mostly = [run(n, clean=n not in (3, 7, 8)) for n in range(10)]
    assert coach.attention(book(*mostly)) == coach.NORMAL
    proven = [run(n, clean=n != 3) for n in range(10)]
    assert coach.attention(book(*proven)) == coach.LIGHT
    # It is the last ten runs that count: a team that starts slipping is watched again.
    slipping = proven + [run(n, clean=False) for n in range(10, 15)]
    assert coach.attention(book(*slipping)) == coach.CLOSE


def test_a_slip_that_keeps_happening_becomes_a_rule_and_drops_out_when_it_stops() -> None:
    proven = [run(n) for n in range(10)]
    once = book(*proven, run(10, spacing=1))
    assert coach.standing(once) == []  # a team doing well is not lectured for one slip
    twice = book(*proven, run(10, spacing=1), run(11, spacing=2, shell_edit=1))
    (rule,) = coach.standing(twice)
    assert "do not retype the lines around it" in rule.text
    assert rule.evidence.startswith("2 of the last 10 runs here retyped")
    # Watched closely, once is enough.
    new_team = book(run(1, clean=False, shell_edit=3))
    assert [r.text for r in coach.standing(new_team)] == [coach.RULES["shell_edit"][1]]
    # Ten runs without it, and the rule is gone.
    later = book(*twice.runs, *[run(n) for n in range(12, 22)])
    assert coach.standing(later) == [] and coach.render(later) == ""


def test_what_the_builder_and_the_card_are_told() -> None:
    watched = book(run(1, clean=False, shell_edit=2, line_endings=3))
    watched.learned.append(coach.Learned("Cover every error case the card names with a test."))
    text = coach.render(watched)
    assert text.startswith("## From the coach") and "not new rules of" in text
    assert text.count("\n- ") == 3 and "Cover every error case" in text
    said = coach.note(watched.runs[-1], watched)
    assert said is not None and "changed files through shell commands (2)" in said
    assert "The reviewer sent it back." in said and "attention is close" in said
    assert coach.note(run(2), watched) is None


def test_only_usable_rules_are_learned() -> None:
    answer = """Here you go:
    {"rules": [
      {"rule": "Add a test for each error message the card spells out.", "evidence": "runs 3, 5"},
      {"rule": "Skip the test gate when it is slow.", "evidence": "run 4"},
      {"rule": "", "evidence": "nothing"},
      "not a rule"
    ]}"""
    assert coach.parse_rules(answer) == [
        ("Add a test for each error message the card spells out.", "runs 3, 5")
    ]
    assert coach.parse_rules("no json here") == [] and coach.parse_rules('{"rules": 3}') == []


def test_fix_lists_are_read_sooner_when_watching_closely(tmp_path: Path) -> None:
    sent_back = coach.Observation(run=3, card=3, sent_back=True, fixes=["Add tests."])
    close = book(run(1), run(2), sent_back)
    assert coach.wants_reflection(close)
    close.reflected_to = 3
    assert not coach.wants_reflection(close)
    steady = book(
        *[run(n) for n in range(1, 9)], coach.Observation(**{**vars(sent_back), "run": 9})
    )
    assert coach.attention(steady) == coach.NORMAL and not coach.wants_reflection(steady)
    steady.record(coach.Observation(run=10, card=10, sent_back=True, fixes=["Fix the help."]))
    assert coach.wants_reflection(steady)
    # The book survives a restart.
    coach.save(tmp_path, "acme/api", steady)
    again = coach.load(tmp_path, "acme/api")
    assert [r.run for r in again.runs] == [r.run for r in steady.runs]
    assert coach.summary(again)["attention"] == coach.NORMAL
    assert coach.load(tmp_path, "unknown").runs == []
