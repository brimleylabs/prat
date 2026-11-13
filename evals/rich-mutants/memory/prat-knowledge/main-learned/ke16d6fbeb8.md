+++
id = "ke16d6fbeb8"
kind = "convention"
summary = "Renderables implement __rich_console__ and optionally __rich_measure__, with Console imported only under TYPE_CHECKING"
paths = ["rich/panel.py", "rich/rule.py", "rich/styled.py", "rich/screen.py", "rich/palette.py", "rich/pretty.py", "rich/console.py", "rich/abc.py", "rich/jupyter.py", "rich/align.py"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 6
hurt = 0
created = "2026-11-13T05:38:21+00:00"
last_verified = "2026-11-13T05:38:21+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "deep scan, 20261113T053544"
+++
Renderables (Panel, Rule, Syntax, Pretty, ProgressBar, Styled) implement `__rich_console__(console, options)`, yielding Segments, Text or other renderables, and optionally `__rich_measure__`, which returns a Measurement for width. Most also inherit `JupyterMixin`. Console types (`rich.console`) are imported only under `if TYPE_CHECKING:` or inside functions to avoid circular imports, as in rich/panel.py, rich/rule.py, rich/styled.py and rich/screen.py.
