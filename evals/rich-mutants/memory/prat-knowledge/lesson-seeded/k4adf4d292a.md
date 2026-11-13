+++
id = "k4adf4d292a"
kind = "pitfall"
summary = "make_emoji.py is legacy and writes to the current directory"
paths = ["tools/make_emoji.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:23+00:00"
last_verified = "2026-11-13T05:41:23+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
It needs the `emoji` package and imports `emoji.unicode_codes.EMOJI_ALIAS_UNICODE`, which newer emoji releases may no longer provide. It writes `_emoji_codes.py` into the current working directory, not into rich/, so move the output by hand. The output is `EMOJI=` followed by a dict of lowercased names with colons stripped.
