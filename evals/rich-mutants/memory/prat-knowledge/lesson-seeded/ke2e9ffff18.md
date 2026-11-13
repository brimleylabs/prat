+++
id = "ke2e9ffff18"
kind = "layout"
summary = "rich (part 2): This part of the `rich` package holds the core rendering pipeline"
paths = ["rich"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:16+00:00"
last_verified = "2026-11-13T05:41:16+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
This part of the `rich` package holds the core rendering pipeline. `rich/console.py` defines `Console`, `ConsoleOptions`, `Group`, the `Capture`, `Theme`, `Pager` and `Screen` context managers, and the HTML, SVG and text exporters. Around it sit the width and measurement layer (`cells.py`, `measure.py`, `constrain.py`, `padding.py`, `containers.py`, `columns.py`), the colour layer (`color.py`, `color_triplet.py`, `default_styles.py`), and the markup, highlighting and format renderables (`markup.py`, `highlighter.py`, `json.py`, `markdown.py`, `emoji.py`, `filesize.py`). It also holds the live and log display code (`live.py`, `live_render.py`, `file_proxy.py`, `layout.py`, `logging.py`, `control.py`, `jupyter.py`, `pager.py`, `diagnose.py`, `errors.py`). Renderables implement `__rich_console__(console, options)`, which yields `Segment` objects or other renderables. They optionally implement `__rich_measure__` to report a `Measurement`. `Console.render` and `render_lines` flatten these into segments, and the console buffers them and writes them to the output file. The module depends on `segment.py`, `style.py`, `text.py`, `table.py`, `syntax.py`, `panel.py`, `pretty.py` and `rich/__i
