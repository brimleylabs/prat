+++
id = "k22ddbf74ee"
kind = "command"
summary = "How to regenerate the golden outputs that the tests compare against."
paths = ["tests/_card_render.py", "tests/test_card.py", "tests/test_bar.py", "tests/test_columns.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:45+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`tests/_card_render.py` is a single generated line, `expected = <repr>`, that test_card.py compares with `make_test_card()` from `rich/__main__.py`. test_card.py rewrites it when run as `__main__`, and it opens the bare path `_card_render.py`, so the working directory must be `tests/`. test_bar.py, test_block_bar.py, test_columns.py, test_columns_align.py, test_log.py and test_markdown*.py also have `if __name__ == "__main__"` blocks that print the current `repr` output for pasting into the expected values. Do not hand-edit `_card_render.py`. Any change to `rich/__main__.py`, themes or highlighters can invalidate it.
