+++
id = "k6bf5b55d2e"
kind = "command"
summary = "Build docs from docs/ with `make html` (or `make.bat html` on Windows); Sphinx and the theme packages must already be installed, since agents cannot pip install at medium autonomy."
paths = ["docs/Makefile", "docs/make.bat", "docs/requirements.txt"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T06:49:52+00:00"
last_verified = "2026-11-13T06:49:52+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k3b09cdf287"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "checked against d5ff7b5fc316"
+++
Run `make html` (or `make.bat html` on Windows) in docs/. Any target is passed to `sphinx-build -M <target> source build`, plain `make` shows Sphinx help, and `SPHINXBUILD`/`SPHINXOPTS`/`O` can be overridden (e.g. `make html O=-W` to fail on warnings). The pinned dependencies are in docs/requirements.txt, but `pip install` is denied at medium autonomy, so if sphinx-build is missing, report that instead of trying to install it.
