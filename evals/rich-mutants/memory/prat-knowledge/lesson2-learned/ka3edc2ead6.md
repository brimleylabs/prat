+++
id = "ka3edc2ead6"
kind = "convention"
summary = "Renderables like Align and Bar extend JupyterMixin and implement __rich_console__ and __rich_measure__; Inspect uses __rich__ instead, and __main__ demo blocks should be excluded from coverage."
paths = ["rich/align.py", "rich/bar.py", "rich/_inspect.py", "rich/__main__.py", "rich/abc.py"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T12:20:47+00:00"
last_verified = "2026-11-13T12:20:47+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k3adbaed85b"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "checked against 629ab83c5fe8"
+++
`Align` and `Bar` extend `JupyterMixin` and implement `__rich_console__(console, options)` yielding `Segment`s, plus `__rich_measure__` returning a `Measurement`. `Inspect` instead implements `__rich__`, returning a `Panel` built with `Panel.fit`. `Align` renders its child at the measured width through `Constrain`, then pads using `Segment.get_shape` and `Segment.set_shape`. Many modules end with `if __name__ == "__main__":` demo code, and `python -m rich` runs `rich/__main__.py`. New demo blocks should carry `# pragma: no cover`, though not every existing one does (e.g. rich/_windows.py).
