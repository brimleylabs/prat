+++
id = "kf7c250bc3f"
kind = "layout"
summary = ".github: The .github directory holds the GitHub configuration for the Rich terminal-formatting library"
paths = [".github"]
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
The .github directory holds the GitHub configuration for the Rich terminal-formatting library. That covers CI workflows, issue and PR templates, Dependabot, and the funding link. The main CI job, pythonpackage.yml, runs on pull requests. It uses `uv sync --locked`, then `make format-check`, `make typecheck` and pytest with coverage, across three operating systems and Python 3.10 to 3.15. The other workflows handle CodeQL scanning, codespell, FAQ suggestions on new issues, a comment on closed issues, and a notification when README.md changes. The templates tell contributors that Rich isn't accepting new features and that AI-generated PRs need prior maintainer approval. They also point to the FAQ, docs and discussions.
