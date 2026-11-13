+++
id = "kc935a47aca"
kind = "command"
summary = "The PRAT test gate is pytest over tests/ with the cache plugin disabled"
paths = [".prat/project.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:09+00:00"
last_verified = "2026-11-13T05:38:09+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`.prat/project.toml` defines `test` as `{python} -m pytest -q -p no:cacheprovider tests`. It is the only verify gate, with a 300 second timeout. The notes say there are about 1,000 tests and that they run in a few seconds.
