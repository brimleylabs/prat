+++
id = "k1cc5efc092"
kind = "convention"
summary = "Tests compare rendered output with literal ANSI snapshot strings"
paths = ["tests/render.py", "tests/test_console.py", "tests/test_table.py", "tests/test_panel.py"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:28+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = "2026-11-13T05:48:11+00:00"
superseded_by = "k96792a55cf"

[[sources]]
note = "deep scan, 20261113T053544"
+++
The tests build a Console that writes to io.StringIO, with a fixed width, color_system and legacy_windows, or they use record/capture. They then assert the exact escape-sequence output. tests/render.py provides the shared render() and replace_link_ids() helpers.
