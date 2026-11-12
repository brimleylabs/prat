"""Server-side sessions for the web UI.

Sessions are rows in PRAT's database, so signing in survives a restart of
``prat serve``. The cookie only carries a random session id, and the database
only stores a SHA-256 hash of that id: someone who can read the database (or a
backup of it) cannot turn a row back into a cookie. The CSRF token, the
fingerprint of the access token the session was created with, and the expiry
stay on the server.

Nonces of used one-time login codes are stored the same way, hashed, until the
code would have expired anyway, so a login link works once even across a
restart.

Flash messages are the one thing kept in memory. They are short-lived notices
("Card queued"), and losing them in a restart loses nothing else.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from prat.db import Database, LoginNonce, WebSessionRecord

COOKIE_NAME = "prat_session"
MAX_FLASHES = 5
# Used login nonces are kept this long past the code's own expiry, so a clock
# that steps back a little cannot make a spent code look fresh and unused.
NONCE_GRACE = timedelta(hours=1)


def hash_secret(value: str) -> str:
    """SHA-256 of a random session id or nonce, as stored in the database.

    The inputs are long random strings, so a plain hash is enough: there is
    nothing to guess, and no salt or slow hash is needed.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> float:
    """Seconds since the epoch. SQLite hands datetimes back without a timezone; they are UTC."""
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp()


@dataclass
class Flash:
    message: str
    tone: str = "info"  # info, ok, warn, bad


@dataclass
class WebSession:
    """One request's view of a session. ``id`` is the raw cookie value."""

    id: str
    csrf_token: str
    token_fingerprint: str
    created_at: float
    expires_at: float
    # Shared with the store, so a message set in one request shows in the next.
    flashes: list[Flash] = field(default_factory=list)

    def flash(self, message: str, tone: str = "info") -> None:
        self.flashes.append(Flash(message, tone))
        del self.flashes[:-MAX_FLASHES]

    def pop_flashes(self) -> list[Flash]:
        items = list(self.flashes)
        self.flashes.clear()
        return items


class SessionStore:
    """Sessions and used login nonces, persisted in the database."""

    def __init__(self, db: Database, lifetime_seconds: float) -> None:
        self.db = db
        self.lifetime = lifetime_seconds
        # Pending flash messages by session id hash. Memory only, see the module docstring.
        self._flashes: dict[str, list[Flash]] = {}
        self._lock = threading.Lock()

    def create(self, token_fingerprint: str) -> WebSession:
        """Start a session, and drop every row that can no longer be used."""
        session_id = secrets.token_urlsafe(32)
        now = _now()
        record = WebSessionRecord(
            id_hash=hash_secret(session_id),
            csrf_token=secrets.token_urlsafe(32),
            token_fingerprint=token_fingerprint,
            created_at=now,
            expires_at=now + timedelta(seconds=self.lifetime),
        )
        with self.db.session() as db_session:
            self._purge(db_session, now, token_fingerprint)
            db_session.add(record)
        return self._view(session_id, record)

    def get(self, session_id: str | None, token_fingerprint: str | None) -> WebSession | None:
        """The live session for a cookie value, or ``None``.

        A session that has expired, or that was created with an access token
        that has since been rotated, is deleted on sight.
        """
        if not session_id or token_fingerprint is None:
            return None
        id_hash = hash_secret(session_id)
        with self.db.session() as db_session:
            record = db_session.get(WebSessionRecord, id_hash)
            if record is None:
                return None
            if _timestamp(record.expires_at) < time.time() or not secrets.compare_digest(
                record.token_fingerprint, token_fingerprint
            ):
                db_session.delete(record)
                self._forget_flashes([id_hash])
                return None
            return self._view(session_id, record)

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        id_hash = hash_secret(session_id)
        with self.db.session() as db_session:
            db_session.execute(delete(WebSessionRecord).where(WebSessionRecord.id_hash == id_hash))
        self._forget_flashes([id_hash])

    def claim_login_nonce(self, nonce: str, expires_at: float) -> bool:
        """Mark a login code's nonce as used. ``False`` if it was used before.

        The insert is the check: the hash is the primary key, so two requests
        with the same code cannot both succeed, in this process or another.
        """
        now = _now()
        try:
            with self.db.session() as db_session:
                db_session.execute(
                    delete(LoginNonce).where(LoginNonce.expires_at < now - NONCE_GRACE)
                )
                db_session.add(
                    LoginNonce(
                        nonce_hash=hash_secret(nonce),
                        expires_at=datetime.fromtimestamp(expires_at, UTC),
                    )
                )
        except IntegrityError:
            return False
        return True

    def __len__(self) -> int:
        with self.db.session() as db_session:
            return db_session.scalar(select(func.count()).select_from(WebSessionRecord)) or 0

    def _view(self, session_id: str, record: WebSessionRecord) -> WebSession:
        with self._lock:
            flashes = self._flashes.setdefault(record.id_hash, [])
        return WebSession(
            id=session_id,
            csrf_token=record.csrf_token,
            token_fingerprint=record.token_fingerprint,
            created_at=_timestamp(record.created_at),
            expires_at=_timestamp(record.expires_at),
            flashes=flashes,
        )

    def _forget_flashes(self, id_hashes: list[str]) -> None:
        with self._lock:
            for id_hash in id_hashes:
                self._flashes.pop(id_hash, None)

    def _purge(self, db_session: Session, now: datetime, token_fingerprint: str) -> None:
        """Delete expired sessions, sessions of a rotated access token and old nonces."""
        dead = or_(
            WebSessionRecord.expires_at < now,
            WebSessionRecord.token_fingerprint != token_fingerprint,
        )
        gone = list(db_session.scalars(select(WebSessionRecord.id_hash).where(dead)))
        if gone:
            db_session.execute(delete(WebSessionRecord).where(dead))
            self._forget_flashes(gone)
        db_session.execute(delete(LoginNonce).where(LoginNonce.expires_at < now - NONCE_GRACE))


class LoginThrottle:
    """Refuse further attempts after too many failures in a short window."""

    def __init__(self, max_failures: int = 10, window_seconds: float = 60.0) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._failures and self._failures[0] < now - self.window:
            self._failures.popleft()

    def blocked(self) -> bool:
        with self._lock:
            self._trim(time.time())
            return len(self._failures) >= self.max_failures

    def fail(self) -> None:
        with self._lock:
            self._failures.append(time.time())

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
