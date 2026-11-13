+++
id = "keccd2faaca"
kind = "layout"
summary = "Test matrix covers 3 OSes and Python 3.10 to 3.15"
paths = [".github/workflows/pythonpackage.yml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:07+00:00"
last_verified = "2026-11-13T05:41:07+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The matrix is windows, ubuntu and macos, with Python 3.10, 3.11, 3.12, 3.13, 3.14 and 3.15. The windows + 3.13 combination is excluded. It triggers only on pull_request, with fail-fast off and bash as the shell on every OS. Code must therefore work on all these versions, including Windows paths and terminals.
