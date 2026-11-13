+++
id = "kffebcdfffc"
kind = "decision"
summary = "Console markup in logging is off by default and is a RichHandler option"
paths = ["questions/logging_color.question.md", "questions/log_renderables.question.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:15+00:00"
last_verified = "2026-11-13T05:41:15+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The answer says markup is disabled by default and works only in `RichHandler`, with a link to the docs on enabling it. Logging a renderable (Table, Tree) is discouraged. The answer suggests `Console.log` or the capture API instead. If the logging defaults change, these two answers go stale.
