+++
id = "kbd5c0eee50"
kind = "layout"
summary = "benchmarks: The benchmarks module holds the Airspeed Velocity (asv) performance benchmarks for Rich"
paths = ["benchmarks"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:39+00:00"
last_verified = "2026-11-13T12:13:39+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
The benchmarks module holds the Airspeed Velocity (asv) performance benchmarks for Rich. `benchmarks/benchmarks.py` defines the suites and `benchmarks/snippets.py` holds the shared input data (Python source, a nested dict, Lorem Ipsum, Japanese text, markup). The suites exercise Text, Syntax, Table, Pretty, Style, Color and Segment from `rich/`, always against a Console that writes to an in-memory StringIO. `benchmarks/results/benchmarks.json` is asv's generated index of the discovered benchmarks. The per-machine result files sit in sibling directories under `benchmarks/results/`. `benchmarks/README.md` points to the root `asv.conf.json` and `asvhashfile`, which I did not read, and to the published dashboard repo. Nothing in the library imports this module.
