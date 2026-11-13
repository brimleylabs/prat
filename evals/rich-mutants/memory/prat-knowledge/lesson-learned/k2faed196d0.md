+++
id = "k2faed196d0"
kind = "convention"
summary = "Renderables implement __rich_console__ yielding Segments, plus an optional __rich_measure__"
paths = ["rich/panel.py", "rich/rule.py", "rich/styled.py", "rich/screen.py", "rich/syntax.py", "rich/pretty.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:17+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Panel`, `Rule`, `Pretty`, `Syntax`, `Styled`, `Screen`, `Spinner` and `ProgressBar` render by yielding `Segment`s or other renderables. They report width through `__rich_measure__` returning `Measurement`. Most inherit `JupyterMixin`, and `Console`/`ConsoleOptions` imports sit under `TYPE_CHECKING` to avoid circular imports. A new renderable should follow the same pattern.
