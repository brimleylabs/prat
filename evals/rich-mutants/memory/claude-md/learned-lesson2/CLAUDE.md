# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Rich is a Python library (>=3.10) for rich text and formatting in the terminal. The package is `rich/`, tests are in `tests/`, docs (Sphinx) in `docs/`. Packaging and dependencies are managed with `uv` (`uv sync` creates `.venv`). Runtime deps are only `markdown-it-py` and `pygments`.

## Commands

- Run all tests (with coverage): `make test` (sets `TERM=unknown`, runs `uv run pytest --cov-report term-missing --cov=rich tests/ -vv`)
- Tests without coverage: `make test-no-cov`
- Single test: `uv run pytest tests/test_table.py::test_render_table -vv`
- Without make/uv: `python -m pytest -q -p no:cacheprovider tests` (the command in `.prat/project.toml`; ~1,000 tests, a few seconds)
- Format: `make format` (black); check only: `make format-check`
- Type check (mypy strict, `rich` package only): `make typecheck`
- Docs: `make docs` (output at `docs/build/html`)
- tox envs: `lint` (format-check + typecheck), `docs`, `py310`–`py315`

Test output is sensitive to the terminal: `TERM=unknown` is used by the Makefile, and `tests/conftest.py` strips `FORCE_COLOR`/`NO_COLOR`. Most tests build a `Console(file=io.StringIO(), force_terminal=True, width=..., color_system=...)` and compare exact output strings, so set these explicitly in new tests.

## Before committing (per CONTRIBUTING.md)

- Tests pass, `make typecheck` passes (strict mypy, all new code needs annotations), code is `black`-formatted (isort with black profile via pre-commit).
- Add an entry to `CHANGELOG.md` under "Unreleased" (Keep a Changelog format); first-time contributors add themselves to `CONTRIBUTORS.md`.
- Docstrings for new code; avoid abbreviations in names.
- AI-generated PRs must follow `AI_POLICY.md`: fill in the PR template, identify the agent used, and link a maintainer-approved issue/discussion.

## Architecture

The core is a render protocol built around `Console` (`rich/console.py`):

- Any object can be printed if it implements `__rich_console__(console, options) -> RenderResult` (yielding `Segment`s or other renderables) and optionally `__rich_measure__(console, options) -> Measurement` (min/max width, see `measure.py`). `__rich__` can return a renderable (or str) for simple casting. Protocols are defined in `console.py` (`ConsoleRenderable`, `RichCast`).
- `Console.render()` recursively expands renderables into a flat stream of `Segment`s (`segment.py`: text + `Style`, plus control codes). `ConsoleOptions` carries width/height/justify/etc. down the tree. Helpers in `segment.py` (split lines, set shape, crop, adjust line length) are how containers like `Panel`, `Table`, `Layout`, `Padding` compose children.
- Output goes through `Console._write_buffer`/`_render_buffer`, which converts `Style`s to ANSI based on the detected `color_system` (`color.py`, `terminal_theme.py`, `_palettes.py`), with legacy Windows handled by `_windows*.py`/`_win32_console.py`, and Jupyter by `jupyter.py`.
- Text pipeline: `markup.py` (`[bold]...[/bold]` BBCode-like syntax) → `Text` (`text.py`, spans + wrapping via `_wrap.py`/`cells.py` for display-width) → `Segment`s. `highlighter.py` and `pretty.py`/`repr.py` apply regex highlighting and pretty-printing. Styles are resolved by name through `Theme`/`default_styles.py`.
- Higher-level widgets built on the protocol: `table`, `panel`, `tree`, `columns`, `layout`, `live`/`live_render`/`status`/`spinner`, `progress`/`progress_bar`, `syntax` (pygments), `markdown` (markdown-it-py), `traceback`, `logging` (`RichHandler`), `prompt`, `inspect` (`_inspect.py`).
- `rich/__init__.py` exposes the global console and the drop-in `rich.print`/`rich.inspect`; `__main__.py` is the `python -m rich` demo.
- Display-width logic lives in `cells.py` with generated tables in `_unicode_data/` — don't hand-edit those.
- `_emoji_codes.py`, `_spinners.py`, `_palettes.py` are large data files.

`tests/_card_render.py` and `tests/render.py` are test helpers (not tests); `tests/test_card.py` uses the former.

## Gotchas

- SVG export: the template is `rich/_export_format.py`; the values are computed at the end of `Console.export_svg()`. The clip rect uses `terminal_width=char_width * width - 1` and `terminal_height=(y + 1) * line_height - 1` (both deliberately minus one); the outer chrome rect uses separate `terminal_width`/`terminal_height` local names that include padding, so don't conflate the two.
- On a clean checkout (Windows), 8 tests already fail, unrelated to any change you make: `test_card_render`, `test_inline_code_in_table_cells`, and six in `test_syntax.py`. Compare against `git stash` before assuming you broke them.
- `EXPECTED_SVG` in `tests/test_console.py` is not used by any test, so it doesn't catch SVG regressions. Assert on the specific attribute you changed.
- Every bug fix needs a regression test that fails without the fix and passes with it, marked `@pytest.mark.regression` (the regression job selects `-m regression`). The marker is registered in `tests/pytest.ini`, **not** `pyproject.toml`: `tests/pytest.ini` takes precedence, so `[tool.pytest.ini_options]` in `pyproject.toml` is ignored when running from the repo root. Verify a new test by reverting the fix and confirming it fails.
