+++
id = "kcacfb8b855"
kind = "decision"
summary = "Project accepts bug fixes but not new features; feature ideas go to GitHub discussions"
paths = [".github/pull_request_template.md", ".github/ISSUE_TEMPLATE/feature_request.md", "FAQ.md", "AI_POLICY.md", ".github/ISSUE_TEMPLATE/bug_report.md"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:27+00:00"
last_verified = "2026-11-13T05:41:27+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "deep scan, 20261113T053836"
+++
The PR and issue templates and the FAQ say Rich isn't accepting new features, and feature ideas need prior approval in GitHub discussions. Enhancements to existing features may be accepted if they don't disrupt the API, and hooks that make the core easier to extend are acceptable. Keep changes to bug fixes, maintenance, or small non-breaking extensions, keep runtime dependencies minimal, and follow AI_POLICY.md for AI-generated contributions.
