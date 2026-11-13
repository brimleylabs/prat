+++
id = "ka3a52aadbd"
kind = "convention"
summary = "Doc dependencies are exact-pinned in docs/requirements.txt"
paths = ["docs/requirements.txt"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:13+00:00"
last_verified = "2026-11-13T05:38:13+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
alabaster, Sphinx, sphinx-rtd-theme and sphinx-copybutton are each pinned with `==`. Install them with `pip install -r docs/requirements.txt` before building. When you bump a version, change this file and nothing else, so builds stay reproducible.
