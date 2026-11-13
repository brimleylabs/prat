+++
id = "k1f1ba4cb9a"
kind = "pitfall"
summary = "Console output is buffered; writes only reach the file when the buffer index returns to 0"
paths = ["rich/console.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:43+00:00"
last_verified = "2026-11-13T12:13:43+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`print`, `log` and `control` run inside `with self:`, which calls `_enter_buffer` and `_exit_buffer`. `_check_buffer` and `_write_buffer` write only when the thread-local `_buffer_index` is 0. They also copy the buffer into `_record_buffer` when `record=True`, and branch for Jupyter, legacy Windows and modern Windows (writes are batched to 32KB/4 characters because of a CPython bug). Capture and pager contexts reuse the same mechanism. Nested `with console:` blocks defer output, and `quiet=True` discards the buffer.
