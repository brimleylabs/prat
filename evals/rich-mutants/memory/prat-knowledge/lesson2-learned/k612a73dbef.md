+++
id = "k612a73dbef"
kind = "layout"
summary = "conf.py configures Sphinx; the release version comes from the installed rich package metadata."
paths = ["docs/source/conf.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:40+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
conf.py sets release = Distribution.from_name('rich').version, so rich must be installed in the build environment or the build fails. It uses sphinx_rtd_theme, autodoc_typehints = 'description', an intersphinx mapping to the Python docs, and Fira Code loaded from a CDN. Static files are expected under _static and templates under _templates.
