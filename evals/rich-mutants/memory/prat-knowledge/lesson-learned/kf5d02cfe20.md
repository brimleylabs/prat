+++
id = "kf5d02cfe20"
kind = "pitfall"
summary = "Style is an immutable, heavily cached, slot-based object; build copies by hand-setting every slot"
paths = ["rich/style.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:17+00:00"
last_verified = "2026-11-13T05:41:17+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Style` uses `__slots__` and lru_cache on `parse`, `_add`, `normalize` and `get_html_style`. Its copy-style helpers (`copy`, `update_link`, `without_color`, `clear_meta_and_links`, `from_color`, `from_meta`) assign every slot by hand. A new slot must be added to `__slots__`, `_hash_getter`, `__init__` and each of those helpers, or caching and equality break. Equality is hash-based, via `_hash_getter` over color, bgcolor, attributes, set-attributes, link and meta. `__add__` copies the result when it has a link so each gets a distinct `link_id`.
