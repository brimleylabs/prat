+++
id = "kfdf37da738"
kind = "layout"
summary = "Vulnerability reports go through Tidelift; FAQ.md is generated, not hand-edited"
paths = ["SECURITY.md", "faq.yml"]
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
SECURITY.md only points to the Tidelift security contact and has no other process. faq.yml configures FAQtory with questions in ./questions and templates in .faq, writing ./FAQ.md. Edit the question files or templates rather than the generated FAQ.md. Note faq.yml's `faq_url` points to FAQ.md on the `master` branch.
