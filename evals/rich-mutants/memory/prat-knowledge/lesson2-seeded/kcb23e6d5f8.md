+++
id = "kcb23e6d5f8"
kind = "pitfall"
summary = "Examples contain intentional errors and mutable-order behaviour"
paths = ["examples/exception.py", "examples/suppress.py", "examples/recursive_error.py", "examples/export.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:13:42+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
exception.py divides by zero and suppress.py runs `1 / 0` on purpose, to show tracebacks. suppress.py also refers to an undefined `name` after that line. recursive_error.py recurses forever to show `max_frames`. export.py calls `print_table()` twice because `export_text()` clears the buffer, whereas `clear=False` keeps it. Do not 'fix' these without checking what the demo is showing.
