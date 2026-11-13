+++
id = "kb6fe7a85e6"
kind = "decision"
summary = "Packaging moved from Poetry to uv; Python 3.9 dropped, 3.15 added"
paths = ["CHANGELOG.md", ".python-version", ".readthedocs.yml"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:11+00:00"
last_verified = "2026-11-13T05:38:11+00:00"
invalid_at = "2026-11-13T05:48:11+00:00"
superseded_by = "k71d79270e1"

[[sources]]
note = "deep scan, 20261113T053544"
+++
The Unreleased section of CHANGELOG.md records the switch to uv, the end of Python 3.9 support and the addition of 3.15. `.python-version` pins 3.15, while the Read the Docs build uses Python 3.12. The docs build installs `docs/requirements.txt` and the package itself.
