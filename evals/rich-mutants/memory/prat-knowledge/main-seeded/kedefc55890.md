+++
id = "kedefc55890"
kind = "pitfall"
summary = "test_logging.py configures the global logging root and adds handlers that are mostly never removed"
paths = ["tests/test_logging.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:24+00:00"
last_verified = "2026-11-13T05:38:24+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
At import time the module calls logging.basicConfig(level="NOTSET", handlers=[handler]) with a module-level RichHandler, and the tests add more handlers to the shared `rich` logger with log.addHandler. Only test_stderr_and_stdout_are_none removes its handler. Handlers therefore accumulate across tests, so assertions should read each handler's own console file and not rely on global state.
