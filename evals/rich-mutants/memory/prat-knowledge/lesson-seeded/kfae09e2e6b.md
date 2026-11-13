+++
id = "kfae09e2e6b"
kind = "layout"
summary = "(root) (part 2): This part of the repo root holds Rich's packaging and tooling config plus the README set"
paths = ["README.it.md", "README.ja.md", "README.kr.md", "README.md", "README.pl.md", "README.pt-br.md", "README.ru.md", "README.sv.md", "README.tr.md", "README.zh-tw.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:06+00:00"
last_verified = "2026-11-13T05:41:06+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
This part of the repo root holds Rich's packaging and tooling config plus the README set. pyproject.toml defines the `rich` package (uv_build backend, Python >=3.10, runtime deps markdown-it-py and pygments, a `dev` dependency group, strict mypy on `rich/`, pytest on `tests/`). tox.ini runs pytest across py310–py315 and has `lint` and `docs` environments. asv.conf.json, asvhashfile and make.bat support benchmarking and Sphinx docs. README.md is the English source, and ten translated READMEs (it, ja, kr, pl, pt-br, ru, sv, tr, zh-tw) mirror it. faq.yml configures FAQtory, which builds the FAQ from `questions/`.
