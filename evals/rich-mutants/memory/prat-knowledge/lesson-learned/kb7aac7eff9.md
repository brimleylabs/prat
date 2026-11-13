+++
id = "kb7aac7eff9"
kind = "pitfall"
summary = "Workflows reference branch 'master' while the local branch is main"
paths = [".github/workflows/codeql.yml", ".github/workflows/readmechanged.yml", ".github/workflows/newissue.yml"]
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
codeql.yml (push and pull_request), readmechanged.yml (push) and newissue.yml (checkout ref) are tied to `master`. Renaming or retargeting branches silently disables them. The README-change workflow also needs a secret (GHP_README_WORKFLOW), and the coverage upload needs CODECOV_TOKEN.
