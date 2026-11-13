+++
id = "ka6e3e97ffa"
kind = "command"
summary = "PRAT lint gate: python tools/check_changelog.py"
paths = [".prat/project.toml", "tools/check_changelog.py", "CHANGELOG.md"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:24+00:00"
last_verified = "2026-11-13T05:41:24+00:00"
invalid_at = "2026-11-13T06:50:32+00:00"
superseded_by = "k9b319da8e7"

[[sources]]
note = "deep scan, 20261113T053836"
+++
This gate fails if files under rich/ changed (diffed against the git merge-base with main, plus uncommitted changes) and CHANGELOG.md has no new '- ' bullet under the '## Unreleased' heading. If nothing under rich/ changed, it passes without checking.
