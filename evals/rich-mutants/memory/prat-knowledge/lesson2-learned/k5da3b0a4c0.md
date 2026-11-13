+++
id = "k5da3b0a4c0"
kind = "pitfall"
summary = "Live and FileProxy: stdout redirection, nesting and cursor control are easy to break"
paths = ["rich/live.py", "rich/live_render.py", "rich/file_proxy.py", "rich/console.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T12:13:43+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`Live` registers itself as a `RenderHook`. `process_renderables` wraps output with `LiveRender.position_cursor()` and appends the live renderable. It does this only when `console.is_interactive`, or when the live has stopped and is not transient. `Console.set_live` returns False for a nested `Live`, which then defers to `_live_stack[0]` in `refresh` and `renderable`. `_enable_redirect_io` swaps `sys.stdout` and `sys.stderr` for `FileProxy` only when the console is a terminal or Jupyter. `Live.stop` sets `vertical_overflow` to `"visible"` and does a final refresh. A refresh thread (`_RefreshThread`) runs under `_lock`.
