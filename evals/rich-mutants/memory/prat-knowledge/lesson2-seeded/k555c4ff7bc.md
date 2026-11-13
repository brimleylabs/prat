+++
id = "k555c4ff7bc"
kind = "pitfall"
summary = "Coverage omits some modules and excludes pragma/TYPE_CHECKING lines"
paths = [".coveragerc"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:49+00:00"
last_verified = "2026-11-13T12:13:49+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
.coveragerc omits certain files and excludes `# pragma: no cover` and TYPE_CHECKING blocks. That is why __main__ demos carry the pragma. Do not rely on the coverage report to find untested demo code.
