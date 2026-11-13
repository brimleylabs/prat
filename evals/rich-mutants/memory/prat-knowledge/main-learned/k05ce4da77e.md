+++
id = "k05ce4da77e"
kind = "convention"
summary = "Every renderable follows the same protocol"
paths = ["rich/console.py", "rich/abc.py", "rich/jupyter.py", "rich/panel.py", "rich/align.py"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 5
hurt = 0
created = "2026-11-13T05:38:27+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = "2026-11-13T05:48:11+00:00"
superseded_by = "ke16d6fbeb8"

[[sources]]
note = "deep scan, 20261113T053544"
+++
A renderable implements __rich_console__(console, options), which yields Segments or renderables, and optionally __rich_measure__, which returns a Measurement. Most renderables also mix in JupyterMixin. Console types are imported only under TYPE_CHECKING to avoid import cycles.
