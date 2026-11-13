+++
id = "kdf548e0f9f"
kind = "convention"
summary = "rich/ is checked with mypy strict and needs full type annotations"
paths = ["pyproject.toml", "CONTRIBUTING.md"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:28+00:00"
last_verified = "2026-11-13T05:38:28+00:00"
invalid_at = "2026-11-13T05:48:09+00:00"
superseded_by = "k6642ffa056"

[[sources]]
note = "deep scan, 20261113T053544"
+++
pyproject.toml sets strict = true and enables the extra error codes ignore-without-code, redundant-expr and truthy-bool. Only the rich package is checked. A type: ignore needs an explicit error code.
