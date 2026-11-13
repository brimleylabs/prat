+++
id = "kcc7de45d6c"
kind = "pitfall"
summary = "rich has circular imports among console, text, pretty, traceback, status, live, color, containers and jupyter, broken by lazy and TYPE_CHECKING imports; keep new imports inside functions"
paths = ["rich/console.py", "rich/color.py", "rich/containers.py", "rich/jupyter.py", "rich/__init__.py", "rich/_extension.py"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:20+00:00"
last_verified = "2026-11-13T05:38:20+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "deep scan, 20261113T053544"
+++
console.py imports `Live` and `Status` only under TYPE_CHECKING, and imports `pretty`, `rule`, `traceback`, `scope`, `json` and `status` inside methods. `color.py` imports `Text` and `Style` lazily inside `Color.__rich__`, `containers.py` imports `Text` inside `Lines.justify`, and `jupyter.py` imports `get_console` from the package `__init__`. Moving any of these to module level can create an import cycle and make `import rich` fail, so follow the existing in-function pattern.
