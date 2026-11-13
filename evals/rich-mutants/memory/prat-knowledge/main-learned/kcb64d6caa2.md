+++
id = "kcb64d6caa2"
kind = "layout"
summary = "Data-only modules are large, generated-looking tables. Edit them carefully and do not reformat them."
paths = ["rich/_emoji_codes.py", "rich/_emoji_replace.py", "rich/_spinners.py", "rich/_palettes.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:19+00:00"
last_verified = "2026-11-13T05:38:19+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`_emoji_codes.py` is a 3,600-line `EMOJI` dict used by `_emoji_replace.py` (regex `:name:` with optional `-emoji` or `-text` variant suffix, lowercased lookup, unknown names left untouched). `_spinners.py` holds the `SPINNERS` dict (interval plus frames, derived from cli-spinners under an MIT license header that must stay). `_palettes.py` holds the Windows, standard and 256-colour `Palette` tables, which `_win32_console.py` and colour downgrading depend on.
