+++
id = "ke21af69ebf"
kind = "command"
summary = "The docs are built with Sphinx from docs/source using the pinned requirements; rich must be installed first."
paths = ["docs/source/conf.py", "docs/source/index.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:40+00:00"
last_verified = "2026-11-13T12:13:40+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
The declared docs dependencies are Sphinx 8.2.3, sphinx-rtd-theme, sphinx-copybutton and alabaster. The directory has no Makefile of its own. Check docs/ for the Makefile or make.bat and docs/requirements.txt, which are outside this module. The `:ref:` and autosectionlabel checks run during a Sphinx build, so a build is how you catch broken labels.
