+++
id = "k8c56f9ebdb"
kind = "decision"
summary = "FAQ.md and suggest.md in .faq/ are FAQtory Jinja templates, so edit the templates, not generated output."
paths = [".faq/FAQ.md", ".faq/suggest.md"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T06:49:48+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k0c3b67ded0"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "checked against d5ff7b5fc316"
+++
Both .faq files credit FAQtory in their footers, and suggest.md also names the project author and links to their GitHub Sponsors page. The FAQ page and issue replies are generated from these templates, so change the templates rather than the output. The automation lives in .github/workflows (for example newissue.yml); check those files to confirm how the templates are used.
