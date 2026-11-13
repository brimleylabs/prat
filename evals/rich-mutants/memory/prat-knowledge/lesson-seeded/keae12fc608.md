+++
id = "keae12fc608"
kind = "command"
summary = "Pages tell readers to run `python -m rich.<module>` demos; these entry points must keep working"
paths = ["docs/source/introduction.rst", "docs/source/live.rst", "docs/source/layout.rst", "docs/source/progress.rst", "docs/source/columns.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:12+00:00"
last_verified = "2026-11-13T05:41:12+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The guide points to module self-demos: `python -m rich`, rich.status, rich.spinner, rich.json, rich.live, rich.layout, rich.progress, rich.markup, rich.emoji, rich.pretty, rich.repr, rich.theme, rich.default_styles, rich.traceback, rich.prompt, rich.tree, and rich.markdown and rich.syntax with -h. Removing or renaming a `__main__` block in the corresponding rich/ module makes the docs wrong. The pages also link to examples/*.py (columns.py, fullscreen.py, table_movie.py, downloader.py, tree.py and others) via GitHub URLs on the willmcgugan/rich master branch.
