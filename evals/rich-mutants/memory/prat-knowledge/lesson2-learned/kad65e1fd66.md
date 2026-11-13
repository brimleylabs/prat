+++
id = "kad65e1fd66"
kind = "decision"
summary = "Syntax delegates highlighting to Pygments through swappable SyntaxTheme classes"
paths = ["rich/syntax.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:44+00:00"
last_verified = "2026-11-13T12:13:44+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`Syntax.get_theme` returns `ANSISyntaxTheme` for the `ansi_light` and `ansi_dark` names in `RICH_SYNTAX_THEMES`, and `PygmentsSyntaxTheme` for anything else. An unknown Pygments theme falls back to `default`, and an unknown lexer falls back to the `text` lexer (`default_lexer`). Rendering has a simple path (no line numbers, wrap or line range) and a per-line path. Code is always normalised to end in `\n` by `_process_code`, and `ends_on_nl` records the original state. `stylize_range` uses 1-based lines and 0-based columns.
