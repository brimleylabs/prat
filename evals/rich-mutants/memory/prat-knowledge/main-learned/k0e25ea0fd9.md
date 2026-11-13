+++
id = "k0e25ea0fd9"
kind = "convention"
summary = "Styles are referenced by dotted theme names defined in DEFAULT_STYLES and resolved via Console.get_style; new names need a theme entry"
paths = ["rich/default_styles.py", "rich/highlighter.py", "rich/markdown.py", "rich/console.py", "rich/theme.py"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 5
hurt = 0
created = "2026-11-13T05:38:20+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "checked against aa3a3c47b423"
+++
Highlighters, Markdown, logging, Progress and Traceback look up styles by dotted names such as `repr.number`, `markdown.h1`, `logging.level.info`, `bar.complete` or `progress.percentage`. These are defined in DEFAULT_STYLES in `rich/default_styles.py` (or in a Theme passed to the Console), and `Console.get_style` resolves them through the thread-local `ThemeStack`. Highlighters build names from `base_style` plus the regex group name (e.g. `repr.` + `ipv4`), so a new regex group or style name needs a matching theme entry; `Markdown` elements call `get_style(name, default="none")`. Running `python -m rich.default_styles` prints every style, and `--html` exports them.
