+++
id = "k1febbbc1af"
kind = "convention"
summary = "Underline length should match the title length, and the two RST styles differ slightly."
paths = ["docs/source/reference/pretty.rst", "docs/source/reference/console.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:17+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Titles use `=` underlines at least as long as the title; Sphinx warns if the underline is shorter. Some files end with extra blank lines and some have none, so there is no strict rule. A few pages leave a trailing space after `:members:` (pretty.rst, for example).
