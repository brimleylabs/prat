+++
id = "ka59cfb2ddc"
kind = "layout"
summary = "questions: The questions/ directory holds canned answers to frequent Rich support questions, one Markdown file per question, named `*.question.md`"
paths = ["questions"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:18+00:00"
last_verified = "2026-11-13T05:38:18+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The questions/ directory holds canned answers to frequent Rich support questions, one Markdown file per question, named `*.question.md`. Each file has YAML front matter (`title`, optional `alt_titles`) followed by a short answer that often links to the Rich docs on readthedocs. The files look meant to be matched against incoming issues, using the title and alternate phrasings. They contain no code and don't import from the rest of the project. Their content describes behaviour implemented in `rich/` (markup, highlighting, logging, traceback, emoji width, `Text.from_ansi`) and documented under `docs/`. Several also state maintainer policy on which issues and PRs will be accepted.
