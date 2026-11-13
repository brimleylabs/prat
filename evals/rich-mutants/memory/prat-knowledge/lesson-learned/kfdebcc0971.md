+++
id = "kfdebcc0971"
kind = "convention"
summary = "Docs toolchain in docs/requirements.txt is pinned exactly with `==`: alabaster 1.0.0, Sphinx 8.2.3, sphinx-rtd-theme 3.0.2, sphinx-copybutton 0.5.2"
paths = ["docs/requirements.txt"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:09+00:00"
last_verified = "2026-11-13T05:41:09+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "deep scan, 20261113T053836"
+++
docs/requirements.txt pins alabaster==1.0.0, Sphinx==8.2.3, sphinx-rtd-theme==3.0.2 and sphinx-copybutton==0.5.2, each with `==`. When you change a version, do it deliberately and check that the docs still build. These pins are separate from the library dependencies in pyproject.toml.
