"""The web UI access token and one-time login codes.

``prat init`` writes a random access token to ``$PRAT_HOME/access-token``,
readable only by its owner where the operating system supports POSIX file
modes. Anyone who can read that file can sign in, which is the same trust
boundary as the database and journals next to it.

A login code is a short-lived, single-use proof derived from the token::

    <expires>.<nonce>.<hmac-sha256(token, "login:<expires>.<nonce>")>

``prat serve`` and ``prat login-url`` print a URL carrying one, so a person
can sign in with a click instead of pasting the token. The server records
used nonces in its database (hashed, see :mod:`prat.web.sessions`), so a code
works once, also across a restart. Rotating the token invalidates every
outstanding code and every session.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import secrets
import stat
import time
from collections.abc import MutableSet
from pathlib import Path
from urllib.parse import quote

from prat.config import Settings

TOKEN_BYTES = 32
LOGIN_CODE_TTL_SECONDS = 600


class TokenError(RuntimeError):
    pass


def _write_owner_only(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        # os.open honours the mode on POSIX (minus the umask); make it explicit.
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_access_token(settings: Settings) -> str | None:
    try:
        token = settings.access_token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def ensure_access_token(settings: Settings) -> str:
    """Return the access token, creating it (owner-only) if it does not exist."""
    token = read_access_token(settings)
    if token is not None:
        return token
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_owner_only(settings.access_token_file, token + "\n")
    return token


def rotate_access_token(settings: Settings) -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_owner_only(settings.access_token_file, token + "\n")
    return token


def token_permissions_ok(path: Path) -> bool | None:
    """``True`` if only the owner can read the file, ``None`` where modes do not apply."""
    if os.name != "posix":
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return not mode & (stat.S_IRWXG | stat.S_IRWXO)


def fingerprint(token: str) -> str:
    """A short, non-reversible identifier of a token (used to expire sessions)."""
    return hashlib.sha256(("prat-session:" + token).encode()).hexdigest()[:24]


def tokens_equal(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def _sign(token: str, message: str) -> str:
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def make_login_code(
    token: str, *, ttl: float = LOGIN_CODE_TTL_SECONDS, now: float | None = None
) -> str:
    expires = int((now if now is not None else time.time()) + ttl)
    nonce = secrets.token_urlsafe(12)
    body = f"{expires}.{nonce}"
    return f"{body}.{_sign(token, 'login:' + body)}"


def check_login_code(token: str, code: str, *, now: float | None = None) -> tuple[str, int] | None:
    """Check a login code's shape, signature and expiry, without using it up.

    Returns the code's nonce and expiry time, or ``None`` for any failure. The
    caller must still make sure the nonce is used only once.
    """
    parts = code.split(".")
    if len(parts) != 3:
        return None
    expires_text, nonce, signature = parts
    if not expires_text.isascii() or not expires_text.isdigit() or not nonce:
        return None
    body = f"{expires_text}.{nonce}"
    if not hmac.compare_digest(signature.encode(), _sign(token, "login:" + body).encode()):
        return None
    expires = int(expires_text)
    if expires < (now if now is not None else time.time()):
        return None
    return nonce, expires


def verify_login_code(
    token: str, code: str, used: MutableSet[str], *, now: float | None = None
) -> bool:
    """Check a login code and mark it used in ``used``. Every failure returns ``False``.

    This is the in-memory form. The server claims the nonce in its database
    instead (:meth:`prat.web.sessions.SessionStore.claim_login_nonce`).
    """
    checked = check_login_code(token, code, now=now)
    if checked is None or checked[0] in used:
        return False
    used.add(checked[0])
    return True


def origin_for(settings: Settings, host: str | None = None, port: int | None = None) -> str:
    if settings.web_public_url:
        return settings.web_public_url.rstrip("/")
    host = host or settings.web_host
    port = port or settings.web_port
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


def login_url(
    settings: Settings,
    *,
    next_path: str = "/",
    host: str | None = None,
    port: int | None = None,
) -> str:
    """A one-time login URL for the configured (or given) address."""
    token = ensure_access_token(settings)
    code = make_login_code(token)
    url = f"{origin_for(settings, host, port)}/login?code={quote(code, safe='')}"
    if next_path and next_path != "/":
        url += f"&next={quote(next_path, safe='/')}"
    return url
