"""Split a shell command line into simple commands for policy analysis.

This is not a shell. It is a conservative tokenizer built on :mod:`shlex`:
anything it splits too eagerly only makes the policy stricter. It handles
operators (``&&``, ``||``, ``;``, ``|``, ``&``), subshell parentheses,
redirections, leading ``NAME=value`` assignments, line continuations and
command substitution (``$(...)``, backticks, ``<(...)``), whose inner text is
returned separately so the caller can analyse it as well.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PUNCT = set("();<>|&")
_REDIRECT_CHARS = set("<>&")


class ShellParseError(ValueError):
    pass


# A here-document operator: <<WORD, <<-WORD, <<'WORD', <<"WORD" (not <<<, a here-string).
_HEREDOC = re.compile(r"(?<!<)<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|([A-Za-z_][\w.-]*))")


def split_heredocs(command: str) -> tuple[str, str]:
    """Take here-document bodies out of ``command``.

    Returns the command to parse, with each body and its operator removed (a body is data on
    the command's standard input, not commands), and the text to scan for command
    substitutions, which keeps the bodies of unquoted here-documents: the shell expands
    ``$(...)`` there, but not in a quoted one (``<<'EOF'``). An unterminated body runs to the
    end, as in the shell."""
    lines = command.split("\n")
    parse: list[str] = []
    scan: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        found = list(_HEREDOC.finditer(line))
        if not found:
            parse.append(line)
            scan.append(line)
            i += 1
            continue
        parse.append(_HEREDOC.sub(" ", line))
        scan.append(_HEREDOC.sub(" ", line))
        i += 1
        for match in found:
            strip_tabs = match.group(1) == "-"
            quoted = match.group(2) is not None or match.group(3) is not None
            word = match.group(2) or match.group(3) or match.group(4)
            while i < len(lines):
                body = lines[i]
                i += 1
                if (body.lstrip("\t") if strip_tabs else body) == word:
                    break
                if not quoted:
                    scan.append(body)
    return "\n".join(parse), "\n".join(scan)


@dataclass
class Redirect:
    op: str
    target: str

    @property
    def is_write(self) -> bool:
        return ">" in self.op

    @property
    def is_fd_dup(self) -> bool:
        return self.target.startswith("&") or (self.target.isdigit() and self.op.endswith("&"))


@dataclass
class SimpleCommand:
    argv: list[str]
    assignments: dict[str, str] = field(default_factory=dict)
    redirects: list[Redirect] = field(default_factory=list)
    # Separator that followed this command ("" at the end).
    separator: str = ""

    @property
    def name(self) -> str:
        return self.argv[0] if self.argv else ""


def _is_operator(token: str) -> bool:
    return bool(token) and all(ch in _PUNCT for ch in token)


def _is_redirect(token: str) -> bool:
    return _is_operator(token) and ("<" in token or ">" in token) and set(token) <= _REDIRECT_CHARS


def substitutions(command: str) -> list[str]:
    """Inner texts of ``$(...)``, ``<(...)``, ``>(...)`` and backtick spans.

    Single-quoted text is skipped (the shell does not expand it there).
    Nested substitutions are returned too, outermost first.
    """
    return [inner for _, inner in located_substitutions(command, nested=True)]


def located_substitutions(command: str, *, nested: bool = False) -> list[tuple[int, str]]:
    """Like :func:`substitutions`, with where each one starts in ``command``, so the caller
    can tell which directory the shell will be in when it runs. Nested ones are included
    only with ``nested`` (their positions are then within their parent's text)."""
    found: list[tuple[int, str]] = []
    i = 0
    n = len(command)
    in_single = False
    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single:
            i += 2
            continue
        if ch == "'":
            in_single = not in_single
            i += 1
            continue
        if in_single:
            i += 1
            continue
        if ch in "$<>" and i + 1 < n and command[i + 1] == "(":
            if ch == "$" and i + 2 < n and command[i + 2] == "(":
                # $(( arithmetic )) is not a command.
                i += 3
                continue
            depth = 1
            j = i + 2
            while j < n and depth:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                j += 1
            inner = command[i + 2 : j - 1] if depth == 0 else command[i + 2 :]
            found.append((i, inner))
            if nested:
                found.extend(located_substitutions(inner, nested=True))
            i = j
            continue
        if ch == "`":
            j = command.find("`", i + 1)
            inner = command[i + 1 :] if j == -1 else command[i + 1 : j]
            found.append((i, inner))
            i = n if j == -1 else j + 1
            continue
        i += 1
    return found


def tokenize(command: str) -> list[str]:
    text = command.replace("\\\r\n", "").replace("\\\n", "")
    text = text.replace("\r\n", "\n").replace("\n", " ; ")
    # Backticks become parentheses so their contents split as their own command.
    text = text.replace("`", " ( ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as exc:
        raise ShellParseError(f"could not parse command: {exc}") from exc


def split_commands(command: str) -> list[SimpleCommand]:
    """Split ``command`` into simple commands with redirections pulled out."""
    tokens = tokenize(command)
    result: list[SimpleCommand] = []
    current = SimpleCommand(argv=[])

    def flush(separator: str) -> None:
        nonlocal current
        if current.argv or current.redirects or current.assignments:
            current.separator = separator
            result.append(current)
        current = SimpleCommand(argv=[])

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_redirect(token):
            op = token
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            # A file descriptor number glued before the operator ("2>").
            if current.argv and current.argv[-1].isdigit() and len(current.argv) > 1:
                current.argv.pop()
            if target and _is_operator(target):
                target = ""
            else:
                i += 1
            current.redirects.append(Redirect(op, target))
            i += 1
            continue
        if _is_operator(token):
            flush(token)
            i += 1
            continue
        if not current.argv and _ASSIGNMENT.match(token):
            name, _, value = token.partition("=")
            current.assignments[name] = value
            i += 1
            continue
        current.argv.append(token)
        i += 1
    flush("")
    return result
