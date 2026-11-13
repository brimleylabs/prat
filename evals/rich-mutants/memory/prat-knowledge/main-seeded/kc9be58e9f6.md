+++
id = "kc9be58e9f6"
kind = "command"
summary = "Building the docs"
paths = ["docs/source/conf.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:16+00:00"
last_verified = "2026-11-13T05:38:16+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The files list no Makefile or build script, and the declared dependencies are Sphinx==8.2.3, sphinx-rtd-theme==3.0.2, sphinx-copybutton==0.5.2 and alabaster==1.0.0. A standard build would be `sphinx-build -b html docs/source <outdir>` from the repo root, run in an environment with rich installed. conf.py expects `_templates` and `_static` directories under docs/source, which were not in this module's file list. Check docs/ for a Makefile before relying on this.
