+++
id = "k4ff48aa5a8"
kind = "layout"
summary = ".prat/project.toml is the PRAT config (name, branch, autonomy, notes, commands, verify gates); it is protected from agent edits."
paths = [".prat/project.toml"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T12:20:43+00:00"
last_verified = "2026-11-13T12:20:43+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k8a6e3bfa5e"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "checked against 629ab83c5fe8"
+++
It sets `name = "rich"`, `default_branch = "main"` and `autonomy = "medium"`, with `notes` saying the package is `rich/` and tests are in `tests/`. A `[commands]` entry plus a listing in `[verify].gates` defines a gate, but agents cannot edit the file because `.prat/` is protected; changes to it must come from the user.
