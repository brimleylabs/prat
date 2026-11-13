+++
id = "kb08dc11bb3"
kind = "pitfall"
summary = "Terminal and color detection depends on many env vars; tests inject _environ"
paths = ["rich/console.py", "rich/diagnose.py"]
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
`Console.is_terminal` checks `_force_terminal`, then idlelib, Jupyter, `TTY_COMPATIBLE`, `FORCE_COLOR`, then `isatty()`. `_detect_color_system` uses `COLORTERM` and `TERM` (Windows uses `_windows` features). Width and height come from `COLUMNS`, `LINES`, `JUPYTER_COLUMNS`, `JUPYTER_LINES`, `NO_COLOR` and `TTY_INTERACTIVE`. `legacy_windows` subtracts 1 from the width. The class attribute `_environ` and the constructor argument `_environ` exist so tests can replace `os.environ`. Behavior that looks environment-specific should be reproduced through `_environ`, not by mutating `os.environ`.
