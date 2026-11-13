+++
id = "k897aa3ec9a"
kind = "layout"
summary = "rich (part 1): This is the first group of helper modules in the `rich` package"
paths = ["rich"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:19+00:00"
last_verified = "2026-11-13T05:38:19+00:00"
invalid_at = "2026-11-13T05:47:40+00:00"
superseded_by = "k1a067763fa"

[[sources]]
note = "deep scan, 20261113T053544"
+++
This is the first group of helper modules in the `rich` package. It holds the public entry point `rich/__init__.py`, which exposes a lazily created global Console plus `print`, `print_json`, `inspect` and `reconfigure`. It also holds small renderables (`align.py`, `bar.py`, `box.py`), ANSI decoding (`ansi.py`), and the Windows legacy-console layer (`_win32_console.py`, `_windows.py`, `_windows_renderer.py`). The rest is internal support: emoji tables and replacement, spinner and palette data, ratio and wrap math, loop and stack helpers, `_inspect.py` for `rich.inspect`, `_log_render.py` for logging output, and SVG/HTML export templates. Most files import `console`, `segment`, `style`, `text`, `table` and `cells` from other `rich` modules, and are imported back by `console.py`, `live.py`, `layout.py`, `progress`-style code and the examples. Many files end with an `if __name__ == "__main__"` demo block that doubles as a manual test.
