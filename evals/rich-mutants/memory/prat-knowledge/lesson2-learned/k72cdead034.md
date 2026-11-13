+++
id = "k72cdead034"
kind = "decision"
summary = "Maintainers decline some classes of issues and PRs"
paths = ["questions/highlight_incorrect.question.md", "questions/jupyter.question.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Highlighter issues and PRs are accepted only for clearly broken regular expressions, because a fix for one user can break another. Jupyter spacing changes are resisted unless there is evidence Rich is doing the wrong thing, and PRs need research showing other Jupyter implementations are not broken. Keep this stance when you edit those answers.
