+++
id = "kd79d19d2ce"
kind = "command"
summary = "PRAT test gate: python -m pytest -q -p no:cacheprovider tests"
paths = [".prat/project.toml", "tests/pytest.ini", "pyproject.toml"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:24+00:00"
last_verified = "2026-11-13T05:41:24+00:00"
invalid_at = "2026-11-13T06:50:31+00:00"
superseded_by = "kc08d44001d"

[[sources]]
note = "deep scan, 20261113T053836"
+++
.prat/project.toml defines `test` as this command, with `{python}` as the interpreter and a 300-second verify timeout. Per the config notes it runs about 1,000 tests in a few seconds. pytest settings are in tests/pytest.ini and pyproject.toml.
