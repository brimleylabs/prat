+++
id = "ka02d0edfb4"
kind = "pitfall"
summary = "SegmentSuite.test_divide_complex is never timed"
paths = ["benchmarks/benchmarks.py", "benchmarks/results/benchmarks.json"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:13+00:00"
last_verified = "2026-11-13T05:38:13+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The method is named `test_*` rather than `time_*`, so asv will not collect it, and it is absent from the results JSON. To benchmark `Segment.divide`, rename it to `time_divide_complex`.
