+++
id = "k0d1ece4b53"
kind = "layout"
summary = "FAQ pipeline: questions/*.question.md are the source files FAQtory (configured in faq.yml) turns into FAQ.md and issue-reply suggestions via .faq/ templates."
paths = ["questions", "faq.yml", ".faq/FAQ.md", ".faq/suggest.md", ".github/workflows/newissue.yml", "FAQ.md"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:27+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "checked against d5ff7b5fc316"
+++
Edit the question files in questions/ or the templates in .faq/, never the generated FAQ.md. Each *.question.md needs YAML front matter with `title` and optional `alt_titles`, followed by a short markdown answer (topics include ANSI escapes, emoji, highlighting, Jupyter spacing, logging, square-bracket markup and tracebacks, mostly linking to rich.readthedocs.io). faq.yml sets `questions_path: ./questions`, and newissue.yml posts a suggestion rendered from .faq/suggest.md when an issue is opened; the questions directory has no code.
