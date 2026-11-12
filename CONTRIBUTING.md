# Contributing to PRAT

PRAT is pre-release. Issues and focused pull requests are welcome; for anything large, open an
issue first so the design can be discussed.

## Setup

You need Python 3.12 or newer and git on `PATH`.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .venv\Scripts\python.exe
```

Call tools through the interpreter (`python -m pytest`, `python -m ruff`, `python -m mypy`).
This works the same everywhere and avoids problems on systems that block unsigned console
script launchers.

## Running the checks

```bash
.venv/bin/python -m pytest                       # all tests
.venv/bin/python -m pytest tests/unit            # fast unit tests only
.venv/bin/python -m pytest -m integration        # tests that create real git repositories
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy                         # strict, on src/prat
```

All four must pass before a change is merged. The integration tests create temporary
repositories and run whole cards through the engine with the scripted fake runtime, so they need
no network and no API key. They take a minute or two, mostly spent in git.

Tests marked `e2e` run a real card against a model and cost money. They are skipped unless
`PRAT_E2E=1`; they use Haiku for every role with a $1 budget, and authenticate with
`ANTHROPIC_API_KEY` (or your own Claude Code login with `PRAT_E2E_AUTH=claude_login`):

```bash
PRAT_E2E=1 .venv/bin/python -m pytest -m e2e
```

## Writing tests

- New behaviour in the engine needs an integration test in `tests/integration/test_lifecycle.py`
  driven by a `FakeRuntime` script. Use `add_card`, `make_engine` and the fixtures in
  `tests/conftest.py`.
- New policy rules need parametrized cases in `tests/unit/test_policy.py`, both the allowed and
  the denied side, and at every autonomy level where the answer changes.
- Changes to the Claude runtime adapter are tested with the fake SDK client in
  `tests/sdk_fakes.py`, which speaks the SDK's real message types and calls the adapter's
  `PreToolUse` hook the way Claude Code does. Engine flows that need several agent roles use the
  `RoleScripts` helper in `tests/integration/test_agent_flow.py`.
- Tests must pass on Linux, macOS and Windows. Use `pathlib`, build commands as argument lists,
  use `sys.executable` rather than `python`, and use `make_dir_link` for symlink cases (it falls
  back to a junction on Windows).
- Never put a real credential in a test. Build credential-shaped samples at runtime, for example
  `"AKIA" + "Q" * 16`, so the repository itself never contains one.

## Database changes

Change the models in `src/prat/db/models.py`, then generate a migration against an empty
development database:

```bash
.venv/bin/python -m alembic revision --autogenerate -m "describe the change"
```

Review the generated file in `src/prat/db/alembic/versions/`, keep it portable (no
SQLite-only or Postgres-only SQL), and run the tests: `test_migrations_match_models` fails if
the migrations and the models disagree.

## Style

- Small modules with one responsibility each; keep `domain`, `paths` and `model_registry` free
  of PRAT imports (`config` may import those two).
- Prompts live in `src/prat/engine/prompts.py` as pure functions.
- Status changes only through the model attributes, so the state machines in `domain.py` apply.
- Subprocesses always as argument lists, never `shell=True`.
- Plain, specific prose in docs and messages.
