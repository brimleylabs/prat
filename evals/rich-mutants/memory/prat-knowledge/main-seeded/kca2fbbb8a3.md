+++
id = "kca2fbbb8a3"
kind = "decision"
summary = "Traceback uses its own Theme, Pygments styles and a Console excepthook"
paths = ["rich/traceback.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:23+00:00"
last_verified = "2026-11-13T05:38:23+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`__rich_console__` builds a Theme with `inherit=False` from the Pygments theme's token styles, which is `ansi_dark` by default, and applies it with `console.use_theme`. `install()` replaces `sys.excepthook`, or the IPython `_showtraceback` hooks when `get_ipython()` is available, and returns the previous hook. Frames hidden by `max_frames` (minimum 4) are replaced by a '... N frames hidden ...' line, and suppressed paths show no source.
