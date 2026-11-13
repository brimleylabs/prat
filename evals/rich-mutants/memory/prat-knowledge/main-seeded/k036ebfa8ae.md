+++
id = "k036ebfa8ae"
kind = "convention"
summary = "Modules end with a __main__ demo, and running it is the manual smoke test"
paths = ["rich/__main__.py", "rich/progress.py", "rich/syntax.py", ".coveragerc"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:27+00:00"
last_verified = "2026-11-13T05:38:27+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Most rich modules end with an `if __name__ == "__main__"` block, so `python -m rich.<module>` shows a rendered demo. `python -m rich` renders the full test card. .coveragerc excludes these blocks from coverage.
