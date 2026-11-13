+++
id = "k0c3b67ded0"
kind = "decision"
summary = "FAQ and issue-reply text are generated from templates, so change the templates and not the output"
paths = [".faq/FAQ.md", ".faq/suggest.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:05+00:00"
last_verified = "2026-11-13T05:41:05+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Both `.faq` files credit FAQtory in their footers. `suggest.md` also names the project author and links to their GitHub Sponsors page. The project chose to generate the FAQ and the issue replies from templates, so output files should not be edited directly. The `.github/workflows` files `newissue.yml` and `readmechanged.yml` are likely the automation that uses these templates. I did not open them.
