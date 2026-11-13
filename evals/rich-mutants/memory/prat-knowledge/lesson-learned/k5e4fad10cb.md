+++
id = "k5e4fad10cb"
kind = "decision"
summary = "Pretty builds a Node tree and expands lines only when they exceed the width"
paths = ["rich/pretty.py", "rich/protocol.py"]
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
`traverse()` builds a `Node` tree. It tracks `visited_ids` to print `...` on recursion, and honours `max_length`, `max_string` and `max_depth`. `Node.render` expands a line only if `check_length` fails or `expand_all` is set. Objects are handled in a fixed order: `__rich_repr__`, attrs, dataclass with the default repr, namedtuple with the default repr, then builtin containers via `_BRACES`. It probes for fake attributes with a gibberish `hasattr` name and wraps `repr` in try/except. `Pretty` turns the result into `Text` via `from_ansi`, with `Text.from_ansi` and `ReprHighlighter`.
