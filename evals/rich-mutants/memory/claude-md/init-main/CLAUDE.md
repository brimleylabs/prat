# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Rich is a Python library (>=3.10) for rich text, tables, progress bars, syntax highlighting, markdown, tracebacks, etc. in the terminal. Runtime deps are only `markdown-it-py` and `pygments`. Packaging and dev environment use `uv` (`uv sync` creates `.venv`).

## Commands

```
make test                 # pytest with coverage (sets TERM=unknown, runs via uv)
make test-no-cov          # pytest without coverage
make format-check         # black --check .
make format               # black .
make typecheck            # mypy -p rich --no-incremental (strict mode)
make docs                 # Sphinx docs -> docs/build/html (needs docs/requirements.txt)
```

Run a single test: `TERM=unknown uv run pytest tests/test_table.py::test_name -vv`. Setting `TERM=unknown` matters: many tests assert on exact output, which depends on terminal detection. `tests/conftest.py` also clears `FORCE_COLOR`/`NO_COLOR`.

Before committing: tests pass, `mypy` strict passes, code formatted with `black` (isort with the black profile is also in pre-commit). New user-facing changes need a `CHANGELOG.md` entry (under "Unreleased", Keep a Changelog format), and docs in `docs/` where relevant.

## Architecture

All library code is in the `rich/` package (flat module layout, `py.typed`). Core rendering pipeline:

- `console.py` (`Console`) is the central object. It owns the output file, detects terminal capabilities (color system, width, Jupyter, legacy Windows), handles themes, markup, highlighting, capture/export, and `print`/`log`/`rule`/`status`.
- **Console protocol**: any renderable implements `__rich_console__(console, options) -> RenderResult` (yielding `Segment`s or other renderables) and optionally `__rich_measure__(console, options) -> Measurement` for width negotiation. Objects can also expose `__rich__`. See `protocol.py`, `measure.py`, `abc.py`. `ConsoleOptions` carries per-render constraints (max width, height, justify, etc.).
- `segment.py` (`Segment`: text plus `Style`) is the low-level unit; `console.py` renders everything down to segment lists, then to ANSI strings. `style.py`, `color.py`, `theme.py`/`default_styles.py`, `terminal_theme.py`, and `palette.py` handle styling and color-system downgrading.
- `text.py` (`Text`) is styled text with spans; `markup.py` parses `[bold]...[/]` into `Text`; `highlighter.py` and `pretty.py`/`repr.py` produce styled reprs.
- Higher-level renderables build on those: `table`, `panel`, `layout`, `columns`, `tree`, `syntax`, `markdown`, `traceback`, `progress`/`progress_bar`, `live`/`live_render`/`status`/`spinner`, `prompt`, `logging` (`RichHandler`), `json`, `align`/`padding`/`constrain`.
- `cells.py` and `_unicode_data/` (one generated module per Unicode version) compute character display width; be careful when changing cell-width behavior.
- Platform and environment shims: `_windows.py`, `_win32_console.py`, `_windows_renderer.py`, `jupyter.py`, `_fileno.py`, `_null_file.py`.
- `rich/__init__.py` exposes the global-console helpers (`print`, `print_json`, `inspect`, `get_console`, `reconfigure`); `python -m rich` (`__main__.py`) renders a demo.

Underscore-prefixed modules are private internals.

## Tests

`tests/` has one `test_<module>.py` per module, mostly asserting exact rendered output, often with a `Console(file=io.StringIO(), force_terminal=True, width=..., color_system=...)`. Shared helpers: `tests/render.py` (render to string) and `tests/_card_render.py`.

## Contributions and AI

`AI_POLICY.md`: AI-generated PRs must fill in the PR template, identify themselves as AI-generated (naming the agent), and link to an issue or discussion where a maintainer approved the solution. `benchmarks/` (asv) is excluded from pre-commit and formatting hooks.
