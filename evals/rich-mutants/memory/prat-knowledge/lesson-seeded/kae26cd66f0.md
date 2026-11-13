+++
id = "kae26cd66f0"
kind = "layout"
summary = "Markdown renderer maps markdown-it tokens to MarkdownElement subclasses"
paths = ["rich/markdown.py", "rich/default_styles.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:17+00:00"
last_verified = "2026-11-13T05:41:17+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Markdown.__rich_console__` walks flattened markdown-it tokens (parser built with strikethrough and table enabled). It dispatches through the `Markdown.elements` class dict. Inline tags (`em`, `strong`, `code`, `s`) push styles named `markdown.<tag>`, and parent elements such as `BlockQuote`, `ListItem` and the table elements take over rendering through `on_child_close`. With `hyperlinks=False`, links render as `text (url)` using a `Link` element. To add a block type, subclass `MarkdownElement`, register it in `elements`, and add `markdown.*` styles to `default_styles.py`.
