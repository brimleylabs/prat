+++
id = "kccca5722d6"
kind = "command"
summary = "The one verification gate is the pytest run defined in .prat/project.toml"
paths = [".prat/project.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:37+00:00"
last_verified = "2026-11-13T12:13:37+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`[commands].test` runs `{python} -m pytest -q -p no:cacheprovider tests`, and `[verify].gates = ["test"]` with `timeout_seconds = 300`. The notes in the file say this is about 1,000 tests and takes a few seconds. Run the same command before finishing any change.
