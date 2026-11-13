+++
id = "k8f55edc5ce"
kind = "pitfall"
summary = "Docs drift from code: hand-written raw HTML <pre> output blocks in docs/source (console.rst, layout.rst) don't update with behaviour; README.md says Python 3.8 while pyproject requires >=3.10."
paths = ["docs/source/console.rst", "docs/source/layout.rst", "README.md", "docs/source/reference"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T12:20:45+00:00"
last_verified = "2026-11-13T12:20:45+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k8e224e9358"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "checked against 629ab83c5fe8"
+++
Console and layout output in docs/source is hand-written raw HTML <pre> and does not update when behaviour changes. The English README still says 'Rich requires Python 3.8 or later', but pyproject.toml requires Python >=3.10. Translated READMEs are separate hand copies and can be stale. Reference pages under docs/source/reference fail if a rich.* module path stops importing.
