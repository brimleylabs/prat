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

## Gotchas

- `rich/cells.py`: `get_character_cell_size` treats codepoints 0-31 and 0x7F-0x9F as zero width (NUL included). The `_cell_len` fast path only applies to printable ranges, so control chars always hit this function.
- Cursor coordinates: `Control.move_to(x, y)` takes 0-based column/row and emits 1-based `\x1b[{y+1};{x+1}H` (the escape-code table near the top of `rich/control.py`). The control tuple stores the raw 0-based `(CURSOR_MOVE_TO, x, y)`. `_windows_renderer.py` subtracts 1 from those stored values as if they were 1-based, so legacy Windows is likely off by one (not changed; its test pins `(20, 30)` -> `row=29, col=19`). `move_to_column` already does `param+1` correctly.
- On a clean checkout, 6 tests already fail (`test_markdown.py::test_inline_code_in_table_cells` and 5 in `test_syntax.py`, likely pygments-version related). Compare against `git stash` before assuming a regression.
- `rich/containers.py` `Lines.justify` (`justify="full"`): extra spaces are handed out one per gap, cycling from the last gap backwards, so the rightmost gaps get the remainder. The cycling index must advance (`index + 1`); a stale `index` once piled everything into one gap. Tests in `tests/test_containers.py` should check gap sizes differ by at most one, not just total width.
- `rich/markup.py` `_parse` yields `(position, text, tag)`. For text, `position` is the offset of the *following* tag (or the text start for trailing text), not where the text begins. Text is only yielded when non-empty (`start > position`); don't assert text positions as start offsets in tests.
- `rich/pretty.py` `traverse()._traverse`: every branch (rich_repr, attrs, dataclass, namedtuple, containers) must recurse with `depth=depth + 1`, or `max_depth` stops working for that type. The attrs branch once passed `depth=depth`. When adding a `max_depth` test for one object kind, add the same nested Foo/Bar/Foo case for the others (see `test_max_depth_*` in `tests/test_pretty.py`).
- `rich/syntax.py` `_apply_stylized_ranges`: `newlines_offsets` holds the *start offset of each line* (0, then `match.start() + 1` per newline, then `len(code) + 1`), and `_get_code_index_for_syntax_position` relies on that (`line_length = next - cur - 1`). Storing the newline's own index shifted every line after the first one column right. Tests should style ranges on lines 2+, not only line 1 (see `test_stylize_range_multiline_columns`).
