+++
id = "ka7aefb2f2a"
kind = "pitfall"
summary = "rich/ has circular imports that are broken by lazy imports"
paths = ["rich/console.py", "rich/__init__.py", "rich/_extension.py", "rich/containers.py", "rich/jupyter.py"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:27+00:00"
last_verified = "2026-11-13T05:38:27+00:00"
invalid_at = "2026-11-13T05:48:10+00:00"
superseded_by = "kcc7de45d6c"

[[sources]]
note = "deep scan, 20261113T053544"
+++
console.py, text.py, pretty.py, traceback.py, status.py, live.py, jupyter.py and __init__.py import each other in a cycle. The cycle is broken with function-level imports and TYPE_CHECKING-only imports. If you move one of these imports to module level, `import rich` can fail.
