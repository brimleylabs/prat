from __future__ import annotations

import pytest

from prat.policy import Decision, ToolCall
from prat.runtime.loop import LoopDetector, call_key


def test_fifth_identical_call_in_eight_is_denied() -> None:
    detector = LoopDetector()
    call = ToolCall.read("src/app.py")
    results = [detector.check(call) for _ in range(4)]
    assert results == [None] * 4
    denial = detector.check(call)
    assert denial is not None and not denial.allowed
    assert "Stop and reconsider" in denial.reason and "BLOCKED" in denial.reason
    assert detector.check(call) is not None  # keeps denying while it persists
    assert detector.tripped == 2


def test_spread_out_calls_are_fine() -> None:
    detector = LoopDetector(threshold=3, window=4)
    same = ToolCall.bash("pytest -q")
    others = [ToolCall.read(f"f{i}.py") for i in range(10)]
    for i in range(5):
        assert detector.check(same) is None
        assert detector.check(others[2 * i]) is None
        assert detector.check(others[2 * i + 1]) is None


def test_targets_are_normalised() -> None:
    assert call_key(ToolCall.bash("git  status")) == call_key(ToolCall.bash("git status"))
    assert call_key(ToolCall.read("a\\b.py")) == call_key(ToolCall.read("a/b.py"))
    assert call_key(ToolCall.read("a.py")) != call_key(ToolCall.write("a.py", "x"))
    assert call_key(ToolCall.mcp("t", {"b": 1, "a": 2})) == call_key(
        ToolCall.mcp("t", {"a": 2, "b": 1})
    )


def test_guard_skips_the_policy_on_a_loop() -> None:
    asked: list[ToolCall] = []

    def policy(call: ToolCall) -> Decision:
        asked.append(call)
        return Decision.allow()

    guarded = LoopDetector(threshold=2, window=2).guard(policy)
    assert guarded(ToolCall.read("x")).allowed
    assert not guarded(ToolCall.read("x")).allowed
    assert len(asked) == 1


def test_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="threshold"):
        LoopDetector(threshold=5, window=3)


def test_many_different_edits_and_reads_of_one_file_are_work() -> None:
    from prat.runtime.claude import tool_calls_for

    detector = LoopDetector()
    path = "coverage/cmdline.py"
    for n in range(12):
        (edit,) = tool_calls_for(
            "Edit", {"file_path": path, "old_string": f"line {n}", "new_string": f"line {n}!"}
        )
        (read,) = tool_calls_for("Read", {"file_path": path, "offset": n * 100, "limit": 100})
        (grep, *_) = tool_calls_for("Grep", {"pattern": f"def f{n}", "path": "."})
        assert [detector.check(call) for call in (edit, read, grep)] == [None, None, None]
    # The same edit, the same read and the same search again and again are still loops.
    for name, tool_input in (
        ("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}),
        ("Read", {"file_path": path, "offset": 1, "limit": 50}),
        ("Grep", {"pattern": "needle", "path": "."}),
    ):
        again = LoopDetector()
        calls = [tool_calls_for(name, tool_input)[0] for _ in range(5)]
        assert [again.check(call) is None for call in calls] == [True, True, True, True, False]
