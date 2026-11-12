"""Request checks and response headers for the web UI.

One ASGI middleware, in front of every route, enforces:

* **Host**: the ``Host`` header must name this server (defeats DNS rebinding).
* **Authentication**: every path except ``/login`` and ``/static/`` needs a
  valid session. Pages redirect to the login page; streams and htmx requests
  get ``401``.
* **State-changing requests** (anything but GET, HEAD, OPTIONS): the ``Origin``
  header (or, without one, the ``Referer``) must be this server; the body must
  be empty or ``application/x-www-form-urlencoded`` (so a cross-site
  ``text/plain`` form cannot pass as one); and the session's CSRF token must
  arrive in the ``X-CSRF-Token`` header or a ``csrf_token`` form field.
  ``POST /login`` has no session yet, so it gets the Origin and body checks.
* **Response headers**: a Content Security Policy without inline scripts or
  styles, ``frame-ancestors 'none'``, ``nosniff``, a ``same-origin`` referrer policy (no
  referrer reaches another site; PRAT's own pages keep their Origin, which ``no-referrer``
  would turn into ``null`` on every form post), and
  ``no-store`` caching for everything but static files.

The checks fail closed: a request that cannot be judged is refused.
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prat.web.sessions import COOKIE_NAME, SessionStore

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_FORM_BYTES = 1024 * 1024
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]
)

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CONTENT_SECURITY_POLICY),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "same-origin"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
)

LOOPBACK_ALIASES = ("127.0.0.1", "localhost", "[::1]")
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})  # noqa: S104 - recognised, not bound


class BindError(ValueError):
    """The requested listen address is refused."""


def is_loopback(host: str) -> bool:
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def check_bind(host: str, *, allow_remote: bool, public_url: str | None) -> str | None:
    """Refuse a non-loopback address unless explicitly allowed.

    Returns a warning to print when a non-loopback address is allowed.
    """
    if is_loopback(host):
        return None
    if not allow_remote:
        raise BindError(
            f"refusing to listen on {host!r}: it is not a loopback address. The web UI can "
            "start agent runs, so it listens on 127.0.0.1 unless you set "
            "web_allow_remote = true (or pass --allow-remote) and put it behind TLS."
        )
    if host.strip() in WILDCARD_HOSTS and not public_url:
        raise BindError(
            f"listening on {host!r} needs web_public_url (the address you open in the "
            "browser), so Host and Origin checks know what to accept"
        )
    return (
        f"WARNING: the web UI is listening on {host}, which is reachable from other "
        "machines. Anyone who obtains the access token or a session cookie can start "
        "agent runs. Use TLS in front of it and keep the token private."
    )


@dataclass(frozen=True)
class WebSecurity:
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    secure_cookies: bool = False


def security_config(host: str, port: int, public_url: str | None = None) -> WebSecurity:
    hosts: set[str] = set()
    origins: set[str] = set()
    if is_loopback(host):
        names: tuple[str, ...] = LOOPBACK_ALIASES
    elif host.strip() in WILDCARD_HOSTS:
        names = ()
    else:
        names = (f"[{host}]" if ":" in host else host,)
    for name in names:
        hosts.add(f"{name}:{port}".lower())
        origins.add(f"http://{name}:{port}".lower())
    secure = False
    if public_url:
        parts = urlsplit(public_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise BindError(f"web_public_url {public_url!r} must be an http(s) URL")
        hosts.add(parts.netloc.lower())
        origins.add(f"{parts.scheme}://{parts.netloc}".lower())
        secure = parts.scheme == "https"
    return WebSecurity(frozenset(hosts), frozenset(origins), secure)


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def same_origin(headers: Headers, allowed: frozenset[str]) -> bool:
    origin = headers.get("origin")
    if origin is not None and origin.strip().lower() != "null":
        return origin.strip().lower() in allowed
    # A browser that hides the origin still says where a request came from in Sec-Fetch-Site,
    # which no page can set. Only "same-origin" counts.
    if headers.get("sec-fetch-site", "").strip().lower() == "same-origin":
        return True
    if origin is not None:
        return False
    referer = headers.get("referer")
    if referer:
        return _origin_of(referer) in allowed
    return False


PUBLIC_PATHS = frozenset({"/login", "/favicon.ico", "/healthz"})


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/static/")


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: WebSecurity,
        sessions: SessionStore,
        token_fingerprint: Callable[[], str | None],
    ) -> None:
        self.app = app
        self.config = config
        self.sessions = sessions
        self.token_fingerprint = token_fingerprint

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        send = self._with_headers(send, cache=not path.startswith("/static/"))
        headers = Headers(scope=scope)
        if headers.get("host", "").lower() not in self.config.allowed_hosts:
            await _plain(400, "unknown host")(scope, receive, send)
            return

        cookies = cookie_parser(headers.get("cookie", ""))
        session = self.sessions.get(cookies.get(COOKIE_NAME), self.token_fingerprint())
        scope.setdefault("state", {})["session"] = session
        method = scope["method"].upper()

        if method in SAFE_METHODS:
            if session is None and not is_public(path):
                await self._unauthenticated(scope, headers)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if not same_origin(headers, self.config.allowed_origins):
            await _plain(403, "cross-site request refused")(scope, receive, send)
            return
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type != "application/x-www-form-urlencoded":
            await _plain(415, "unsupported content type")(scope, receive, send)
            return
        try:
            body = await _read_body(receive, MAX_FORM_BYTES)
        except _TooLarge:
            await _plain(413, "request too large")(scope, receive, send)
            return
        if body and not content_type:
            await _plain(415, "unsupported content type")(scope, receive, send)
            return
        if path != "/login":
            if session is None:
                await _plain(403, "sign in first")(scope, receive, send)
                return
            supplied = headers.get(CSRF_HEADER) or _form_value(body, CSRF_FIELD)
            if not supplied or not hmac.compare_digest(
                supplied.encode(), session.csrf_token.encode()
            ):
                await _plain(403, "missing or invalid CSRF token")(scope, receive, send)
                return
        await self.app(scope, _replay(body, receive), send)

    def _unauthenticated(self, scope: Scope, headers: Headers) -> Response:
        path: str = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        target = path + (f"?{query}" if query else "")
        login = "/login" if path in ("/", "/board") else f"/login?next={quote(target, safe='/')}"
        if path.startswith("/events/"):
            return _plain(401, "sign in first")
        if headers.get("hx-request"):
            response = _plain(401, "sign in first")
            response.headers["HX-Redirect"] = login
            return response
        return Response(status_code=303, headers={"location": login})

    def _with_headers(self, send: Send, *, cache: bool) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    mutable[name] = value
                if cache:
                    mutable["cache-control"] = "no-store"
            await send(message)

        return wrapped


class _TooLarge(Exception):
    pass


async def _read_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise _TooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _form_value(body: bytes, name: str) -> str | None:
    if not body:
        return None
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return None
    found = values.get(name)
    return found[0] if found else None


def _plain(status: int, text: str) -> Response:
    return PlainTextResponse(text, status_code=status)
