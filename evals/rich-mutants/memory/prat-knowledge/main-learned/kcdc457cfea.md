+++
id = "kcdc457cfea"
kind = "decision"
summary = "Python support floor and runtime dependencies are intentionally minimal and must be kept consistent"
paths = ["pyproject.toml", "tox.ini"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:12+00:00"
last_verified = "2026-11-13T05:38:12+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
pyproject.toml requires Python >=3.10 and lists 3.10–3.15 classifiers, and tox.ini's py{310..315} list matches. Runtime deps are only `markdown-it-py>=2.2.0` and `pygments>=2.13.0,<3.0.0`; ipywidgets is an optional `jupyter` extra. When raising or lowering the Python range, update pyproject.toml classifiers and the tox envlist together.
