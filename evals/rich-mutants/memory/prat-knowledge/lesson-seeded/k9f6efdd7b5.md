+++
id = "k9f6efdd7b5"
kind = "pitfall"
summary = "Benchmarks.json is generated and its code snapshots lag the source"
paths = ["benchmarks/results/benchmarks.json"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:09+00:00"
last_verified = "2026-11-13T05:41:09+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`benchmarks/results/benchmarks.json` is asv output with a top-level `"version": 2`; don't hand-edit it. Its stored code for the Text, Syntax, Table and Pretty suites predates the current source: the Syntax and TextHotCache consoles have no `width=100`, and the Pretty setup differs in places. Regenerate it with asv rather than patching it.
