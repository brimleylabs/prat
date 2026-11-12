"""Authentication, CSRF, Origin and Host checks, headers, escaping and secrets."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.routing import Route

from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.runtime.auth import check_auth
from prat.web import BindError, check_bind
from prat.web.routes_pages import _database_label, safe_next
from prat.web.security import CONTENT_SECURITY_POLICY, security_config
from prat.web.tokens import (
    ensure_access_token,
    make_login_code,
    rotate_access_token,
    token_permissions_ok,
    verify_login_code,
)
from web_support import EVIL, ORIGIN, make_web

pytestmark = pytest.mark.integration

PAGES = ["/board", "/cards/new", "/projects", "/projects/new", "/settings", "/board/columns"]


# Authentication -------------------------------------------------------------------------


def test_pages_need_a_session(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project, login=False) as web:
        for path in [*PAGES, "/cards/1", "/projects/acme-api"]:
            response = web.client.get(path, follow_redirects=False)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login"), path
        response = web.client.get("/cards/1", follow_redirects=False)
        assert response.headers["location"] == "/login?next=/cards/1"
        assert web.client.get("/events/board").status_code == 401
        assert web.client.get("/events/cards/1").status_code == 401
        htmx = web.client.get("/board/columns", headers={"HX-Request": "true"})
        assert htmx.status_code == 401
        assert htmx.headers["HX-Redirect"].startswith("/login")
        # Login and static files are public.
        assert web.client.get("/login").status_code == 200
        assert web.client.get("/static/app.css").status_code == 200
        assert web.client.get("/static/vendor/htmx-2.0.4.min.js").status_code == 200


def test_token_login_sets_a_strict_httponly_cookie(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project, login=False) as web:
        token = ensure_access_token(settings)
        response = web.client.post(
            "/login",
            data={"token": token, "next": "/projects"},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/projects"
        cookie = response.headers["set-cookie"].lower()
        assert "prat_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie
        assert token not in response.headers["set-cookie"]
        assert web.client.get("/board").status_code == 200


def test_wrong_tokens_are_refused_then_throttled(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project, login=False) as web:
        for _ in range(10):
            response = web.client.post(
                "/login", data={"token": "nope"}, headers={"Origin": ORIGIN}, follow_redirects=False
            )
            assert response.status_code == 401
            assert "prat_session" not in response.headers.get("set-cookie", "")
        token = ensure_access_token(settings)
        blocked = web.client.post(
            "/login", data={"token": token}, headers={"Origin": ORIGIN}, follow_redirects=False
        )
        assert blocked.status_code == 429


def test_login_post_needs_same_origin(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project, login=False) as web:
        token = ensure_access_token(settings)
        refused: list[dict[str, str]] = [
            {"Origin": EVIL},
            {},
            {"Referer": EVIL + "/x"},
            {"Origin": "null"},
            {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ]
        for headers in refused:
            response = web.client.post(
                "/login", data={"token": token}, headers=headers, follow_redirects=False
            )
            assert response.status_code == 403, headers
        assert web.client.get("/board", follow_redirects=False).status_code == 303
        # A real browser posting from prat's own page: no usable Origin, but it says same-origin.
        accepted = web.client.post(
            "/login",
            data={"token": token},
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert accepted.status_code == 303


def test_one_time_login_link(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project, login=False) as web:
        token = ensure_access_token(settings)
        code = make_login_code(token)
        first = web.client.get(f"/login?code={code}&next=/settings", follow_redirects=False)
        assert first.status_code == 303
        assert first.headers["location"] == "/settings"
        assert web.client.get("/settings").status_code == 200
        # Reopening the used link in a signed-in browser just goes to the page.
        reopened = web.client.get(f"/login?code={code}&next=/usage", follow_redirects=False)
        assert reopened.status_code == 303
        assert reopened.headers["location"] == "/usage"
        web.client.cookies.clear()
        again = web.client.get(f"/login?code={code}", follow_redirects=False)
        assert again.status_code == 401
        assert "expired or was already used" in again.text
        expired = make_login_code(token, now=time.time() - 3600)
        assert web.client.get(f"/login?code={expired}").status_code == 401
        forged = make_login_code("another-token")
        assert web.client.get(f"/login?code={forged}").status_code == 401
        assert web.client.get("/board", follow_redirects=False).status_code == 303


def test_login_codes() -> None:
    used: set[str] = set()
    code = make_login_code("t0ken")
    assert not verify_login_code("other", code, used)
    assert verify_login_code("t0ken", code, used)
    assert not verify_login_code("t0ken", code, used)
    expires, nonce, sig = make_login_code("t0ken").split(".")
    assert not verify_login_code("t0ken", f"{int(expires) + 999}.{nonce}.{sig}", used)
    assert not verify_login_code("t0ken", "garbage", used)


def test_safe_next() -> None:
    assert safe_next("/cards/3?run=2") == "/cards/3?run=2"
    for bad in ("https://evil.example", "//evil.example", "/\\evil", "evil", None, "/x\r\ny"):
        assert safe_next(bad) == "/board"


def test_logout_and_token_rotation_end_sessions(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        cookie = web.client.cookies.get("prat_session")
        assert web.post("/logout").status_code == 303
        web.client.cookies.set("prat_session", cookie or "")
        assert web.client.get("/board", follow_redirects=False).status_code == 303

        web.login()
        assert web.client.get("/board").status_code == 200
        time.sleep(0.02)
        rotate_access_token(settings)
        assert web.client.get("/board", follow_redirects=False).status_code == 303


def test_access_token_file_is_private(settings: Settings) -> None:
    ensure_access_token(settings)
    assert token_permissions_ok(settings.access_token_file) in (True, None)
    first = settings.access_token_file.read_text(encoding="utf-8")
    assert ensure_access_token(settings) == first.strip()
    assert rotate_access_token(settings) != first.strip()


# CSRF and Origin -------------------------------------------------------------------------


def test_cross_site_posts_are_rejected_and_create_nothing(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        form = {"project": "acme-api", "title": "Injected", "start": "1"}
        # A page on another site: its Origin, even if it somehow had the token.
        assert web.post("/cards", form, origin=EVIL).status_code == 403
        # A classic cross-site text/plain form post.
        plain = web.client.post(
            "/cards",
            content=b"project=acme-api&title=Injected&start=1",
            headers={"Origin": EVIL, "Content-Type": "text/plain"},
            follow_redirects=False,
        )
        assert plain.status_code == 403
        # Same origin but text/plain is still not a form.
        same_plain = web.client.post(
            "/cards",
            content=f"project=acme-api&title=Injected&csrf_token={web.csrf}".encode(),
            headers={"Origin": ORIGIN, "Content-Type": "text/plain"},
            follow_redirects=False,
        )
        assert same_plain.status_code == 415
        # Neither Origin nor Referer.
        assert web.post("/cards", form, origin=None).status_code == 403
        # Right origin, missing or wrong CSRF token.
        assert web.post("/cards", form, csrf=False).status_code == 403
        assert (
            web.post("/cards", form, csrf=False, headers={"X-CSRF-Token": "wrong"}).status_code
            == 403
        )
        # Without a session cookie at all.
        cookies = dict(web.client.cookies)
        web.client.cookies.clear()
        assert web.post("/cards", form).status_code == 403
        for key, value in cookies.items():
            web.client.cookies.set(key, value)
        assert web.card_count() == 0
        assert web.engine.scheduled_cards() == frozenset()

        # The real thing works: same-origin Referer and the token as a form field.
        accepted = web.client.post(
            "/cards",
            data={**form, "start": "", "csrf_token": web.csrf},
            headers={"Referer": ORIGIN + "/cards/new"},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert web.card_count() == 1


def _all_routes(routes: list[Any]) -> list[Any]:
    """Flatten included routers (newer FastAPI keeps them as wrapper objects)."""
    found: list[Any] = []
    for route in routes:
        nested = getattr(route, "original_router", None) or getattr(route, "router", None)
        if nested is not None and hasattr(nested, "routes"):
            found += _all_routes(list(nested.routes))
        else:
            found.append(route)
    return found


def test_every_state_changing_route_requires_csrf(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card()
        checked = 0
        for route in _all_routes(web.app.routes):
            if not isinstance(route, Route) or not (route.methods or set()) - {"GET", "HEAD"}:
                continue
            path = route.path.replace("{card_id}", str(card_id))
            if path == "/login":
                continue
            response = web.post(path, {"body": "x", "feedback": "x"}, csrf=False)
            assert response.status_code == 403, path
            checked += 1
        assert checked >= 10
        assert web.status(card_id) == CardStatus.DRAFT


def test_unknown_host_is_refused(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        response = web.client.get("/board", headers={"Host": "attacker.example:18300"})
        assert response.status_code == 400
        assert web.client.get("/board", headers={"Host": "localhost:18300"}).status_code == 200


def test_bind_rules() -> None:
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.2"):
        assert check_bind(host, allow_remote=False, public_url=None) is None
    for host in ("0.0.0.0", "192.168.1.20", "::"):
        with pytest.raises(BindError):
            check_bind(host, allow_remote=False, public_url=None)
    with pytest.raises(BindError, match="web_public_url"):
        check_bind("0.0.0.0", allow_remote=True, public_url=None)
    warning = check_bind("192.168.1.20", allow_remote=True, public_url=None)
    assert warning and "WARNING" in warning
    config = security_config("127.0.0.1", 18300)
    assert "http://localhost:18300" in config.allowed_origins
    assert "127.0.0.1:18300" in config.allowed_hosts
    proxied = security_config("0.0.0.0", 18300, "https://prat.example.test")
    assert proxied.allowed_origins == frozenset({"https://prat.example.test"})
    assert proxied.secure_cookies


# Headers and markup ------------------------------------------------------------------------

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
STYLE_ATTR = re.compile(r"\sstyle\s*=", re.IGNORECASE)
HANDLER_ATTR = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def test_security_headers_everywhere(settings: Settings, db: Database, project: Project) -> None:
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    with make_web(settings, db, project) as web:
        for path in ("/board", "/login", "/static/app.css", "/nope"):
            response = web.client.get(path, follow_redirects=False)
            headers = response.headers
            assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY, path
            assert "frame-ancestors 'none'" in headers["content-security-policy"]
            assert "script-src 'self'" in headers["content-security-policy"]
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert headers["referrer-policy"] == "same-origin"
        assert web.client.get("/board").headers["cache-control"] == "no-store"


def test_pages_have_no_inline_script_style_or_handlers(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True, acceptance_criteria="- greet returns hi")
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)
        paths = [*PAGES, f"/cards/{card_id}", f"/cards/{card_id}/panel", "/projects/acme-api"]
        paths.append(f"/cards/{card_id}/runs/1/replay")
        web.client.cookies.clear()
        paths.append("/login")
        web.login()
        for path in paths:
            html = web.client.get(path).text
            assert not INLINE_SCRIPT.search(html), path
            assert not STYLE_ATTR.search(html), path
            assert not HANDLER_ATTR.search(html), path


def test_templates_never_bypass_escaping() -> None:
    root = Path(__import__("prat.web", fromlist=["x"]).__file__).parent
    for template in (root / "templates").glob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert "|safe" not in text.replace(" ", ""), template.name
        assert "autoescape false" not in text, template.name
    for module in root.glob("*.py"):
        if module.name == "icons.py":
            continue
        assert "Markup(" not in module.read_text(encoding="utf-8"), module.name


# Escaping --------------------------------------------------------------------------------

TITLE = '<script>alert("title")</script>'
COMMENT = "<img src=x onerror=alert(1)>"
JOURNAL = "<script>alert('journal')</script>"


def test_untrusted_text_is_escaped_everywhere(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(title=TITLE, goal=TITLE, acceptance_criteria=f"- {TITLE}")
        web.scripts[card_id] = [
            {"op": "text", "text": JOURNAL},
            {"op": "write", "path": "evil.html", "content": JOURNAL + "\n"},
            {"op": "bash", "command": f"echo {COMMENT!r}"},
            {"op": "finish", "message": JOURNAL},
        ]
        assert web.post(f"/cards/{card_id}/comments", {"body": COMMENT}).status_code == 303
        stream = web.client.get(f"/events/cards/{card_id}")  # not running: ends at once
        assert "event: end" in stream.text
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)
        pages = {
            "board": web.client.get("/board").text,
            "columns": web.client.get("/board/columns").text,
            "card": web.client.get(f"/cards/{card_id}").text,
            "replay": web.client.get(f"/cards/{card_id}/runs/1/replay").text,
            "project": web.client.get("/projects/acme-api").text,
        }
        for name, html in pages.items():
            for raw in (TITLE, COMMENT, JOURNAL):
                assert raw not in html, (name, raw)
        assert "&lt;script&gt;alert(&#34;title&#34;)&lt;/script&gt;" in pages["board"]
        assert "&lt;img src=x onerror=alert(1)&gt;" in pages["card"]
        assert "&lt;script&gt;alert(&#39;journal&#39;)&lt;/script&gt;" in pages["replay"]
        # The diff view shows the file's script tag as text.
        assert "evil.html" in pages["card"]


def test_live_stream_escapes_journal_text(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.scripts[card_id] = [
            {"op": "text", "text": JOURNAL},
            {"op": "wait", "seconds": 0.3},
            {"op": "write", "path": "hello.py", "content": "x = 1\n"},
            {"op": "finish", "message": "done"},
        ]
        web.post(f"/cards/{card_id}/run")
        body = web.client.get(f"/events/cards/{card_id}").text
        assert "event: entry" in body
        assert "event: end" in body
        assert JOURNAL not in body
        assert "&lt;script&gt;alert(&#39;journal&#39;)&lt;/script&gt;" in body


# Secrets ------------------------------------------------------------------------------------

FAKE_KEY = "sk-ant-api03-" + "Q" * 48


def test_no_secret_reaches_any_page(
    settings: Settings, db: Database, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    with make_web(
        settings, db, project, auth_checker=check_auth, anthropic_api_key=FAKE_KEY
    ) as web:
        token = ensure_access_token(settings)
        settings_page = web.client.get("/settings")
        assert settings_page.status_code == 200
        assert "Usable" in settings_page.text
        card_id = web.add_card()
        for path in [*PAGES, f"/cards/{card_id}", "/projects/acme-api", "/login"]:
            text = web.client.get(path).text
            assert FAKE_KEY not in text, path
            assert FAKE_KEY[:20] not in text, path
            assert token not in text, path


def test_database_password_is_hidden() -> None:
    label = _database_label("postgresql+psycopg://PRAT:hunter2secret@db.internal/PRAT")
    assert "hunter2secret" not in label
    assert "db.internal" in label


def test_agent_output_secrets_are_masked(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.scripts[card_id] = [
            {"op": "text", "text": f"I found {FAKE_KEY} in the environment"},
            {"op": "write", "path": "hello.py", "content": "x = 1\n"},
            {"op": "finish", "message": "done"},
        ]
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)
        for path in (f"/cards/{card_id}", f"/cards/{card_id}/runs/1/replay"):
            text = web.client.get(path).text
            assert FAKE_KEY not in text
            assert "[redacted]" in text
