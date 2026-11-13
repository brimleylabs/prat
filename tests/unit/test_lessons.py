"""Lessons: the answer's shape, and the check that every code excerpt is really in the diff."""

from __future__ import annotations

import json

from prat.lessons import Excerpt, Lesson, check, diff_sides, parse, render

DIFF = """\
diff --git a/rich/ratio.py b/rich/ratio.py
index 1111111..2222222 100644
--- a/rich/ratio.py
+++ b/rich/ratio.py
@@ -10,7 +10,7 @@ def ratio_reduce(total, ratios):
     total_ratio = sum(ratios)
     if not total_ratio:
         return [0] * len(ratios)
-    for ratio in ratios[:-1]:
+    for ratio in ratios:
         distributed = total * ratio / total_ratio
         values.append(distributed)
"""


def answer(**overrides: object) -> str:
    data: dict[str, object] = {
        "title": "Loops that skip the last item",
        "idea": "Slicing with [:-1] leaves out the last element.",
        "happened": "The last ratio got nothing.",
        "excerpts": [
            {"file": "rich/ratio.py", "code": "    for ratio in ratios:", "note": "every ratio"}
        ],
        "why": "The loop must visit every ratio.",
        "alternatives": ["Give the remainder to the last item."],
        "question": "What if ratios is empty?",
        "answer": "The early return covers it.",
        "tags": ["Off-By-One"],
    }
    data.update(overrides)
    return "Here it is:\n" + json.dumps(data)


def test_an_answer_becomes_a_lesson() -> None:
    lesson = parse(answer())
    assert lesson is not None
    assert lesson.title == "Loops that skip the last item"
    assert lesson.excerpts[0].file == "rich/ratio.py"
    assert lesson.tags == ["off-by-one"]


def test_an_answer_without_the_three_layers_is_no_lesson() -> None:
    assert parse(answer(why="")) is None
    assert parse("not json at all") is None


def test_the_diff_is_read_into_its_old_and_new_sides() -> None:
    old, new = diff_sides(DIFF)["rich/ratio.py"]
    assert "    for ratio in ratios[:-1]:" in old and "    for ratio in ratios[:-1]:" not in new
    assert "    for ratio in ratios:" in new
    context = "        values.append(distributed)"
    assert context in old and context in new


def test_excerpts_found_in_the_diff_are_kept_and_invented_ones_dropped() -> None:
    lesson = Lesson(
        idea="i",
        happened="h",
        why="w",
        excerpts=[
            Excerpt(
                "rich/ratio.py",
                "    for ratio in ratios:\n        distributed = total * ratio / total_ratio",
            ),
            Excerpt("rich/ratio.py", "    for ratio in ratios[:-1]:"),
            Excerpt("rich/ratio.py", "    for r in ratios:  # tidied by the model"),
        ],
    )
    checked = check(lesson, DIFF, lambda path: None)
    assert [e.code.split("\n")[0] for e in checked.excerpts] == [
        "    for ratio in ratios:",
        "    for ratio in ratios[:-1]:",
    ]
    assert checked.dropped == 1 and checked.checked


def test_an_excerpt_from_the_file_at_the_head_commit_counts() -> None:
    lesson = Lesson(idea="i", happened="h", why="w", excerpts=[Excerpt("rich/x.py", "def x():")])
    files = {"rich/x.py": "import os\r\ndef x():\r\n    pass\r\n"}
    assert check(lesson, DIFF, files.get).excerpts


def test_a_lesson_whose_excerpts_all_fail_is_marked_unchecked() -> None:
    lesson = Lesson(idea="i", happened="h", why="w", excerpts=[Excerpt("a.py", "made up")])
    checked = check(lesson, DIFF, lambda path: None)
    assert not checked.checked and checked.dropped == 1
    assert "Unchecked" in render(checked)


def test_the_rendered_lesson_has_its_three_layers() -> None:
    lesson = parse(answer())
    assert lesson is not None
    text = render(lesson)
    for heading in ("## The idea", "## What happened here", "## Why this way", "## A question"):
        assert heading in text
    assert "## The trap" not in text  # never invented for a card that went smoothly
