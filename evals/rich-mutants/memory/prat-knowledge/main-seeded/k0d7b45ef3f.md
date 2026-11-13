+++
id = "k0d7b45ef3f"
kind = "pitfall"
summary = "Expected values are long literal escape-code strings, so small rendering changes break many tests"
paths = ["tests/test_syntax.py", "tests/test_progress.py", "tests/test_panel.py", "tests/test_segment.py", "tests/test_text.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:25+00:00"
last_verified = "2026-11-13T05:38:25+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Panel, progress, syntax, segment and text tests assert whole rendered output, including truecolor and 256-color SGR sequences, as one string literal. A change to style merging, segment splitting, or border or title rendering shows up as a diff in one of these strings. Check whether the new output is correct, then update the literal. The tests print `repr(result)` so you can copy it.
