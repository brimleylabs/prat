+++
id = "k2a1bed144d"
kind = "decision"
summary = "__rich_repr__ yields tuples in three shapes, and @rich.repr.auto generates __repr__ from it"
paths = ["rich/repr.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:18+00:00"
last_verified = "2026-11-13T05:41:18+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
A `__rich_repr__` result may contain bare values, `(key, value)`, or `(key, value, default)`, and the last form is omitted when `value == default`. The `angular` attribute on the method switches output to `<Name ...>`. `repr.auto` builds `__rich_repr__` from `__init__`'s signature when the class lacks one, and always overwrites `__repr__`. Note that `rich_repr(cls)` ignores `angular` when applied without parentheses.
