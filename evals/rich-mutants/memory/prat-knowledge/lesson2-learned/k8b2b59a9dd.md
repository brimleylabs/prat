+++
id = "k8b2b59a9dd"
kind = "layout"
summary = "Issue automation: FAQtory suggestions on open, a canned comment on close"
paths = [".github/workflows/newissue.yml", ".github/workflows/comment.yml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:39+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
newissue.yml installs FAQtory, runs `faqtory suggest` on the issue title and posts the result as a comment. The workflow files for opened and closed issues both have `name: issues`. comment.yml posts a thank-you and sponsorship comment when an issue is closed. Both use peter-evans/create-or-update-comment pinned to a commit SHA, and the title is passed through an env var.
