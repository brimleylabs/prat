+++
id = "k43d5bed55d"
kind = "layout"
summary = "The large data modules `_emoji_codes.py`, `_spinners.py` and `_palettes.py` are plain literals, and `_emoji_replace.py` consumes the emoji table."
paths = ["rich/_emoji_codes.py", "rich/_emoji_replace.py", "rich/_spinners.py", "rich/_palettes.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:16+00:00"
last_verified = "2026-11-13T05:41:16+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`_emoji_codes.py` is one `EMOJI` dict of lowercase name to string. It holds many aliases and some names containing odd characters (`’`, `&`, `__`). `_emoji_replace._emoji_replace` lowercases the name, looks it up, and appends a variant selector (`\ufe0e` for text, `\ufe0f` for emoji) from a `-text` or `-emoji` suffix or `default_variant`. Unknown codes are returned unchanged. `_spinners.SPINNERS` maps name to `{interval (ms), frames}`, where frames is a string or a list, and its header carries the MIT notice for cli-spinners. `_palettes.py` defines the `WINDOWS_PALETTE`, `STANDARD_PALETTE` and `EIGHT_BIT_PALETTE` `Palette` objects, in ANSI colour-number order.
