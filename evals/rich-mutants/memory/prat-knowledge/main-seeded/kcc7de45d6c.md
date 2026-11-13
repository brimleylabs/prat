+++
id = "kcc7de45d6c"
kind = "pitfall"
summary = "Lazy and circular imports between console.py, text, pretty, traceback, status and live"
paths = ["rich/console.py", "rich/color.py", "rich/containers.py", "rich/jupyter.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:20+00:00"
last_verified = "2026-11-13T05:38:20+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`console.py` imports `Live` and `Status` only under TYPE_CHECKING, and imports `pretty`, `rule`, `traceback`, `scope`, `json` and `status` inside methods. `color.py` imports `Text` and `Style` lazily inside `Color.__rich__`, and `containers.py` imports `Text` inside `Lines.justify`. A new top-level import in these files can create an import cycle, so follow the existing in-function pattern. `jupyter.py` imports `get_console` from the package `__init__`.
