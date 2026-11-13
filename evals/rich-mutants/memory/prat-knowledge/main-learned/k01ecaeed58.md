+++
id = "k01ecaeed58"
kind = "convention"
summary = "API docs come from docstrings, and the guide pages are hand-written reST"
paths = ["docs/source/reference", "docs/source/reference.rst", "docs/source/index.rst", "docs/source/conf.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:29+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
docs/source/reference/*.rst are one-line automodule stubs, so API documentation is edited in rich/ docstrings. If you rename or move a rich module, update its stub and the toctree in reference.rst, or the docs build breaks. New guide pages have to be listed in index.rst.
