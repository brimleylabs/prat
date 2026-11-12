"""Pure helpers of the web UI and its CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from typer.testing import CliRunner

from prat.cli import app
from prat.git import DiffFile, DiffStats
from prat.journal import Journal, JournalEntry, JournalTail
from prat.web import views
from prat.web.tokens import read_access_token, verify_login_code

DIFF = """diff --git a/hello.py b/hello.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/hello.py
@@ -0,0 +1,2 @@
+def greet():
+    return 'hi'
diff --git a/README.md b/README.md
index 2222222..3333333 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
 # acme
-old line
+new line
\\ No newline at end of file
"""


def test_build_diff_parses_files_and_line_numbers() -> None:
    stats = DiffStats(
        base="a" * 40,
        head="b" * 40,
        files=[DiffFile("hello.py", "A", 2, 0), DiffFile("README.md", "M", 1, 1)],
        commits=[],
    )
    files = views.build_diff(stats, DIFF)
    assert [f.path for f in files] == ["hello.py", "README.md"]
    hello, readme = files
    assert hello.status_label == "added"
    assert [(line.kind, line.new) for line in hello.lines] == [
        ("hunk", None),
        ("add", 1),
        ("add", 2),
    ]
    kinds = [line.kind for line in readme.lines]
    assert kinds == ["hunk", "ctx", "del", "add", "note"]
    deleted = next(line for line in readme.lines if line.kind == "del")
    assert (deleted.old, deleted.text) == (2, "old line")
    assert hello.size_bytes > 0
    limited = views.build_diff(stats, DIFF, max_lines_per_file=1)
    assert limited[0].hidden_lines == 2


def test_criteria_items() -> None:
    text = "- first\n  continued\n* second\n1. third\n\n[x] fourth"
    assert views.criteria_items(text) == ["first continued", "second", "third", "fourth"]
    assert views.criteria_items("plain one\nplain two") == ["plain one", "plain two"]
    assert views.criteria_items("") == []


def test_redact_masks_credentials() -> None:
    secret = "ghp_" + "a" * 36
    assert secret not in views.redact(f"token={secret} ok")
    assert "[redacted]" in views.redact(f"token={secret} ok")
    assert views.redact("nothing here") == "nothing here"


def test_formatting() -> None:
    assert views.fmt_usd(0) == "$0.00"
    assert views.fmt_usd(0.004) == "$0.0040"
    assert views.fmt_usd(1234.5) == "$1,234.50"
    assert views.fmt_duration(0.25) == "250 ms"
    assert views.fmt_duration(75) == "1m 15s"
    assert views.fmt_duration(3720) == "1h 02m"
    assert views.fmt_bytes(2048) == "2.0 KB"
    now = datetime.now(UTC)
    assert views.fmt_ago(now - timedelta(minutes=5), now) == "5 min ago"


def _entry(seq: int, kind: str, **data: object) -> JournalEntry:
    return JournalEntry(
        seq, datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seq), kind, dict(data)
    )


def test_present_journal_entries() -> None:
    entries = [
        _entry(1, "run.started"),
        _entry(2, "runtime.tool_call", call_id="c1", tool="bash", input={"command": "git push"}),
        _entry(
            3,
            "policy",
            agent="builder",
            call={"kind": "bash", "command": "git push"},
            allowed=False,
            reason="git push is not allowed",
            loop=False,
        ),
        _entry(4, "runtime.tool_result", call_id="c1", ok=False, denied=True, reason="no"),
        _entry(5, "critic.text", text="looks fine"),
        _entry(6, "verify.gate", name="test", passed=True, command="pytest", duration_seconds=1.5),
    ]
    views_ = views.present_all(entries)
    assert views_[1].category == "tool" and views_[1].code == "git push"
    assert views_[2].category == "denied" and views_[2].tone == "denied"
    assert "not allowed" in views_[2].detail
    assert views_[4].agent == "critic"
    assert views_[5].tone == "ok"
    assert views_[3].offset_label == "+00:03.0"
    histogram = views.tool_histogram(entries)
    assert histogram[0].tool == "bash" and histogram[0].denied == 1
    assert views.category_counts(views_)["denied"] == 1


def test_builder_model_entry_names_the_repository_instruction_files() -> None:
    plain = views.present(_entry(1, "execute.model", model="m", tools=["read", "write"]))
    assert plain.detail == "tools: read, write"
    older = views.present(_entry(1, "execute.model", model="m", tools=["read"]))
    assert "repository instructions" not in older.detail
    guided = views.present(
        _entry(1, "execute.model", model="m", tools=["read"], repo_instructions=["AGENTS.md"])
    )
    assert guided.detail == "tools: read; repository instructions: AGENTS.md"


def test_journal_tail_reads_only_new_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    tail = JournalTail(path)
    assert tail.read_new() == []
    journal = Journal(path)
    journal.append("one", {})
    assert [e.kind for e in tail.read_new()] == ["one"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts": "2026-01-01T00:00:00+00:00", "kind": "half"')
    assert tail.read_new() == []
    with path.open("a", encoding="utf-8") as handle:
        handle.write(', "data": {}}\n')
    assert [e.kind for e in tail.read_new()] == ["half"]


def test_cli_token_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    home = tmp_path / "home"

    def run(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args])
        assert result.exit_code == code, result.output
        return result.output

    out = run("init")
    assert "access token" in out
    from prat.config import load_settings

    settings = load_settings(home)
    token = read_access_token(settings)
    assert token and token not in out

    url = run("login-url", "--next", "/cards/2").strip()
    parts = urlsplit(url)
    assert parts.netloc == "127.0.0.1:18300"
    query = parse_qs(parts.query)
    assert query["next"] == ["/cards/2"]
    assert verify_login_code(token, query["code"][0], set())

    run("login-url", "--next", "https://evil.example", code=1)
    run("rotate-token")
    assert read_access_token(settings) != token
    assert "refusing to listen" in run("serve", "--host", "0.0.0.0", code=2)
