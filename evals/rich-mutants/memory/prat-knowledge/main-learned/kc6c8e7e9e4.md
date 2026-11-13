+++
id = "kc6c8e7e9e4"
kind = "pitfall"
summary = "Template variables are supplied externally and are not defined in this repo"
paths = [".faq/FAQ.md", ".faq/suggest.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:10+00:00"
last_verified = "2026-11-13T05:38:10+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`questions`, `faq_url` and the `question.*` fields come from FAQtory, which runs in the `.github` workflows. A rename in the templates would silently break the generated FAQ and the replies. I did not read the question source files or the workflows, so check those before changing the variable names.
