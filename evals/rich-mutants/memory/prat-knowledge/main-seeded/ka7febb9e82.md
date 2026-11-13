+++
id = "ka7febb9e82"
kind = "pitfall"
summary = "Some statements in the docs may be stale relative to pyproject.toml"
paths = ["docs/source/introduction.rst", "docs/source/conf.py", "docs/source/progress.rst"]
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
introduction.rst says Rich requires Python 3.8.0 and above, but pyproject.toml declares Python >=3.10. Several pages also link to examples at github.com/willmcgugan/rich/blob/master/..., and conf.py's intersphinx_mapping uses an http:// URL. Check any version or URL claim against the repo before relying on it or copying it into a new page.
