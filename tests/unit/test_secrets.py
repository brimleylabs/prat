from __future__ import annotations

import pytest

from prat.policy.secrets import find_secrets, scan_diff_additions

# Every sample is assembled at runtime; none is a real credential.
SAMPLES = {
    "aws_access_key_id": "AKIA" + "Z" * 16,
    "github_token": "gh" + "p_" + "x" * 36,
    "slack_token": "xo" + "xb-" + "1234567890-abcdef",
    "anthropic_api_key": "sk-" + "ant-" + "a" * 30,
    "private_key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "google_api_key": "AI" + "za" + "b" * 35,
    "stripe_live_key": "sk" + "_live_" + "c" * 24,
}


@pytest.mark.parametrize(("kind", "sample"), list(SAMPLES.items()))
def test_detects_known_formats(kind: str, sample: str) -> None:
    found = find_secrets(f"value = '{sample}'")
    assert kind in {m.kind for m in found}
    assert all(sample not in m.preview for m in found)


@pytest.mark.parametrize(
    "text",
    [
        "api_key = os.environ['API_KEY']",
        "token = get_token()",
        "AKIA is a prefix",
        "sk-short",
        "the password is in the vault",
    ],
)
def test_ignores_ordinary_code(text: str) -> None:
    assert find_secrets(text) == []


def test_line_numbers() -> None:
    text = "a\nb\nkey=" + SAMPLES["aws_access_key_id"] + "\n"
    [match] = find_secrets(text)
    assert match.line == 3


def test_scan_diff_only_checks_added_lines() -> None:
    key = SAMPLES["aws_access_key_id"]
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        f"-old = '{key}'\n"
        "+new = os.environ['KEY']\n"
        "diff --git a/y.py b/y.py\n"
        "+++ b/y.py\n"
        f"+leak = '{key}'\n"
    )
    results = scan_diff_additions(diff)
    assert [(file, m.kind) for file, m in results] == [("y.py", "aws_access_key_id")]
