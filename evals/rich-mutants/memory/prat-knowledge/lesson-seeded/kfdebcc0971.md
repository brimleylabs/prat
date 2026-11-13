+++
id = "kfdebcc0971"
kind = "convention"
summary = "Docs toolchain versions are pinned exactly"
paths = ["docs/requirements.txt"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:09+00:00"
last_verified = "2026-11-13T05:41:09+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
docs/requirements.txt pins alabaster==1.0.0, Sphinx==8.2.3, sphinx-rtd-theme==3.0.2 and sphinx-copybutton==0.5.2, each with `==`. When you change a version, change it deliberately and check that the docs still build. The pins are separate from the library dependencies in pyproject.toml.
