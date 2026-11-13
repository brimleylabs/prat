+++
id = "k0d7b45ef3f"
kind = "pitfall"
summary = "Small rendering changes break many snapshot tests that assert whole rendered escape-code strings"
paths = ["tests/test_syntax.py", "tests/test_progress.py", "tests/test_panel.py", "tests/test_segment.py", "tests/test_text.py", "rich/style.py", "rich/segment.py", "rich/text.py", "rich/table.py", "tests"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 5
hurt = 0
created = "2026-11-13T05:38:25+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "deep scan, 20261113T053544"
+++
Panel, progress, syntax, segment and text tests assert full rendered output, including truecolor and 256-color SGR sequences, as long string literals. Changes to Style, Segment, cells, Text wrapping, Table widths, default_styles, style merging, or border and title rendering can alter expected strings in dozens of test files. Check every failing diff to confirm the change is intended, then update the literal; the tests print `repr(result)` so you can copy it.
