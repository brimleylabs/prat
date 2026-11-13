+++
id = "k3cbaf2d7b9"
kind = "layout"
summary = "The logo exists as ASCII art and as an SVG, in different places"
paths = ["assets/logo.txt", "imgs/logo.svg"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:37+00:00"
last_verified = "2026-11-13T12:13:37+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`assets/logo.txt` is a 9-line ASCII drawing, and `imgs/logo.svg` is a vector file of about 45 KB. The SVG is one large `<svg>` element with a gradient, an outline filter and long path data, and it is not meant to be edited by hand. The other files in `imgs/` are PNGs and GIFs used by `rich/logging.py`, `benchmarks/snippets.py` and some tests, but no file I read points to `logo.svg`.
