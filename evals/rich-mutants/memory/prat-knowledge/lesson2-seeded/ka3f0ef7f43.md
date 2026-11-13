+++
id = "ka3f0ef7f43"
kind = "convention"
summary = "Text keeps its string as a list of chunks plus a cached length, and spans are always offsets into plain"
paths = ["rich/text.py"]
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
`Text._text` is a list of strings that `plain` joins lazily, and `_length` is cached. Code that mutates `_text` directly must also update `_length` and `_spans`, as `expand_tabs`, `right_crop`, `truncate` and `extend_style` do. Use `plain.setter`, `append`, `append_text` and `append_tokens` otherwise. `Text` uses `__slots__`, so new attributes must be added to that list. Text passed in is sanitized with `strip_control_codes`. `append(Text, style=...)` raises ValueError. Hot paths bind `Span`/`Segment` to local names like `_Span` for speed, so keep that style.
