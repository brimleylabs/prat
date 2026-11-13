+++
id = "k4dddef4b1f"
kind = "convention"
summary = "README.md is the source of truth, and the translated READMEs only mirror it"
paths = ["README.md", "README.de.md", "README.pl.md", ".github/workflows/readmechanged.yml", "imgs"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:30+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
About 18 README.<lang>.md files mirror README.md and share a language-link header. Several are out of sync or truncated. A workflow sends a notification when README.md changes. Images are loaded from imgs/, some through raw GitHub URLs.
