+++
id = "k9ec86fb94f"
kind = "convention"
summary = "Type checking is mypy strict on the `rich` package only"
paths = ["pyproject.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:38+00:00"
last_verified = "2026-11-13T12:13:38+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`[tool.mypy]` has `files = ["rich"]`, `strict = true` and the extra error codes `ignore-without-code`, `redundant-expr` and `truthy-bool`. Any `# type: ignore` therefore needs an explicit error code. pygments, IPython and ipywidgets are exempted through `ignore_missing_imports`. Formatting uses black (>=26 in the dev group), and isort uses `profile = "black"`.
