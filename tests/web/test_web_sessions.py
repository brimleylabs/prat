"""Web sessions and used login codes are persisted, hashed, and survive a restart."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text, update

from prat.config import Settings
from prat.db import Database, LoginNonce, Project, WebSessionRecord
from prat.db.migrate import current_revision, upgrade
from prat.web.sessions import COOKIE_NAME, NONCE_GRACE, SessionStore, hash_secret
from prat.web.tokens import (
    check_login_code,
    ensure_access_token,
    fingerprint,
    make_login_code,
    rotate_access_token,
)
from web_support import ORIGIN, make_web

HOUR = 3600.0


def session_rows(db: Database) -> list[WebSessionRecord]:
    with db.session() as session:
        return list(session.scalars(select(WebSessionRecord)))


def nonce_rows(db: Database) -> list[LoginNonce]:
    with db.session() as session:
        return list(session.scalars(select(LoginNonce)))


def expire_all_sessions(db: Database) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    with db.session() as session:
        session.execute(update(WebSessionRecord).values(expires_at=past))


def stored_text(db: Database) -> str:
    """Every value of every table, and the raw bytes of a SQLite database, as text."""
    chunks: list[str] = []
    quote = db.engine.dialect.identifier_preparer.quote
    with db.engine.connect() as connection:
        for table in inspect(connection).get_table_names():
            for row in connection.execute(text(f"SELECT * FROM {quote(table)}")):  # noqa: S608
                chunks.extend(str(value) for value in row)
        if db.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    path = db.engine.url.database
    if db.engine.dialect.name == "sqlite" and path:
        for suffix in ("", "-wal"):
            try:
                with open(path + suffix, "rb") as handle:
                    chunks.append(handle.read().decode("latin-1"))
            except OSError:
                continue
    return "\n".join(chunks)


# The store ---------------------------------------------------------------------------------


def test_store_keeps_sessions_in_the_database(db: Database) -> None:
    store = SessionStore(db, HOUR)
    created = store.create("fp-1")
    assert len(store) == 1
    # A second store on the same database is what a restarted server has.
    restarted = SessionStore(db, HOUR)
    found = restarted.get(created.id, "fp-1")
    assert found is not None
    assert found.id == created.id
    assert found.csrf_token == created.csrf_token
    assert found.token_fingerprint == "fp-1"
    assert found.created_at == pytest.approx(time.time(), abs=60)
    assert found.expires_at == pytest.approx(found.created_at + HOUR, abs=1)
    assert restarted.get("not-a-session", "fp-1") is None
    assert restarted.get(None, "fp-1") is None
    assert restarted.get(created.id, None) is None
    assert len(restarted) == 1


def test_store_only_holds_a_hash_of_the_session_id(db: Database) -> None:
    store = SessionStore(db, HOUR)
    created = store.create("fp-1")
    (row,) = session_rows(db)
    assert row.id_hash == hash_secret(created.id)
    assert row.id_hash != created.id
    assert len(row.id_hash) == 64
    # The hash itself is not a session id.
    assert store.get(row.id_hash, "fp-1") is None
    assert created.id not in stored_text(db)


def test_store_rejects_and_removes_an_expired_session(db: Database) -> None:
    store = SessionStore(db, HOUR)
    created = store.create("fp-1")
    expire_all_sessions(db)
    assert store.get(created.id, "fp-1") is None
    assert session_rows(db) == []
    # A lifetime of zero is expired by the time it is looked up.
    short = SessionStore(db, -1)
    assert short.get(short.create("fp-1").id, "fp-1") is None
    assert len(store) == 0


def test_store_rejects_and_removes_a_session_of_a_rotated_token(db: Database) -> None:
    store = SessionStore(db, HOUR)
    created = store.create("fp-old")
    assert store.get(created.id, "fp-new") is None
    assert session_rows(db) == []
    assert store.get(created.id, "fp-old") is None


def test_store_delete_ends_one_session(db: Database) -> None:
    store = SessionStore(db, HOUR)
    first, second = store.create("fp-1"), store.create("fp-1")
    store.delete(first.id)
    store.delete(None)
    store.delete("unknown")
    assert store.get(first.id, "fp-1") is None
    assert store.get(second.id, "fp-1") is not None
    assert len(store) == 1


def test_creating_a_session_purges_dead_rows(db: Database) -> None:
    store = SessionStore(db, HOUR)
    live, expired = store.create("fp-1"), store.create("fp-1")
    now = datetime.now(UTC)
    with db.session() as session:
        session.execute(
            update(WebSessionRecord)
            .where(WebSessionRecord.id_hash == hash_secret(expired.id))
            .values(expires_at=now - timedelta(minutes=1))
        )
        # A session left over from before the access token was rotated.
        session.add(
            WebSessionRecord(
                id_hash=hash_secret("rotated"),
                csrf_token="x",
                token_fingerprint="fp-0",
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
    assert len(store) == 3
    newest = store.create("fp-1")
    assert {row.id_hash for row in session_rows(db)} == {
        hash_secret(live.id),
        hash_secret(newest.id),
    }
    assert store.get(expired.id, "fp-1") is None
    assert store.get(live.id, "fp-1") is not None


def test_flashes_are_shared_between_requests_and_cleared_once_shown(db: Database) -> None:
    store = SessionStore(db, HOUR)
    created = store.create("fp-1")
    first = store.get(created.id, "fp-1")
    assert first is not None
    for index in range(7):
        first.flash(f"message {index}", "ok")
    second = store.get(created.id, "fp-1")
    assert second is not None
    assert [f.message for f in second.pop_flashes()] == [f"message {i}" for i in range(2, 7)]
    third = store.get(created.id, "fp-1")
    assert third is not None
    assert third.pop_flashes() == []
    # Flashes live in memory only: after a restart the session is intact without them.
    third.flash("not shown after a restart")
    restarted = SessionStore(db, HOUR).get(created.id, "fp-1")
    assert restarted is not None
    assert restarted.pop_flashes() == []
    assert restarted.csrf_token == created.csrf_token


def test_login_nonce_is_single_use_and_stored_hashed(db: Database) -> None:
    store = SessionStore(db, HOUR)
    expires = time.time() + 600
    assert store.claim_login_nonce("nonce-one", expires)
    assert not store.claim_login_nonce("nonce-one", expires)
    assert store.claim_login_nonce("nonce-two", expires)
    # A restarted server still knows the nonce was used.
    assert not SessionStore(db, HOUR).claim_login_nonce("nonce-one", expires)
    rows = nonce_rows(db)
    assert {row.nonce_hash for row in rows} == {hash_secret("nonce-one"), hash_secret("nonce-two")}
    assert "nonce-one" not in stored_text(db)


def test_old_login_nonces_are_purged(db: Database) -> None:
    store = SessionStore(db, HOUR)
    long_gone = time.time() - NONCE_GRACE.total_seconds() - 60
    just_expired = time.time() - 60
    assert store.claim_login_nonce("long-gone", long_gone)
    assert store.claim_login_nonce("just-expired", just_expired)
    assert store.claim_login_nonce("fresh", time.time() + 600)
    # Both a later claim and a later sign-in clear nonces whose codes expired long ago.
    assert {row.nonce_hash for row in nonce_rows(db)} == {
        hash_secret("just-expired"),
        hash_secret("fresh"),
    }
    assert store.claim_login_nonce("long-gone-too", long_gone)
    store.create("fp-1")
    assert {row.nonce_hash for row in nonce_rows(db)} == {
        hash_secret("just-expired"),
        hash_secret("fresh"),
    }


def test_check_login_code_does_not_use_the_code_up() -> None:
    code = make_login_code("t0ken")
    checked = check_login_code("t0ken", code)
    assert checked is not None
    nonce, expires = checked
    assert code.startswith(f"{expires}.{nonce}.")
    assert check_login_code("t0ken", code) == checked
    assert check_login_code("other", code) is None
    assert check_login_code("t0ken", make_login_code("t0ken", now=time.time() - 3600)) is None
    for bad in ("", "garbage", "1.2", f"x.{nonce}.sig", f"{expires}..sig", f"{code}.extra"):
        assert check_login_code("t0ken", bad) is None, bad


def test_migration_adds_the_session_tables_to_an_existing_database(settings: Settings) -> None:
    settings.ensure_dirs()
    database = Database(settings.db_url)
    try:
        upgrade(database, "0001")
        assert current_revision(database) == "0001"
        before = set(inspect(database.engine).get_table_names())
        assert not {"web_sessions", "login_nonces"} & before
        upgrade(database)
        after = set(inspect(database.engine).get_table_names())
        assert {"web_sessions", "login_nonces"} <= after
        store = SessionStore(database, HOUR)
        assert store.get(store.create("fp-1").id, "fp-1") is not None
    finally:
        database.dispose()


# The running server --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_session_survives_a_restart(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        cookie = web.client.cookies.get(COOKIE_NAME)
        csrf = web.csrf
        assert cookie
        assert web.post("/cards", {"project": "acme-api", "title": "Before"}).status_code == 303

    # A new application on the same database: what ``prat serve`` is after a restart.
    with make_web(settings, db, project, login=False) as web:
        assert web.client.get("/board", follow_redirects=False).status_code == 303
        web.client.cookies.set(COOKIE_NAME, cookie)
        page = web.client.get("/board", follow_redirects=False)
        assert page.status_code == 200
        assert "Before" in page.text
        # The CSRF token is the same one, so a page left open before the restart still works.
        web.refresh_csrf()
        assert web.csrf == csrf
        assert web.post("/cards", {"project": "acme-api", "title": "After"}).status_code == 303
        refused = web.post("/cards", {"project": "acme-api", "title": "No"}, csrf=False)
        assert refused.status_code == 403
        assert web.card_count() == 2
        # Signing out ends it for good, also for the next restart.
        assert web.post("/logout").status_code == 303
        assert session_rows(db) == []

    with make_web(settings, db, project, login=False) as web:
        web.client.cookies.set(COOKIE_NAME, cookie)
        assert web.client.get("/board", follow_redirects=False).status_code == 303


@pytest.mark.integration
def test_a_used_login_code_is_rejected_after_a_restart(
    settings: Settings, db: Database, project: Project
) -> None:
    token = ensure_access_token(settings)
    code = make_login_code(token)
    unused = make_login_code(token)
    with make_web(settings, db, project, login=False) as web:
        first = web.client.get(f"/login?code={code}", follow_redirects=False)
        assert first.status_code == 303
        assert web.client.get("/board").status_code == 200

    with make_web(settings, db, project, login=False) as web:
        again = web.client.get(f"/login?code={code}", follow_redirects=False)
        assert again.status_code == 401
        assert "expired or was already used" in again.text
        assert COOKIE_NAME not in again.headers.get("set-cookie", "")
        assert web.client.get("/board", follow_redirects=False).status_code == 303
        # A code that was issued before the restart and never used still works, once.
        assert web.client.get(f"/login?code={unused}", follow_redirects=False).status_code == 303
        web.client.cookies.clear()
        assert web.client.get(f"/login?code={unused}", follow_redirects=False).status_code == 401
    assert len(nonce_rows(db)) == 2


@pytest.mark.integration
def test_an_expired_session_is_rejected_and_removed(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        assert web.client.get("/board").status_code == 200
        assert len(session_rows(db)) == 1
        expire_all_sessions(db)
        response = web.client.get("/board", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")
        assert session_rows(db) == []
        # Its CSRF token died with it.
        assert web.post("/cards", {"project": "acme-api", "title": "Late"}).status_code == 403
        assert web.card_count() == 0


@pytest.mark.integration
def test_expiry_also_holds_across_a_restart(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        cookie = web.client.cookies.get(COOKIE_NAME)
    expire_all_sessions(db)
    with make_web(settings, db, project, login=False) as web:
        web.client.cookies.set(COOKIE_NAME, cookie or "")
        assert web.client.get("/board", follow_redirects=False).status_code == 303
        assert session_rows(db) == []


@pytest.mark.integration
def test_rotating_the_token_invalidates_persisted_sessions(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        cookie = web.client.cookies.get(COOKIE_NAME)
        old_code = make_login_code(ensure_access_token(settings))
    (row,) = session_rows(db)
    assert row.token_fingerprint == fingerprint(ensure_access_token(settings))

    # The token is rotated while the server is down (``prat rotate-token``).
    new_token = rotate_access_token(settings)
    with make_web(settings, db, project, login=False) as web:
        web.client.cookies.set(COOKIE_NAME, cookie or "")
        assert web.client.get("/board", follow_redirects=False).status_code == 303
        assert session_rows(db) == []
        assert web.client.get(f"/login?code={old_code}", follow_redirects=False).status_code == 401
        # The new token signs in, and that session is stored under the new fingerprint.
        web.client.cookies.clear()
        web.login()
        (row,) = session_rows(db)
        assert row.token_fingerprint == fingerprint(new_token)


@pytest.mark.integration
def test_the_raw_session_id_never_reaches_the_database(
    settings: Settings, db: Database, project: Project
) -> None:
    token = ensure_access_token(settings)
    with make_web(settings, db, project) as web:
        by_token = web.client.cookies.get(COOKIE_NAME)
        web.client.cookies.clear()
        code = make_login_code(token)
        assert web.client.get(f"/login?code={code}", follow_redirects=False).status_code == 303
        by_code = web.client.cookies.get(COOKIE_NAME)
        assert by_token and by_code and by_token != by_code
        web.refresh_csrf()
        assert web.post("/cards", {"project": "acme-api", "title": "Card"}).status_code == 303
        assert web.client.get("/board").status_code == 200
        response = web.client.post(
            "/login", data={"token": token}, headers={"Origin": ORIGIN}, follow_redirects=False
        )
        replaced = response.cookies.get(COOKIE_NAME)
        assert replaced and replaced not in (by_token, by_code)

        stored = stored_text(db)
        for session_id in (by_token, by_code, replaced):
            assert session_id not in stored
        assert token not in stored
        nonce = code.split(".")[1]
        assert nonce not in stored
        assert hash_secret(nonce) in stored
        hashes = {row.id_hash for row in session_rows(db)}
        # Signing in again from the same browser replaced the second session.
        assert hashes == {hash_secret(by_token), hash_secret(replaced)}
        assert all(h in stored for h in hashes)
