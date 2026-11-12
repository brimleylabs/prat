"""Invariant: every page and API route refuses a request with no session; every
state-changing request refuses a missing or wrong CSRF token and a foreign Origin."""

from __future__ import annotations

from pathlib import Path

from invariant_support import BASE, EVIL, concrete, routes, setup, web

# The only routes open without a session. Adding one is a change to this invariant,
# which self-improvement cannot make: it takes a commit by a person.
PUBLIC = {("GET", "/login"), ("POST", "/login"), ("GET", "/favicon.ico"), ("GET", "/healthz")}
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def test_inv_the_health_check_tells_nothing_but_liveness(tmp_path: Path) -> None:
    s = setup(tmp_path)
    with web(s, login=False) as (_app, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"status", "version", "commit"}
        assert str(tmp_path) not in response.text and "\\" not in response.text


def test_inv_every_route_needs_a_session(tmp_path: Path) -> None:
    s = setup(tmp_path)
    with web(s, login=False) as (app, client):
        served = [route for route in routes(app) if route not in PUBLIC]
        assert len(served) >= 20, served
        for method, path in served:
            url = concrete(path)
            if method in STATE_CHANGING:
                response = client.request(
                    method, url, data={}, headers={"Origin": BASE}, follow_redirects=False
                )
                assert response.status_code in (401, 403), (method, path, response.status_code)
            else:
                response = client.request(method, url, follow_redirects=False)
                assert response.status_code in (303, 401), (method, path, response.status_code)
                if response.status_code == 303:
                    assert response.headers["location"].startswith("/login"), path


def test_inv_state_changing_requests_need_csrf_and_same_origin(tmp_path: Path) -> None:
    s = setup(tmp_path)
    with web(s) as (app, client):
        changing = [
            (method, path)
            for method, path in routes(app)
            if method in STATE_CHANGING and (method, path) not in PUBLIC
        ]
        assert changing
        for method, path in changing:
            url = concrete(path)
            cases = [
                {"Origin": BASE},
                {"Origin": BASE, "X-CSRF-Token": "wrong"},
                {"Origin": EVIL, "X-CSRF-Token": client.csrf},
                {"X-CSRF-Token": client.csrf},
            ]
            for headers in cases:
                response = client.request(
                    method, url, data={}, headers=headers, follow_redirects=False
                )
                assert response.status_code == 403, (method, path, headers, response.status_code)
        # The login form itself refuses a foreign Origin.
        refused = client.post("/login", data={"token": "x"}, headers={"Origin": EVIL})
        assert refused.status_code == 403
