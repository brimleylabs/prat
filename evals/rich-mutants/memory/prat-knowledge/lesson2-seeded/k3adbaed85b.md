+++
id = "k3adbaed85b"
kind = "convention"
summary = "Renderable protocol and the `__main__` demo blocks"
paths = ["rich/align.py", "rich/bar.py", "rich/_inspect.py", "rich/__main__.py", "rich/abc.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:43+00:00"
last_verified = "2026-11-13T12:13:43+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Renderables here (`Align`, `Bar`, `Inspect`) extend `JupyterMixin` and implement `__rich_console__(console, options)` yielding `Segment`s, plus `__rich_measure__` returning a `Measurement`. `Align` renders its child at the measured width through `Constrain`, then pads using `Segment.get_shape` and `Segment.set_shape`. Many modules end with `if __name__ == "__main__":  # pragma: no cover` demo code, and `python -m rich` runs `rich/__main__.py`, which renders a feature test card and prints timing. Keep the demo blocks excluded from coverage.
