+++
id = "k4d5b0a3ae0"
kind = "layout"
summary = "benchmarks/results/ holds 177 asv JSON files: the benchmarks.json index and per-machine run results under darrenburns-2022-mbp/."
paths = ["benchmarks/results", "examples/columns.py"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T05:47:34+00:00"
last_verified = "2026-11-13T05:47:34+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k222cb40631"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "checked against aa3a3c47b423"
+++
benchmarks/results/ holds 177 .json files. benchmarks.json is the asv benchmark index. Per-run results live under darrenburns-2022-mbp/ and have the shape {commit_hash, env_name, date, params, python, requirements, env_vars, result_columns, results, durations, version}. No code reads these files; examples/columns.py does not refer to them.
