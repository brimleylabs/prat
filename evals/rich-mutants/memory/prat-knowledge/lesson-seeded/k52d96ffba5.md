+++
id = "k52d96ffba5"
kind = "pitfall"
summary = "SegmentSuite.test_divide_complex is never run by asv"
paths = ["benchmarks/benchmarks.py", "benchmarks/results/benchmarks.json"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:08+00:00"
last_verified = "2026-11-13T05:41:08+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
asv only collects methods prefixed with `time_`, but this method is named `test_divide_complex`. It is therefore not benchmarked and does not appear in `benchmarks/results/benchmarks.json`. Rename it to `time_divide_complex` if it should be measured.
