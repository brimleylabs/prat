from __future__ import annotations

from pathlib import Path

from conftest import git, make_repo
from prat.domain import Effort, FindingSource
from prat.improve.roadmap import MAX_CRITERIA, ROADMAP_REL, parse_roadmap, read_roadmap

REPO_ROADMAP = Path(__file__).resolve().parents[2] / "docs" / "ROADMAP.md"


def test_prats_own_roadmap_parses() -> None:
    items = parse_roadmap(REPO_ROADMAP.read_text(encoding="utf-8"))
    titles = [item.title for item in items]
    assert len(items) == 15
    assert titles[0] == "Project memory"
    assert titles[-1] == "Provenance on every run"
    assert not any("manual" in title.casefold() for title in titles)
    assert "Workspace provider interface" not in " ".join(titles)
    assert "Evaluation harness" not in titles
    for item in items:
        assert item.description, item.title
        assert 1 <= len(item.acceptance_criteria) <= MAX_CRITERIA, item.title
    assert items[0].effort is Effort.MEDIUM
    assert len({item.fingerprint for item in items}) == len(items)
    positions = [item.position for item in items]
    assert positions == sorted(positions)


def test_finished_and_manual_items_are_skipped() -> None:
    text = "\n".join(
        [
            "# Roadmap",
            "intro text",
            "## First",
            "Effort: low",
            "Do the first thing.",
            "Acceptance criteria:",
            "- one",
            "- two",
            "## Second (done)",
            "gone",
            "## Third (MANUAL)",
            "built by a person",
            "## Fourth",
            "No effort line and no criteria.",
        ]
    )
    items = parse_roadmap(text)
    assert [i.title for i in items] == ["First", "Fourth"]
    assert items[0].effort is Effort.LOW
    assert items[0].acceptance_criteria == ("one", "two")
    assert items[0].description == "Do the first thing."
    assert items[1].effort is Effort.MEDIUM
    assert items[1].acceptance_criteria == ()
    assert [i.position for i in items] == [1, 4]


def test_details_of_the_format() -> None:
    text = (
        "## Item\r\n"
        "Line one.\r\n"
        "\r\n"
        "### A subheading stays in the description\r\n"
        "Acceptance criteria:\r\n"
        "* starred bullet\r\n"
        "- a criterion that wraps\r\n"
        "  onto the next line\r\n" + "".join(f"- extra {n}\r\n" for n in range(10))
    )
    (item,) = parse_roadmap(text)
    assert "### A subheading stays in the description" in item.description
    assert item.acceptance_criteria[0] == "starred bullet"
    assert item.acceptance_criteria[1] == "a criterion that wraps onto the next line"
    assert len(item.acceptance_criteria) == MAX_CRITERIA


def test_the_fingerprint_follows_the_heading_only() -> None:
    first = parse_roadmap("## Card  drafter\nOne description.\n")[0]
    again = parse_roadmap("## card drafter\nA rewritten description.\nEffort: high\n")[0]
    other = parse_roadmap("## Card drafter two\nx\n")[0]
    assert first.fingerprint == again.fingerprint
    assert first.fingerprint != other.fingerprint


def test_items_become_roadmap_findings() -> None:
    (item,) = parse_roadmap("## Thing\nEffort: low\nWhy.\nAcceptance criteria:\n- done\n")
    draft = item.to_draft()
    assert draft.source is FindingSource.ROADMAP
    assert draft.fingerprint == item.fingerprint
    assert draft.proposal == "Why."
    assert draft.effort is Effort.LOW
    assert draft.evidence == {"roadmap_position": 1, "file": ROADMAP_REL}


def test_the_roadmap_is_read_from_the_branch_not_the_working_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "prat", files={ROADMAP_REL: "## Committed\nYes.\n"})
    (repo / ROADMAP_REL).write_text("## Uncommitted\nNo.\n", encoding="utf-8")
    assert [i.title for i in read_roadmap(repo, "main")] == ["Committed"]
    git(repo, "checkout", "-q", "-b", "other")
    assert read_roadmap(repo, "no-such-branch") == []


def test_no_roadmap_file_means_no_items(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "plain")
    assert read_roadmap(repo, "main") == []
