+++
id = "kae02193aec"
kind = "layout"
summary = "Status and Spinner wrap Live and the _spinners data; Prompt classes subclass PromptBase"
paths = ["rich/status.py", "rich/spinner.py", "rich/prompt.py", "rich/syntax.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:18+00:00"
last_verified = "2026-11-13T05:41:18+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Status` is a transient `Live` around a `Spinner`, and `Spinner` reads frames from `rich/_spinners.py` by name, raising `KeyError` for an unknown name. `PromptBase` runs a loop of `make_prompt`, `get_input` (`console.input`), `process_response` and `on_validate_error`. Subclasses (`Prompt`, `IntPrompt`, `FloatPrompt`, `Confirm`) override `response_type` and the messages. `Syntax` themes come from Pygments, or from the built-in `ANSI_LIGHT` and `ANSI_DARK` maps via `ANSISyntaxTheme`. Line numbers, indent guides, word wrap and `line_range` take a slower code path in `_get_syntax`.
