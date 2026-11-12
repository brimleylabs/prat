from __future__ import annotations

import pytest

from prat.policy.shell import ShellParseError, split_commands, substitutions


def argvs(command: str) -> list[list[str]]:
    return [c.argv for c in split_commands(command)]


def test_operators_split_commands() -> None:
    assert argvs("a 1 && b 2 || c; d | e & f") == [
        ["a", "1"],
        ["b", "2"],
        ["c"],
        ["d"],
        ["e"],
        ["f"],
    ]


def test_quotes_are_kept_together() -> None:
    assert argvs("echo 'a && b' \"c; d\"") == [["echo", "a && b", "c; d"]]


def test_newlines_and_continuations() -> None:
    assert argvs("a\nb") == [["a"], ["b"]]
    assert argvs("a \\\n  --flag") == [["a", "--flag"]]


def test_comments_do_not_hide_commands() -> None:
    assert ["git", "push"] in argvs("echo x # hidden\ngit push")


def test_redirections() -> None:
    [cmd] = split_commands("python run.py > out.txt 2>&1")
    assert cmd.argv == ["python", "run.py"]
    writes = [r.target for r in cmd.redirects if r.is_write and not r.is_fd_dup]
    assert writes == ["out.txt"]
    [cmd] = split_commands("sort < in.txt >> out.txt")
    assert [(r.op, r.target) for r in cmd.redirects] == [("<", "in.txt"), (">>", "out.txt")]


def test_assignments() -> None:
    [cmd] = split_commands("A=1 B=two git status")
    assert cmd.assignments == {"A": "1", "B": "two"}
    assert cmd.argv == ["git", "status"]


def test_substitutions() -> None:
    assert substitutions("echo $(git push)") == ["git push"]
    assert substitutions('echo "$(a $(b))"') == ["a $(b)", "b"]
    assert substitutions("echo `whoami`") == ["whoami"]
    assert substitutions("diff <(ls a) <(ls b)") == ["ls a", "ls b"]
    assert substitutions("echo '$(not run)'") == []
    assert substitutions("echo $((1 + 2))") == []


def test_unbalanced_quotes_raise() -> None:
    with pytest.raises(ShellParseError):
        split_commands("echo 'oops")
