"""Job progress: phases, rate over the last minutes, ETA, errors, stalls; and the helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prat.jobs import read_progress, stalled
from prat.progress import Progress


def write(path: Path, lines: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def test_rate_eta_and_errors_come_from_the_recent_lines(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    start = 1_000_000.0
    lines: list[dict[str, object]] = [{"ts": start, "phase": "crawl", "done": 0, "total": 100}]
    lines += [
        {"ts": start + 60 * i, "phase": "crawl", "done": 10 * i, "total": 100, "errors": i}
        for i in range(1, 6)
    ]
    write(path, lines)
    view = read_progress(path, now=start + 300)
    (phase,) = view.phases
    assert phase.done == 50 and phase.fraction == 0.5
    assert phase.rate == pytest.approx(10 / 60)
    assert phase.eta_low == pytest.approx(300) and phase.eta_high == pytest.approx(300)
    assert view.errors == 5 and view.error_rate == pytest.approx(0.1)


def test_a_slowdown_moves_the_eta_at_once(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    start = 1_000_000.0
    fast = [{"ts": start + 10 * i, "done": 10 * i, "total": 1000} for i in range(60)]
    slow = [{"ts": start + 600 + 60 * i, "done": 590 + i, "total": 1000} for i in range(1, 4)]
    write(path, fast + slow)
    phase = read_progress(path, now=start + 800).phases[0]
    assert phase.eta_high is not None and phase.eta_low is not None
    assert phase.eta_high > 400 * 30  # the last two minutes run at one item a minute


def test_phases_and_lines_without_times(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    write(path, [{"phase": "crawl", "done": 5}, {"phase": "summarise", "done": 1, "total": 9}])
    view = read_progress(path)
    assert [p.name for p in view.phases] == ["crawl", "summarise"]
    assert view.current is not None and view.current.name == "summarise"


def test_no_progress_for_too_long_is_a_stall(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    write(path, [{"ts": 1000.0, "done": 1}])
    view = read_progress(path)
    assert stalled("running", view, 900.0, minutes=10, now=1000.0 + 601)
    assert not stalled("running", view, 900.0, minutes=10, now=1000.0 + 599)
    assert not stalled("succeeded", view, 900.0, minutes=10, now=99999.0)


def test_the_helper_writes_lines_prat_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "p.jsonl"
    monkeypatch.setenv("PRAT_PROGRESS", str(path))
    progress = Progress(total=3, phase="enrich", unit="campgrounds", every_seconds=0)
    for name in ("a", "b", "c"):
        progress.advance(item=name)
    progress.tokens(100, 20, "some-model")
    progress.finish()
    view = read_progress(path)
    assert view.phases[0].done == 3 and view.phases[0].unit == "campgrounds"
    assert view.item == "c" and view.tokens_in == 100


def test_without_prat_the_helper_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAT_PROGRESS", raising=False)
    Progress(total=1).advance()
