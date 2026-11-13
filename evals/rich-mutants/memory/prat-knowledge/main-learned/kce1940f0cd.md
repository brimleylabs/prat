+++
id = "kce1940f0cd"
kind = "decision"
summary = "AI-generated PRs are allowed only under strict conditions"
paths = ["AI_POLICY.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:11+00:00"
last_verified = "2026-11-13T05:38:11+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
AI_POLICY.md requires the PR template to be filled in and the PR to say it is AI generated, naming the agent. The PR must also link an issue or discussion where a maintainer has approved the solution. PRs that don't meet these conditions may be closed without comment.
