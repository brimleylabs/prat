+++
id = "kbfff743faf"
kind = "layout"
summary = "rich (part 4): This part of rich holds the renderables that lay out text and structured data: `Text` (styled strings made of `Span`s), `Table`, `Tree` and `Traceback`"
paths = ["rich"]
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
This part of rich holds the renderables that lay out text and structured data: `Text` (styled strings made of `Span`s), `Table`, `Tree` and `Traceback`. It also holds the theme layer: `Theme`/`ThemeStack` for style lookup, `themes.py` for the DEFAULT theme, and `TerminalTheme` for the colors used when exporting. Each renderable follows the console protocol, with `__rich_console__` yielding `Segment`s and `__rich_measure__` returning a `Measurement`. They depend on `console.py`, `segment.py`, `style.py`, `measure.py`, `padding.py`, `containers.py`, `cells.py` and `_wrap.py`. `Text` is the base primitive that `Table`, `Tree`, `Traceback`, markup, highlighters and most other modules use. `traceback.py` is the heaviest consumer: it combines `Syntax`, `Panel`, `Columns`, `pretty` and `scope`, and installs `sys.excepthook` or an IPython hook. Each of these files ends with an `if __name__ == "__main__"` demo block marked `# pragma: no cover`.
