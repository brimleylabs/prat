+++
id = "kb739cd74ad"
kind = "pitfall"
summary = "Adding a Unicode version touches _versions.py, a new generated table and tests together"
paths = ["rich/_unicode_data/_versions.py", "tests/test_unicode_data.py", "rich/cells.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:26+00:00"
last_verified = "2026-11-13T05:41:26+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Version selection uses _versions.py, and tests/test_unicode_data.py pins parsing and selection. Tables must be sorted and non-overlapping, with widths 0 or 2.
