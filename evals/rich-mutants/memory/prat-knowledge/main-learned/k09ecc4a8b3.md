+++
id = "k09ecc4a8b3"
kind = "layout"
summary = "index.rst is the master toctree; new pages must be listed there"
paths = ["docs/source/index.rst", "docs/source/reference.rst", "docs/source/appendix.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:15+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Every guide page is listed by filename in the toctree in docs/source/index.rst (order: intro, console, style, markup, text, highlighting, pretty, logging, traceback, prompt, then per-renderable pages, protocol, reference, appendix). A new .rst file that is not added there will not appear in the navigation. reference.rst and appendix.rst each hold their own toctree pointing at reference/*.rst and appendix/*.rst.
