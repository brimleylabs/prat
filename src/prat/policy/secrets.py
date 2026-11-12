"""Detection of credential-shaped literals.

Used to deny agent file writes and commands that contain secrets, and to scan
the diff of a run before it can reach review. The patterns aim at formats
with a distinctive prefix so that false positives stay rare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    line: int
    preview: str


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern(
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY( BLOCK)?-----"
        ),
    ),
    SecretPattern("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    SecretPattern("github_fine_grained_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    SecretPattern("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    SecretPattern("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    SecretPattern("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    SecretPattern("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
)


def _mask(text: str) -> str:
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]


def find_secrets(text: str) -> list[SecretMatch]:
    """Return every secret-like literal in ``text`` (values are masked)."""
    matches: list[SecretMatch] = []
    for pattern in SECRET_PATTERNS:
        for found in pattern.regex.finditer(text):
            line = text.count("\n", 0, found.start()) + 1
            matches.append(SecretMatch(pattern.name, line, _mask(found.group(0))))
    matches.sort(key=lambda m: m.line)
    return matches


REDACTED = "[redacted]"
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.S,
)


def redact(text: str) -> str:
    """Replace every secret-like literal in ``text`` with a fixed marker.

    A private key is removed from its header to its footer (or to the end of
    the text when the footer is missing), so no key material survives.
    """
    text = _PRIVATE_KEY_BLOCK.sub(f"{REDACTED}:private_key", text)
    for pattern in SECRET_PATTERNS:
        text = pattern.regex.sub(f"{REDACTED}:{pattern.name}", text)
    return text


def scan_diff_additions(diff: str) -> list[tuple[str, SecretMatch]]:
    """Scan only the added lines of a unified diff. Returns ``(file, match)`` pairs."""
    results: list[tuple[str, SecretMatch]] = []
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith("+") and not line.startswith("+++"):
            for match in find_secrets(line[1:]):
                results.append((current, match))
    return results
