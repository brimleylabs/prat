+++
id = "k807deaa8b4"
kind = "convention"
summary = "codespell runs on every push and PR with a fixed ignore list and skip paths"
paths = [".github/workflows/codespell.yml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:39+00:00"
last_verified = "2026-11-13T12:13:39+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
codespell uses `--ignore-words-list="ba,fo,hel,revered,womens"` and skips README translations, svg and ai files, benchmarks/snippets.py, tests, tools and lock files. A deliberately odd spelling in docs or rich/ source may need adding to the ignore list. Tests and tools are not spell-checked.
