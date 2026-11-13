+++
id = "k91b9e7cd6f"
kind = "command"
summary = "Run the module's tests"
paths = ["tests/test_unicode_data.py", "rich/_unicode_data/__init__.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:24+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`pytest tests/test_unicode_data.py` checks that every version in `VERSIONS` loads, that `_parse_version` pads versions correctly, and that version selection and invalid-version fallback behave as expected. Run it after touching `_versions.py`, `__init__.py` or any table file. rich/cells.py behaviour that depends on these tables is tested elsewhere in tests/.
