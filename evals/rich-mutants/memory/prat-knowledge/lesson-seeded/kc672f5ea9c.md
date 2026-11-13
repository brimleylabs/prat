+++
id = "kc672f5ea9c"
kind = "layout"
summary = "Issue automation: FAQ suggestion on open, thank-you comment on close"
paths = [".github/workflows/newissue.yml", ".github/workflows/comment.yml", ".github/ISSUE_TEMPLATE/bug_report.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:08+00:00"
last_verified = "2026-11-13T05:41:08+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
newissue.yml installs FAQtory and runs `faqtory suggest` on the issue title, which is passed through an env var, then posts the result as a comment. comment.yml posts a sponsorship message when an issue is closed. Both workflows are named 'issues'. The bug template links to the FAQ and to `python -m rich.diagnose` and `rich.diagnose.report()` for diagnostics.
