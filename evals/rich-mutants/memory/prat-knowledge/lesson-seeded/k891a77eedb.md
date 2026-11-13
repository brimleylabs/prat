+++
id = "k891a77eedb"
kind = "convention"
summary = "Cross-reference with Sphinx roles and shared labels instead of raw URLs"
paths = ["docs/source/markup.rst", "docs/source/style.rst", "docs/source/live.rst", "docs/source/console.rst", "docs/source/protocol.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:11+00:00"
last_verified = "2026-11-13T05:41:11+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Pages link to API objects with :class:, :meth:, :func: and :ref: roles. Shared anchors such as `.. _console_markup:`, `.. _styles:`, `.. _themes:`, `.. _highlighting:`, `.. _live:`, `.. _progress:`, `.. _protocol:` and `.. _interactive_mode:` are targets that other pages use. Renaming or removing one of these labels breaks links in other files, such as console.rst, introduction.rst, prompt.rst and progress.rst. Other labels like appendix-colors and appendix_box are defined in the appendix pages and used from here.
