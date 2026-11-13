+++
id = "k7bb0dbe03f"
kind = "layout"
summary = "(misc group 1): This group holds small non-code support files for Rich, a Python library for rich text and formatting in the terminal"
paths = ["(misc group 1)"]
status = "stale"
confidence = "observed"
author = "scout"
helped = 0
hurt = 1
created = "2026-11-13T05:38:09+00:00"
last_verified = "2026-11-13T05:38:09+00:00"
invalid_at = "2026-11-13T05:47:32+00:00"
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "reported wrong: The entry says '(misc group 1)' holds .faq/ templates, .prat/project.toml, assets/logo.txt and imgs/logo.svg, but the evidence states '(misc group 1) (does not exist at the current commit)'. The inventory also shows assets/ now holds 1 .ai and 1 .svg file, not logo.txt, and imgs/ holds 16 .png and 5"
+++
This group holds small non-code support files for Rich, a Python library for rich text and formatting in the terminal. `.faq/` has two FAQtory Jinja templates that render the FAQ page and the automated reply on new issues. `.prat/project.toml` tells PRAT how to test the project. `assets/logo.txt` is an ASCII-art logo. `imgs/logo.svg` is a single-line SVG, about 45KB, that the localized README files embed through a raw GitHub URL. I could not read `imgs/logo.svg` in full. Its single very long line exceeds the read limit, so I saw only the `<svg>` opening tag, which has `viewBox="-71.791 155.194 749.563 184.069"`. None of these files contain Python code.
