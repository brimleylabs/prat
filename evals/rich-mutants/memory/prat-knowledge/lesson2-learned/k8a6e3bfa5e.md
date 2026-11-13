+++
id = "k8a6e3bfa5e"
kind = "layout"
summary = ".prat/project.toml is the PRAT config: name, branch, autonomy, notes, commands, gates"
paths = [".prat/project.toml"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:37+00:00"
last_verified = "2026-11-13T12:13:37+00:00"
invalid_at = "2026-11-13T12:20:43+00:00"
superseded_by = "k4ff48aa5a8"

[[sources]]
note = "deep scan, 20261113T121106"
+++
It sets `name = "rich"`, `default_branch = "main"` and `autonomy = "medium"`. Its `notes` field says the package is `rich/` and the tests are in `tests/`. Adding a new gate means adding a `[commands]` entry and listing it in `[verify].gates`.
