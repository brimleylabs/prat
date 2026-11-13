+++
id = "kdaa821dde4"
kind = "layout"
summary = "benchmarks: The benchmarks module holds Rich's performance benchmarks, written for Airspeed Velocity (`asv`)"
paths = ["benchmarks"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:12+00:00"
last_verified = "2026-11-13T05:38:12+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The benchmarks module holds Rich's performance benchmarks, written for Airspeed Velocity (`asv`). `benchmarks/benchmarks.py` defines one suite class per area: Text, Syntax wrapping, Table, Pretty, Style, Color, and Segment. Each class imports its inputs from `benchmarks/snippets.py`, which holds the shared sample data: Python code, a dict, Lorem ipsum, Japanese text and markup. `benchmarks/results/benchmarks.json` is an asv-generated index of the benchmark names, their source code and their timing settings. The suites import `rich.color`, `rich.console`, `rich.pretty`, `rich.segment`, `rich.style`, `rich.syntax`, `rich.table` and `rich.text`. The README links to `asv.conf.json` and `asvhashfile` at the repo root, and describes publishing results to the separate rich-benchmarks dashboard repo.
