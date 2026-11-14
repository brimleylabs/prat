"""A health URL answers only with a page that is not an uncaught error."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from prat.setup_agent import _answers

PAGES = {
    "/ok": (200, b"<html><body>Sign in</body></html>"),
    "/redirect": (302, b""),
    "/missing": (404, b"not found"),
    "/broken": (500, b"oops"),
    "/php": (200, b"<br />\n<b>Fatal error</b>:  Uncaught Error: Failed opening required"),
    "/python": (200, b"Traceback (most recent call last):\n  File ..."),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = PAGES[self.path]
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_a_working_page_or_a_client_error_answers(base: str) -> None:
    assert _answers(f"{base}/ok")
    assert _answers(f"{base}/redirect")
    assert _answers(f"{base}/missing")


def test_a_server_error_or_an_error_page_served_as_success_does_not(base: str) -> None:
    assert not _answers(f"{base}/broken")
    assert not _answers(f"{base}/php")
    assert not _answers(f"{base}/python")
