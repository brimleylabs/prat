import io

import pytest

from rich.console import Console
from rich.rule import Rule
from rich.text import Text


def test_rule():
    console = Console(
        width=16,
        file=io.StringIO(),
        force_terminal=True,
        legacy_windows=False,
        _environ={},
    )
    console.print(Rule())
    console.print(Rule("foo"))
    console.rule(Text("foo", style="bold"))
    console.rule("foobarbazeggfoobarbazegg")
    expected = "\x1b[92m────────────────\x1b[0m\n"
    expected += "\x1b[92m───── \x1b[0mfoo\x1b[92m ──────\x1b[0m\n"
    expected += "\x1b[92m───── \x1b[0m\x1b[1mfoo\x1b[0m\x1b[92m ──────\x1b[0m\n"
    expected += "\x1b[92m─ \x1b[0mfoobarbazeg…\x1b[92m ─\x1b[0m\n"

    result = console.file.getvalue()
    assert result == expected


def test_rule_error():
    console = Console(width=16, file=io.StringIO(), legacy_windows=False, _environ={})
    with pytest.raises(ValueError):
        console.rule("foo", align="foo")




def test_rule_cjk():
    console = Console(
        width=16,
        file=io.StringIO(),
        force_terminal=True,
        color_system=None,
        legacy_windows=False,
        _environ={},
    )
    console.rule("欢迎！")
    expected = "──── 欢迎！ ────\n"
    assert console.file.getvalue() == expected




def test_rule_center_aligned_title_not_enough_space_for_rule():
    console = Console(width=4, file=io.StringIO(), record=True)
    console.rule("ABCD")
    assert console.file.getvalue() == "────\n"


@pytest.mark.parametrize("align", ["left", "right"])
def test_rule_side_aligned_not_enough_space_for_rule(align):
    console = Console(width=2, file=io.StringIO(), record=True)
    console.rule("ABCD", align=align)
    assert console.file.getvalue() == "──\n"




def test_characters():
    console = Console(
        width=16,
        file=io.StringIO(),
        force_terminal=True,
        color_system=None,
        legacy_windows=False,
        _environ={},
    )
    console.rule(characters="+*")
    console.rule("foo", characters="+*")
    console.print(Rule(characters=".,"))
    expected = "+*+*+*+*+*+*+*+*\n"
    expected += "+*+*+ foo +*+*+*\n"
    expected += ".,.,.,.,.,.,.,.,\n"
    assert console.file.getvalue() == expected


def test_repr():
    rule = Rule("foo")
    assert isinstance(repr(rule), str)


def test_error():
    with pytest.raises(ValueError):
        Rule(characters="")
