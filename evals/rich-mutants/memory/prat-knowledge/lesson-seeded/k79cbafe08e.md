+++
id = "k79cbafe08e"
kind = "pitfall"
summary = "Public API changes ripple into docs, examples and benchmarks that have no tests"
paths = ["docs/source/reference", "docs/source", "examples", "benchmarks/benchmarks.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:26+00:00"
last_verified = "2026-11-13T05:41:26+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The docs reference pages autodoc rich modules, and the user guide tells readers to run `python -m rich.<module>` demos. Examples and benchmarks import the public API directly. When you rename or remove something, grep docs/, examples/ and benchmarks/ too.
