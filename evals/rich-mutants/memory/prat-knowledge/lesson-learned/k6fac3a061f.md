+++
id = "k6fac3a061f"
kind = "pitfall"
summary = "The two files use different exporter generations, so they are not interchangeable"
paths = ["docs/images/box.svg", "docs/images/svg_export.svg"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:11+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
box.svg uses `<g>` groups, per-line `clipPath` entries and `<text>` elements. It has a `font-variant-east-asian: full-width` rule, a 24.4px line height and a #292929 background. svg_export.svg uses `<tspan>` elements and per-cell `<rect>` backgrounds, with a 26.4px line height and a #191919 background. It has no per-line clipPaths. Do not splice markup from one into the other.
