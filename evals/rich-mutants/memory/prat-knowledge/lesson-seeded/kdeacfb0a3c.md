+++
id = "kdeacfb0a3c"
kind = "command"
summary = "Running and publishing benchmarks with asv"
paths = ["benchmarks/README.md"]
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
`asv run` benchmarks the main branch, and `asv run HEAD^!` benchmarks only your latest commit. `asv publish` writes the static site to `benchmarks/html`, and `asv preview` serves it locally. To update the website, run `asv run HASHFILE:asvhashfile` with the tags listed in the root `asvhashfile`, then copy the generated HTML into the `rich-benchmarks` repo.
