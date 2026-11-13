+++
id = "ke16d6fbeb8"
kind = "convention"
summary = "Renderables implement __rich_console__ / __rich_measure__, with Console types imported only under TYPE_CHECKING"
paths = ["rich/panel.py", "rich/rule.py", "rich/styled.py", "rich/screen.py", "rich/palette.py", "rich/pretty.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:21+00:00"
last_verified = "2026-11-13T05:38:21+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Widgets such as Panel, Rule, Syntax, Pretty, ProgressBar and Styled yield Segments or Text from `__rich_console__(console, options)` and report width from `__rich_measure__`. Most inherit `JupyterMixin`. Panel, Screen, Segment, Styled and Palette import `rich.console` only inside `if TYPE_CHECKING:` or inside functions, to avoid circular imports.
