+++
id = "k6c8b50ea0b"
kind = "pitfall"
summary = "Codespell runs on push and PR, with an ignore list and skip globs"
paths = [".github/workflows/codespell.yml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:08+00:00"
last_verified = "2026-11-13T05:41:08+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
codespell.yml ignores the words ba, fo, hel, revered and womens. It skips README.*.md, svg, ai, benchmarks/snippets.py, tests, tools and lock files. A new intentional non-word in rich/, docs or the README will fail CI unless it is reworded or added to the ignore list.
