+++
id = "k0d1ece4b53"
kind = "layout"
summary = "FAQ pipeline: questions/*.question.md -> FAQtory (faq.yml, .faq templates) -> FAQ.md and issue replies"
paths = ["questions", "faq.yml", ".faq/FAQ.md", ".faq/suggest.md", ".github/workflows/newissue.yml", "FAQ.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:27+00:00"
last_verified = "2026-11-13T05:41:27+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Edit questions or the templates, not the generated FAQ.md. newissue.yml posts a suggestion rendered from .faq/suggest.md when an issue is opened. Question files need YAML front matter with title and optional alt_titles.
