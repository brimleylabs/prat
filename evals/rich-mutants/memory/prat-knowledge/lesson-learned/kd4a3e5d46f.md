+++
id = "kd4a3e5d46f"
kind = "pitfall"
summary = "highlight_incorrect.question.md uses the wrong front matter key `alt_title`"
paths = ["questions/highlight_incorrect.question.md", "questions/ansi_escapes.question.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:14+00:00"
last_verified = "2026-11-13T05:41:14+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
highlight_incorrect.question.md writes `alt_title:` (singular) with an indented list. Every other file uses `alt_titles:`, and the indentation varies between 2 and 4 spaces. Any parser that reads `alt_titles` will skip this file's alternates. Use `alt_titles` in new files.
