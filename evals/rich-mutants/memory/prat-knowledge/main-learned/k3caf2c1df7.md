+++
id = "k3caf2c1df7"
kind = "pitfall"
summary = "asv.conf.json looks stale and does not match the current build setup"
paths = ["asv.conf.json", "asvhashfile", "benchmarks/results"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:12+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Its build_command runs `pip install poetry` and `python setup.py build`, but pyproject.toml uses uv_build and the repo has no poetry config. It tracks branch `master` while the repo's main branch is `main`, pins `pythons` to 3.10 and `setuptools` to 59.2.0, and allows an install_timeout of 180s. asvhashfile lists release tags (v8.0.0 to v12.5.0, unsorted as text) to benchmark. Check that benchmark runs actually work before relying on this config. Results land in benchmarks/results.
