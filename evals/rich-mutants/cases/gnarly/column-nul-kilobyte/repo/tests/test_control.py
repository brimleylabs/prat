from rich.control import Control, escape_control_codes, strip_control_codes
from rich.segment import ControlType, Segment


def test_control():
    control = Control(ControlType.BELL)
    assert str(control) == "\x07"


def test_strip_control_codes():
    assert strip_control_codes("") == ""
    assert strip_control_codes("foo\rbar") == "foobar"
    assert strip_control_codes("Fear is the mind killer") == "Fear is the mind killer"


def test_escape_control_codes():
    assert escape_control_codes("") == ""
    assert escape_control_codes("foo\rbar") == "foo\\rbar"
    assert escape_control_codes("Fear is the mind killer") == "Fear is the mind killer"


def test_control_move_to():
    control = Control.move_to(5, 10)
    print(control.segment)
    assert control.segment == Segment(
        "\x1b[11;6H", None, [(ControlType.CURSOR_MOVE_TO, 5, 10)]
    )


def test_control_move():
    assert Control.move(0, 0).segment == Segment("", None, [])
    control = Control.move(3, 4)
    print(repr(control.segment))
    assert control.segment == Segment(
        "\x1b[3C\x1b[4B",
        None,
        [(ControlType.CURSOR_FORWARD, 3), (ControlType.CURSOR_DOWN, 4)],
    )




def test_title():
    control_segment = Control.title("hello").segment
    assert control_segment == Segment(
        "\x1b]0;hello\x07",
        None,
        [(ControlType.SET_WINDOW_TITLE, "hello")],
    )
