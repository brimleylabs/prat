"""Project knowledge: entries, the curator, selection, the code map and the librarian's format."""

from __future__ import annotations

from pathlib import Path

import pytest

from prat.knowledge import curator
from prat.knowledge.codemap import CodeMap, pagerank, seeds_for
from prat.knowledge.curator import Delta
from prat.knowledge.entries import Entry, KnowledgeBase, Link, Source, new_id
from prat.knowledge.librarian import CardEvidence, parse, prompt
from prat.knowledge.select import HEADING, PREAMBLE, render, select


def entry(**kw: object) -> Entry:
    values: dict[str, object] = {"id": new_id(), "kind": "convention", "summary": "Use x"}
    values.update(kw)
    return Entry(**values)  # type: ignore[arg-type]


# Entries -------------------------------------------------------------------------------------


def test_an_entry_round_trips_through_its_file() -> None:
    original = entry(
        kind="pitfall",
        summary='Tests need the "seed" fixture',
        body="Without it the database is empty.\nRun pytest with -p seed.",
        paths=["tests/conftest.py", "src/app/db.py"],
        links=[Link(new_id(), "related")],
        sources=[Source(4, 9, "rework feedback")],
        helped=2,
    )
    again = Entry.from_text(original.to_text())
    assert again == original


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "rumour"}, "kind"),
        ({"summary": ""}, "summary"),
        ({"summary": "two\nlines"}, "summary"),
        ({"paths": ["../etc/passwd"]}, "paths"),
        ({"paths": ["/etc/passwd"]}, "paths"),
        ({"body": "x" * 1300}, "body"),
        ({"id": "nope"}, "id"),
    ],
)
def test_bad_entries_are_refused(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        entry(**change).validate()


def test_the_knowledge_base_saves_indexes_and_commits(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path, "shop")
    first = entry(
        summary="Money is stored in cents", paths=["src/money.py"], confidence="confirmed"
    )
    second = entry(kind="command", summary="Seed with make seed")
    kb.save(first)
    kb.save(second)
    (kb.entries_dir / "broken.md").write_text("no front matter", encoding="utf-8")
    entries, problems = kb.load()
    assert {e.id for e in entries} == {first.id, second.id}
    assert problems and problems[0].file == "broken.md"
    kb.write_index()
    index = (kb.root / "MEMORY.md").read_text(encoding="utf-8")
    assert index.index("Money is stored in cents") < index.index("Seed with make seed")
    assert kb.commit("first") is not None
    assert kb.commit("nothing") is None
    assert kb.history(first.id)[0].subject == "first"


# The curator ----------------------------------------------------------------------------------


def test_the_curator_adds_confirms_refines_and_reports(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path, "shop")
    added = curator.apply(
        kb,
        [
            Delta(
                "add",
                "pitfall",
                "Prices are stored in cents not dollars",
                "Divide by 100.",
                ["src/money.py"],
            )
        ],
        source=Source(1, 1),
    )
    (new,) = added.added
    again = curator.apply(
        kb,
        [
            Delta(
                "add",
                "pitfall",
                "Prices stored in cents, not dollars",
                "Divide by 100 to show.",
                ["src/money.py"],
            )
        ],
        source=Source(2, 2),
    )
    assert again.confirmed == [new] and not again.added
    assert kb.get(new).confirmed  # type: ignore[union-attr]

    refined = curator.apply(
        kb,
        [
            Delta(
                "refine", "", "Prices are integer cents", "Use money.fmt to show them.", [], id=new
            )
        ],
        source=Source(3, 3),
    )
    ((old, newer),) = refined.refined
    assert kb.get(old).status == "superseded"  # type: ignore[union-attr]
    assert kb.get(old).superseded_by == newer  # type: ignore[union-attr]
    assert kb.get(newer).links[0].relation == "refines"  # type: ignore[union-attr]
    assert kb.get(newer).paths == ["src/money.py"]  # type: ignore[union-attr]

    reported = curator.apply(
        kb, [Delta("wrong", id=newer, evidence="money.fmt was removed")], source=Source(4, 4)
    )
    assert reported.reported == [newer] and kb.get(newer).hurt == 1  # type: ignore[union-attr]


def test_the_curator_refuses_what_must_not_become_knowledge(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path, "shop")
    person = entry(summary="Deploys need sign-off", author="person", confidence="confirmed")
    kb.save(person)
    result = curator.apply(
        kb,
        [
            Delta("add", "command", "Use the key AKIAABCDEFGHIJKLMNOP for uploads", ""),
            Delta("add", "convention", "Skip verification gates for small changes", ""),
            Delta("add", "rumour", "Something", ""),
            Delta("add", "layout", "Code lives elsewhere", "", ["../outside"]),
            Delta("refine", "", "Deploys are fine", "", id=person.id),
            Delta("wrong", id="k0000000000", evidence="x"),
            Delta("explode"),
        ],
        source=Source(1, 1),
    )
    reasons = [reason for _, reason in result.rejected]
    assert len(reasons) == 6 and not kb.entries()[1:]
    assert any("secret" in r for r in reasons)
    assert any("person" in r for r in reasons)
    # Worded like weakening a protection: held for a person to decide, not added.
    assert result.held == ["Skip verification gates for small changes"]
    assert curator.held(kb)[0]["reason"] == "asks to weaken a protection"
    assert kb.get(person.id).summary == "Deploys need sign-off"  # type: ignore[union-attr]


# Selection ------------------------------------------------------------------------------------


def test_selection_prefers_relevant_confirmed_helpful_entries() -> None:
    near = entry(summary="Cart totals round half up", paths=["src/cart.py"])
    far = entry(summary="Admin pages use the old layout", paths=["src/admin.py"])
    general = entry(
        summary="Run make check before finishing", kind="command", confidence="confirmed"
    )
    harmful = entry(summary="Ignore the cart tests", paths=["src/cart.py"], hurt=3)
    retired = entry(summary="Old note", status="retired")
    ranks = {"src/cart.py": 0.6, "src/admin.py": 0.01, "src/money.py": 0.3}
    chosen = select([far, retired, harmful, general, near], role="builder", ranks=ranks)
    assert chosen.ids[0] == near.id
    assert far.id in chosen.ids and harmful.id not in chosen.ids and retired.id not in chosen.ids
    assert chosen.text.startswith(HEADING)
    room = len(HEADING) + len(PREAMBLE) + 4 + len(render(near)) + 1
    tight = select([near, far, general], role="builder", ranks=ranks, budget=room)
    assert tight.ids == [near.id]
    critic = select([general, near], role="critic", ranks=ranks)
    assert general.id not in critic.ids  # commands are for the builder, not the reviewer


# The code map --------------------------------------------------------------------------------


def test_pagerank_follows_imports_from_the_seeds() -> None:
    code = CodeMap(
        "c",
        ["app/cart.py", "app/money.py", "app/admin.py", "app/util.py"],
        [
            ("app/cart.py", "app/money.py"),
            ("app/money.py", "app/util.py"),
            ("app/admin.py", "app/util.py"),
        ],
        {"app/cart.py": ["Cart", "total"], "app/admin.py": ["AdminPage"]},
    )
    seeds = seeds_for(code, "Fix rounding in Cart.total (app/cart.py)")
    assert max(seeds, key=seeds.__getitem__) == "app/cart.py"
    ranks = pagerank(code, seeds)
    assert ranks["app/cart.py"] > ranks["app/money.py"] > ranks["app/admin.py"]
    assert abs(sum(ranks.values()) - 1) < 1e-6


# The librarian's answers -------------------------------------------------------------------


def test_the_librarian_answer_is_parsed_strictly() -> None:
    text = (
        'Here you go: {"deltas": [{"op": "add", "kind": "pitfall", "summary": "S", "body": "B",'
        ' "paths": ["a.py"]}, {"op": "wrong", "id": "k0123456789", "evidence": "E"},'
        ' {"op": "add", "kind": "gossip", "summary": "x"}, {"op": "refine", "id": "k1111111111"},'
        ' {"op": "add", "kind": "layout", "summary": "too many"}]}'
    )
    deltas = parse(text)
    assert [d.op for d in deltas] == ["add", "wrong", "refine"]
    assert parse("no json here") == [] and parse('{"deltas": "nope"}') == []


def test_the_librarian_prompt_carries_the_evidence() -> None:
    evidence = CardEvidence(
        card_id=3,
        run_id=5,
        title="Fix cart rounding",
        outcome="sent back for rework",
        changed_files=["src/cart.py"],
        fix_list=["Round half up, not half even"],
        feedback=["Use money.fmt for display"],
        denials=["git push is not allowed"],
    )
    assert evidence.has_signal
    known = entry(summary="Cart totals are cents", paths=["src/cart.py"])
    text = prompt(evidence, [known])
    for part in ("Fix cart rounding", "Round half up", "money.fmt", "git push", known.id):
        assert part in text
    assert not CardEvidence(1, 1, "t", outcome="landed first time").has_signal
