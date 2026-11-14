"""A copy of a plain PostgreSQL database per card, against a real server.

Runs when PRAT_TEST_POSTGRES_URL points at a PostgreSQL server (for example
``postgresql+psycopg://postgres:password@127.0.0.1:5432``); each test makes its own
project database there and drops it afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from conftest import TEST_POSTGRES_URL
from prat.projects import DatabaseWorkspace
from prat.workspace.postgres_copy import PostgresCopyProvider, copy_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_POSTGRES_URL, reason="set PRAT_TEST_POSTGRES_URL to PostgreSQL"),
]


def admin(sql: str) -> None:
    engine = create_engine(f"{TEST_POSTGRES_URL}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture
def provider() -> Iterator[PostgresCopyProvider]:
    name = f"prat_copy_{uuid.uuid4().hex[:8]}"
    admin(f'CREATE DATABASE "{name}"')
    engine = create_engine(f"{TEST_POSTGRES_URL}/{name}", isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE items (id int PRIMARY KEY)"))
        connection.execute(text("INSERT INTO items VALUES (1), (2)"))
    engine.dispose()
    spec = DatabaseWorkspace(kind="postgres", url_env="APP_DB_URL")
    found = PostgresCopyProvider(spec, f"{TEST_POSTGRES_URL}/{name}")
    try:
        yield found
    finally:
        for card_id in (1, 2):
            admin(f'DROP DATABASE IF EXISTS "{copy_name(name, card_id)}" WITH (FORCE)')
        admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_a_card_gets_a_copy_with_the_data_and_loses_it_on_discard(
    provider: PostgresCopyProvider,
) -> None:
    provider.create(1)
    assert provider.exists(1)
    engine = create_engine(provider.branch_url(1))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM items")).scalar() == 2
    engine.dispose()
    provider.create(1)  # a second prepare of the same card keeps its copy
    assert provider.discard(1)
    assert not provider.exists(1)
    assert not provider.discard(1)
