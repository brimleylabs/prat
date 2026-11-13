+++
id = "kc278de20dc"
kind = "layout"
summary = "questions: The questions/ directory holds canned answers to frequently asked questions about Rich, one Markdown file per question (`*.question.md`)"
paths = ["questions"]
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
The questions/ directory holds canned answers to frequently asked questions about Rich, one Markdown file per question (`*.question.md`). Each file has YAML front matter (`title`, optional `alt_titles`) and a short answer body that often links to the Rich docs on readthedocs. The answers explain common surprises: ANSI escapes, emoji width, highlighting, markup brackets, Jupyter spacing, logging, spinner output and tracebacks. They also state the maintainers' stance on which issues and PRs they will accept. The directory has no code and no tests. Its only link to the rest of the project is by topic, to rich/ modules such as highlighter, markup, logging, traceback, spinner and text, and to the docs. The README says to put new questions here and to use the `.question.md` extension.
