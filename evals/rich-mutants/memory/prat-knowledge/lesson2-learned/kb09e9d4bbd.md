+++
id = "kb09e9d4bbd"
kind = "pitfall"
summary = "Workflows hard-code the 'master' branch (and upstream willmcgugan/rich), but this clone uses 'main', so triggers won't fire"
paths = [".github/workflows/codeql.yml", ".github/workflows/readmechanged.yml", ".github/workflows/newissue.yml", ".prat/project.toml"]
status = "active"
confidence = "confirmed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T12:20:42+00:00"
last_verified = "2026-11-13T12:20:42+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k0c35adb43d"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "checked against 629ab83c5fe8"

[[sources]]
note = "deep scan, 20261113T121106"
+++
codeql.yml (push and PR), readmechanged.yml (push) and newissue.yml (`ref: master`) are tied to master, while PRAT's default_branch is main (.prat/project.toml). As written, those triggers won't fire on this clone's pushes and the newissue.yml checkout would fail. readmechanged.yml also links to the upstream repo willmcgugan/rich. pythonpackage.yml runs on any pull_request and is unaffected; check branch names before relying on or editing these workflows.
