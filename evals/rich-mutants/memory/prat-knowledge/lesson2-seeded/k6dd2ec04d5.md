+++
id = "k6dd2ec04d5"
kind = "convention"
summary = "One question per file, named *.question.md, with YAML front matter"
paths = ["questions/README.md", "questions/square_brackets.question.md", "questions/logging_color.question.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:13:42+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Files must end in `.question.md`, as questions/README.md requires. Front matter has a quoted `title` phrased like a user's question or issue title, and optionally `alt_titles` as a list of other phrasings. The body is short plain Markdown with docs links.
