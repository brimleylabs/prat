+++
id = "k6fbae076e9"
kind = "convention"
summary = "Shared look: Fira Code web font, mac-style title bar, fixed palette"
paths = ["docs/images/box.svg", "docs/images/svg_export.svg"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:40+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Both SVGs declare `@font-face` for "Fira Code" with `local()` first, then cdnjs firacode 6.2.0 woff2 and woff URLs. Both draw a window titled "Rich" with three red, yellow and green circles (#ff5f57, #febc2e, #28c840). `box.svg` uses a #292929 background and `svg_export.svg` uses #191919.
