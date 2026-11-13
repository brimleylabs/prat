+++
id = "k0c35adb43d"
kind = "pitfall"
summary = "Workflows hard-code the 'master' branch and upstream repo; this clone uses 'main'"
paths = [".github/workflows/codeql.yml", ".github/workflows/readmechanged.yml", ".github/workflows/newissue.yml", ".prat/project.toml"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:48+00:00"
last_verified = "2026-11-13T12:13:48+00:00"
invalid_at = "2026-11-13T12:20:42+00:00"
superseded_by = "kb09e9d4bbd"

[[sources]]
note = "deep scan, 20261113T121106"
+++
CodeQL, readmechanged, newissue and the templates refer to master and Textualize/rich. PRAT's default_branch is main, so those workflows will not trigger on this clone's pushes as written.
