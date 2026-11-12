from __future__ import annotations

from collections.abc import Iterator

import pytest

from prat.build import build_commit


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    build_commit.cache_clear()
    yield
    build_commit.cache_clear()


def test_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAT_BUILD_COMMIT", "ABCDEF1234567")
    assert build_commit() == "abcdef1234567"


def test_a_malformed_value_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAT_BUILD_COMMIT", "not a commit; rm -rf /")
    assert build_commit() is None


def test_a_checkout_reports_its_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAT_BUILD_COMMIT", raising=False)
    commit = build_commit()
    # The test suite runs from a git checkout of PRAT.
    assert commit is None or len(commit) == 40
