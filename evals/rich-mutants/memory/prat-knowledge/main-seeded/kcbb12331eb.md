+++
id = "kcbb12331eb"
kind = "decision"
summary = "console.rst is the canonical description of environment-variable behaviour"
paths = ["docs/source/console.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:16+00:00"
last_verified = "2026-11-13T05:38:16+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
console.rst documents TERM=dumb/unknown, FORCE_COLOR, NO_COLOR (takes precedence over FORCE_COLOR and removes color only), TTY_COMPATIBLE, TTY_INTERACTIVE, COLUMNS/LINES and JUPYTER_COLUMNS/JUPYTER_LINES. It recommends TTY_COMPATIBLE=1 with TTY_INTERACTIVE=0 for CI. It says constructor arguments take precedence over environment variables. Changes to detection logic in rich/console.py should be reflected here.
