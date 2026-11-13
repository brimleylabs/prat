+++
id = "kea5fbef3c9"
kind = "convention"
summary = "Custom highlighters and themes use a style prefix defined in `base_style`."
paths = ["examples/highlighter.py", "examples/log.py", "examples/rainbow.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:14+00:00"
last_verified = "2026-11-13T05:41:14+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
RegexHighlighter subclasses set `base_style` ("example." or "req.") and use named regex groups. The matching Theme keys are `<base_style><group>`, for example `example.email` in highlighter.py and `req.method` in log.py. rainbow.py instead subclasses Highlighter and overrides `highlight`. A custom Highlighter is passed to Console(highlighter=...) or called directly on a string.
