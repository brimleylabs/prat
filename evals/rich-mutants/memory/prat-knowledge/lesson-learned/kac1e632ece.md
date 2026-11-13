+++
id = "kac1e632ece"
kind = "command"
summary = "Upstream workflow uses uv via the Makefile: make test, make format-check, make typecheck, make docs"
paths = ["Makefile", "tox.ini", "uv.lock", "CONTRIBUTING.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:25+00:00"
last_verified = "2026-11-13T05:41:25+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The Makefile runs `TERM=unknown uv run pytest --cov=rich tests/ -vv`, `uv run black --check .`, `uv run mypy -p rich --no-incremental`, and `cd docs; make html`. Dependencies install with `uv sync --locked` from uv.lock. tox.ini provides the same through py310–py315, lint and docs environments.
