+++
id = "k5faeb4f691"
kind = "pitfall"
summary = "check_changelog.py is the project's lint gate: it requires a new `- ` entry under `## Unreleased` in CHANGELOG.md when rich/ changed, and exact text matching decides what is new."
paths = ["tools/check_changelog.py", "CHANGELOG.md"]
status = "superseded"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T06:50:02+00:00"
last_verified = "2026-11-13T06:50:02+00:00"
invalid_at = "2026-11-13T06:50:33+00:00"
superseded_by = "k068fc87029"

[[links]]
to = "k8085f971a9"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "checked against d5ff7b5fc316"
+++
Entries are lines starting with `- ` under a heading that lowercases to exactly `## unreleased`; any other `## ` heading ends the section. A new entry is a line whose text is not in the base CHANGELOG.md, so rewording an existing entry counts as new. The base is CHANGELOG.md at the git merge-base with main (or an optional BASE_CHANGELOG path argument); if nothing under rich/ changed it prints 'nothing to describe' and passes. Run it from the repo root, as the project's lint command does, since it reads CHANGELOG.md by relative path.
