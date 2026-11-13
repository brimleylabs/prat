+++
id = "kc0c4ace2dc"
kind = "pitfall"
summary = "Translated READMEs and examples are not tested; leave them alone unless the card asks"
paths = ["README.md", "README.ja.md", "README.pl.md", "examples/README.md", "examples/downloader.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:27+00:00"
last_verified = "2026-11-13T05:41:27+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The 18 README.*.md translations are already out of sync. Some examples need the network, extra packages or arguments, never exit, or write files to the current directory, so do not run them in automated verification.
