+++
id = "k0e25ea0fd9"
kind = "convention"
summary = "Styles are named in the theme dict and looked up by dotted names"
paths = ["rich/default_styles.py", "rich/highlighter.py", "rich/markdown.py", "rich/console.py"]
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
`default_styles.py` maps style names such as `repr.number`, `markdown.h1`, `logging.level.info` and `bar.complete` to `Style` objects, and `Console.get_style` resolves them through the thread-local `ThemeStack`. Highlighters build style names from `base_style` plus the regex group name, for example `repr.` + `ipv4`, so a new regex group needs a matching theme entry. `Markdown` elements call `get_style(name, default="none")`. Running `python -m rich.default_styles` prints every style, and `--html` exports them.
