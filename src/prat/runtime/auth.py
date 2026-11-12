"""Authentication for the Claude runtime.

Two modes (see :class:`prat.config.AuthMode`):

``api_key`` (default)
    The key comes from the ``ANTHROPIC_API_KEY`` environment variable or the
    ``anthropic_api_key`` setting, and is handed to the Claude Code process
    through its environment. This is the mode Anthropic's terms expect for
    products built on the Agent SDK, and the only one suitable for more than
    one person.

``claude_login`` (opt in, personal use)
    The unmodified Claude Code binary authenticates with whatever login its
    owner set up through Anthropic's own flow. PRAT never opens, copies,
    stores or forwards the credential files or tokens; it only asks the
    binary itself (``claude auth status --json``) whether it is signed in,
    and keeps nothing from the answer except "signed in or not" and the kind
    of login. API key variables are blanked for the child process so the
    login, not a stray key, is what gets used.

Nothing in this module returns or logs a secret.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from prat.config import AuthMode, Settings
from prat.proc import run_tree

API_KEY_ENV = "ANTHROPIC_API_KEY"
# Variables that would make Claude Code use something other than the login.
_KEY_VARIABLES = (API_KEY_ENV, "ANTHROPIC_AUTH_TOKEN")

LEGAL_URL = "https://code.claude.com/docs/en/legal-and-compliance"


class AuthError(RuntimeError):
    """The configured authentication mode cannot be used."""


@dataclass(frozen=True)
class AuthStatus:
    mode: AuthMode
    usable: bool
    detail: str
    source: str | None = None
    login_kind: str | None = None

    def require(self) -> None:
        if not self.usable:
            raise AuthError(self.detail)


def find_claude_cli(settings: Settings) -> Path | None:
    """The Claude Code executable the SDK will run (bundled copy first)."""
    if settings.claude_cli_path is not None:
        path = settings.claude_cli_path.expanduser()
        return path if path.is_file() else None
    try:
        import claude_agent_sdk
    except ImportError:  # pragma: no cover - it is a dependency
        claude_agent_sdk = None  # type: ignore[assignment]
    if claude_agent_sdk is not None:
        bundled_dir = Path(claude_agent_sdk.__file__).parent / "_bundled"
        for name in ("claude.exe", "claude"):
            if (bundled_dir / name).is_file():
                return bundled_dir / name
    found = shutil.which("claude")
    return Path(found) if found else None


def _api_key(settings: Settings) -> tuple[str | None, str | None]:
    """Return ``(key, where it came from)``. Callers must never display the key."""
    if os.environ.get(API_KEY_ENV, "").strip():
        return os.environ[API_KEY_ENV].strip(), f"environment variable {API_KEY_ENV}"
    if settings.anthropic_api_key is not None:
        value = settings.anthropic_api_key.get_secret_value().strip()
        if value:
            return value, "PRAT setting anthropic_api_key"
    return None, None


def check_auth(settings: Settings, *, probe_timeout: float = 30.0) -> AuthStatus:
    """Explain whether the configured mode can be used, without revealing secrets."""
    if settings.auth_mode is AuthMode.API_KEY:
        key, source = _api_key(settings)
        if key is None:
            return AuthStatus(
                AuthMode.API_KEY,
                False,
                f"auth_mode is api_key but no key is set: export {API_KEY_ENV} or set "
                'anthropic_api_key in the PRAT config. (auth_mode = "claude_login" is '
                "available for personal use on your own plan; see the README.)",
            )
        return AuthStatus(AuthMode.API_KEY, True, f"API key found in {source}", source=source)
    return _check_login(settings, probe_timeout)


def _check_login(settings: Settings, timeout: float) -> AuthStatus:
    cli = find_claude_cli(settings)
    if cli is None:
        return AuthStatus(
            AuthMode.CLAUDE_LOGIN,
            False,
            "auth_mode is claude_login but no Claude Code executable was found "
            "(install claude-agent-sdk, or set claude_cli_path)",
        )
    env = {**os.environ, **dict.fromkeys(_KEY_VARIABLES, "")}
    env.pop("CLAUDECODE", None)
    try:
        proc = run_tree([str(cli), "auth", "status", "--json"], env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AuthStatus(
            AuthMode.CLAUDE_LOGIN,
            False,
            f"could not ask Claude Code for its login status: {type(exc).__name__}",
        )
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return AuthStatus(
            AuthMode.CLAUDE_LOGIN,
            False,
            f"Claude Code did not report a login status (exit {proc.returncode}); "
            "run `claude /login` yourself, then try again",
        )
    # Only these two fields are read. Identity details in the answer are ignored.
    logged_in = data.get("loggedIn") is True
    kind = data.get("authMethod")
    kind = kind if isinstance(kind, str) else None
    if not logged_in:
        return AuthStatus(
            AuthMode.CLAUDE_LOGIN,
            False,
            "Claude Code is not signed in. Sign in yourself with `claude /login` "
            "(PRAT never handles the login).",
            login_kind=kind,
        )
    return AuthStatus(
        AuthMode.CLAUDE_LOGIN,
        True,
        f"Claude Code is signed in ({kind or 'unknown method'}); runs use your own "
        f"login and plan limits. Personal use only, see {LEGAL_URL}",
        source=str(cli),
        login_kind=kind,
    )


def runtime_env(settings: Settings) -> dict[str, str]:
    """Environment overrides for the Claude Code process in the configured mode."""
    if settings.auth_mode is AuthMode.API_KEY:
        key, _ = _api_key(settings)
        if key is None:
            raise AuthError(check_auth(settings).detail)
        return {API_KEY_ENV: key}
    # claude_login: blank any key so the binary uses its owner's login.
    return dict.fromkeys(_KEY_VARIABLES, "")
