+++
id = "k63e1eab8a1"
kind = "layout"
summary = "Large generated or vendored data tables: edit with care"
paths = ["rich/_emoji_codes.py", "rich/_emoji_replace.py", "rich/_spinners.py", "rich/_palettes.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:13:42+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`rich/_emoji_codes.py` is a 3,600-line `EMOJI` dict that is not meant to be hand-edited. It mixes Unicode names, legacy aliases and `flag_for_*` names, and is loaded lazily by `rich/_emoji_replace.py`. `rich/_spinners.py` is vendored from cli-spinners (MIT licence header at the top). `rich/_palettes.py` holds the `WINDOWS_PALETTE`, `STANDARD_PALETTE` and `EIGHT_BIT_PALETTE` colour tables and has 256 entries in the 8-bit table. Read these with offset/limit or Grep rather than whole.
