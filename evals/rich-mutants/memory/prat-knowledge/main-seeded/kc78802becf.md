+++
id = "kc78802becf"
kind = "convention"
summary = "Adding a Unicode version needs both a data file and a `_versions.py` entry"
paths = ["rich/_unicode_data/_versions.py", "rich/_unicode_data/__init__.py", "tests/test_unicode_data.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:23+00:00"
last_verified = "2026-11-13T05:38:23+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`VERSIONS` in `_versions.py` is an ordered tuple of strings like `"17.0.0"`, and `load()` uses `VERSIONS[-1]` as "latest". A table file is imported from the name `.unicode{major}-{minor}-{patch}`, which is the version with dots replaced by dashes. A new version therefore needs a matching file and an entry appended in ascending order. The hard-coded `"17.0.0"` expectations in tests/test_unicode_data.py (`test_load_version` and `test_load_version_invalid`) also have to be updated.
