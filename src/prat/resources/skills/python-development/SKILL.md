---
name: python-development
description: Use whenever you write, change or test Python code: modules, scripts, FastAPI, Flask or Django apps, SQLAlchemy models, pytest suites, or Python dependency and virtualenv setup.
phase: build
tier: stack
---

# Python development

Target Python 3.12 idioms unless the project pins an older version
(`requires-python` in `pyproject.toml`, `.python-version`, CI config).

## Environment

- Use the project's own virtualenv interpreter: `.venv/bin/python` (or
  `.venv\Scripts\python.exe` on Windows). Do not use the system Python or
  `pip install --user`.
- Run tools through it: `.venv/bin/python -m pytest`, `-m ruff`, `-m mypy`.
- Detect the dependency manager from files: `uv.lock` (uv), `poetry.lock`
  (Poetry), `Pipfile.lock` (pipenv), `requirements*.txt` (pip). Use that one.
- Add a dependency only if the card needs it. Declare it in the project file,
  not just in your venv.

## Code style

- Type hints on all public functions. Use built-in generics and `|`:
  `def load(ids: list[int]) -> dict[int, User | None]:`
- `pathlib.Path` for files, never string concatenation for paths.
- `logging.getLogger(__name__)`, not `print`, in library and app code.
  Use lazy formatting: `log.info("loaded %d rows", n)`.
- `@dataclass(slots=True, frozen=True)` for plain value objects; pydantic
  models where the project validates input with pydantic.
- f-strings for messages, never for SQL.
- Context managers for files, locks, sessions, connections.
- Raise specific exceptions; never bare `except:`. Catch `Exception` only at
  boundaries, and log with `log.exception(...)`.
- Follow the project's formatter and linter config (ruff, black, isort).

## Testing with pytest

```python
import pytest


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('name = "demo"\n')
    return p


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), (" 2 ", 2)])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


def test_rejects_garbage():
    with pytest.raises(ValueError, match="not a number"):
        parse_int("x")
```

- `tmp_path` for files, `monkeypatch` for env vars and attributes.
- Put shared fixtures in `conftest.py`, close to where they are used.
- Mock at the boundary (HTTP, clock, external services), not your own code.
- Run one test fast: `pytest tests/test_x.py::test_name -x -q`.

## SQLAlchemy 2.0 style

```python
from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)


with Session(engine) as session:
    user = session.scalars(select(User).where(User.email == email)).first()
```

- Use `select()`, not legacy `session.query()`, in new code (match the file
  if it is all legacy).
- Avoid N+1: `selectinload()` / `joinedload()` for relationships you iterate.
- Schema changes go through Alembic (see `safe-database-changes`).

## Framework pointers

- **FastAPI:** pydantic models for request and response, `Depends` for
  sessions and auth, `async def` only if everything inside is async. Test
  with `TestClient` or `httpx.AsyncClient`.
- **Flask:** app factory, blueprints, `app.test_client()` in tests.
- **Django:** `manage.py makemigrations` then `migrate`, `select_related` /
  `prefetch_related`, forms or serializers for validation, `manage.py test`
  or pytest-django per project setup.

## Common traps

- Mutable default arguments (`def f(x=[])`). Use `None` and create inside.
- Blocking calls (`requests`, `time.sleep`, sync DB drivers) inside
  `async def` handlers.
- Naive datetimes. Use `datetime.now(UTC)`.
- Import-time side effects (DB connections, reading env) that break tests.
- Shadowing stdlib modules with local file names (`json.py`, `types.py`).
- Catching an exception and returning `None`, hiding the real error.
- Tests that pass only in a specific order because of shared global state.
