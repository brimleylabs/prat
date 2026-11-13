+++
id = "k38f1baaa23"
kind = "pitfall"
summary = "Python-version lists are duplicated and must be updated together"
paths = ["pyproject.toml", "tox.ini", "README.md"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:07+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = "2026-11-13T06:50:32+00:00"
superseded_by = "k278d5bd240"

[[sources]]
note = "deep scan, 20261113T053836"
+++
Supported versions appear in `requires-python` and the classifiers (3.10–3.15) in pyproject.toml, and in the tox `py{310,...,315}` envlist. Changing the supported range means editing both files. The README prose is stale: README.md says Python 3.8 or later, which contradicts `requires-python = ">=3.10"`.
