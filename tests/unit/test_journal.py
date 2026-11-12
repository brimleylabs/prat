from __future__ import annotations

from pathlib import Path

from prat.journal import Journal, read_journal, trim


def test_append_and_read(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "run-1.jsonl")
    journal.append("step.started", {"step": "prepare"})
    journal.append("runtime.text", {"text": "hello"})
    entries = list(read_journal(journal.path))
    assert [e.seq for e in entries] == [1, 2]
    assert [e.kind for e in entries] == ["step.started", "runtime.text"]
    assert entries[1].data == {"text": "hello"}
    assert entries[0].ts.tzinfo is not None


def test_sequence_continues_across_writers(tmp_path: Path) -> None:
    path = tmp_path / "run-1.jsonl"
    Journal(path).append("a")
    Journal(path).append("b")
    assert [e.seq for e in read_journal(path)] == [1, 2]


def test_payloads_are_trimmed(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl", payload_limit=200)
    journal.append("tool", {"output": "x" * 1000, "nested": {"list": ["y" * 500]}})
    [entry] = read_journal(journal.path)
    assert entry.data["output"].startswith("x" * 200)
    assert "trimmed 800 chars" in entry.data["output"]
    assert "trimmed 300 chars" in entry.data["nested"]["list"][0]


def test_reader_skips_a_torn_final_line(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("ok")
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts": "2026-01-01T00:00:00+00:00", "ki')
    assert [e.kind for e in read_journal(journal.path)] == ["ok"]


def test_missing_journal_reads_empty(tmp_path: Path) -> None:
    assert list(read_journal(tmp_path / "nope.jsonl")) == []


def test_trim_handles_odd_values() -> None:
    assert trim(None, 10) is None
    assert trim(3, 10) == 3
    assert trim(("a", "b"), 10) == ["a", "b"]
    assert trim(Path("x"), 10) == str(Path("x"))
