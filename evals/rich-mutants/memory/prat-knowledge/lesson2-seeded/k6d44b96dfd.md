+++
id = "k6d44b96dfd"
kind = "layout"
summary = "Packaging uses uv_build with the module at the repo root"
paths = ["pyproject.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:38+00:00"
last_verified = "2026-11-13T12:13:38+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`build-backend = "uv_build"` with `module-root = ""`, so the `rich/` package sits at the repo root, not under `src/`. `.DS_Store` and `*.ipynb` files are excluded from both sdist and wheel. Runtime dependencies are only `markdown-it-py>=2.2.0` and `pygments>=2.13.0,<3.0.0`. `ipywidgets` is the optional `jupyter` extra. Dev tools live in `[dependency-groups].dev`, not in extras.
