+++
id = "k6b3dcff0fe"
kind = "pitfall"
summary = "Some modules are omitted from coverage, and some patterns are excluded from the report"
paths = [".coveragerc"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:11+00:00"
last_verified = "2026-11-13T05:38:11+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
.coveragerc omits `rich/jupyter.py`, `rich/_windows.py`, `rich/_timer.py` and `rich/diagnose.py`. It also excludes `pragma: no cover`, `if TYPE_CHECKING:`, `if __name__ == "__main__":` and `@overload` lines from the report. Missing coverage on those modules is expected.
