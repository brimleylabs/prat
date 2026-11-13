+++
id = "kc0e8d73aee"
kind = "convention"
summary = "Colors are immutable NamedTuples with lru_cache on parse, get_ansi_codes and downgrade"
paths = ["rich/color.py", "rich/color_triplet.py", "rich/_palettes.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:43+00:00"
last_verified = "2026-11-13T12:13:43+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`Color.parse` accepts names from `ANSI_COLOR_NAMES`, `#rrggbb`, `color(n)` and `rgb(r,g,b)`, and raises `ColorParseError` otherwise. `Color.downgrade(system)` maps truecolor to 8-bit (grayscale when saturation < 0.15) or to the standard and Windows palettes through `STANDARD_PALETTE.match` and `WINDOWS_PALETTE.match`. `get_ansi_codes` uses an offset of 82 or 92 for bright colors (numbers 8–15). `get_truecolor` falls back to `DEFAULT_TERMINAL_THEME`. `Console._color_system` drives this downgrade at render time through `Style.render`.
