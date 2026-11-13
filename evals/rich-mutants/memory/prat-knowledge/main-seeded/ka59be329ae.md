+++
id = "ka59be329ae"
kind = "decision"
summary = "Width table rows are (start, end, width) with wide East Asian ranges as 2 and zero-width ranges as 0"
paths = ["tools/make_width_tables.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:26+00:00"
last_verified = "2026-11-13T05:38:26+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The table is sorted tuples. The VS16 narrow-to-wide set is built only from the wcwidth VS16_NARROW_TO_WIDE['9.0.0'] entry and is shared by every version's table. Versions missing from WIDE_EASTASIAN or ZERO_WIDTH fall back to an empty list for WIDE_EASTASIAN and ZERO_WIDTH.
