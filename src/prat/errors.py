"""The internal error log.

:func:`record_error` stores one row per unexpected failure: where it came
from, what kind it was, a capped message and a small JSON detail. Every string
is passed through the secret scanner first, so a credential that appears in an
exception message or a traceback is never stored.

Each row carries a fingerprint: a stable hash of the source, the kind and
either the innermost PRAT frame of the traceback or the normalised message.
The same problem therefore keeps the same fingerprint across restarts, line
number changes and values that differ from one occurrence to the next, which
is what lets diagnostics count repeats.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import traceback
from collections.abc import Mapping
from typing import Any

from prat.db import Database
from prat.db.models import ErrorLog
from prat.policy.secrets import REDACTED, redact

log = logging.getLogger(__name__)

ERROR_SOURCES: tuple[str, ...] = ("web", "engine", "runtime", "gate", "improve", "cli")
MAX_MESSAGE_CHARS = 2000
MAX_KIND_CHARS = 120
MAX_DETAIL_STRING_CHARS = 2000
MAX_DETAIL_ITEMS = 40
MAX_DETAIL_DEPTH = 4
MAX_DETAIL_JSON_CHARS = 16_000
MAX_TRACEBACK_FRAMES = 12

# name = value pairs whose name says the value is a credential.
_ASSIGNMENT = re.compile(
    r"(?i)\b([\w.-]*(?:password|passwd|secret|token|api[_-]?key|authorization|credential)"
    r"[\w.-]*)(\"?\s*[=:]\s*)(?:bearer\s+|basic\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_KEY = re.compile(r"(?i)password|passwd|secret|token|api[_-]?key|authorization|credential")

_HEX = re.compile(r"\b(?:0[xX][0-9a-fA-F]+|[0-9a-fA-F]{7,})\b")
_NUMBER = re.compile(r"\d+")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_SPACE = re.compile(r"\s+")


def redact_text(text: str) -> str:
    """``text`` with credential-shaped values replaced by a fixed marker."""
    text = redact(text)
    text = _URL_USERINFO.sub(rf"\1{REDACTED}@", text)
    text = _ASSIGNMENT.sub(rf"\1\2{REDACTED}", text)
    return _BEARER.sub(rf"\1 {REDACTED}", text)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def redact_detail(value: Any, *, _depth: int = 0) -> Any:
    """A JSON-safe, size-capped copy of ``value`` with secrets redacted."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _cap(redact_text(value), MAX_DETAIL_STRING_CHARS)
    if _depth >= MAX_DETAIL_DEPTH:
        return _cap(redact_text(str(value)), 200)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DETAIL_ITEMS:
                out["..."] = f"{len(value) - MAX_DETAIL_ITEMS} more"
                break
            name = _cap(redact_text(str(key)), 120)
            if _SECRET_KEY.search(name) and isinstance(item, str | bytes):
                out[name] = REDACTED
            else:
                out[name] = redact_detail(item, _depth=_depth + 1)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        result = [redact_detail(item, _depth=_depth + 1) for item in items[:MAX_DETAIL_ITEMS]]
        if len(items) > MAX_DETAIL_ITEMS:
            result.append(f"... {len(items) - MAX_DETAIL_ITEMS} more")
        return result
    return _cap(redact_text(str(value)), MAX_DETAIL_STRING_CHARS)


def normalise_message(message: str) -> str:
    """The message with the parts that vary between occurrences collapsed."""
    text = _QUOTED.sub("'?'", message)
    text = _HEX.sub("#", text)
    text = _NUMBER.sub("#", text)
    return _SPACE.sub(" ", text).strip().casefold()[:200]


def _prat_frame(exc: BaseException) -> str | None:
    """``module:function`` of the innermost frame that belongs to PRAT."""
    found: str | None = None
    for frame, _line in traceback.walk_tb(exc.__traceback__):
        module = frame.f_globals.get("__name__", "")
        if isinstance(module, str) and (module == "prat" or module.startswith("prat.")):
            found = f"{module}:{frame.f_code.co_name}"
    return found


def fingerprint(source: str, kind: str, message: str, exc: BaseException | None = None) -> str:
    """A stable 64-character identifier for one kind of failure."""
    where = _prat_frame(exc) if exc is not None else None
    basis = "\n".join([source, kind, where or normalise_message(message)])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _traceback_tail(exc: BaseException) -> list[str]:
    frames = traceback.extract_tb(exc.__traceback__)[-MAX_TRACEBACK_FRAMES:]
    return [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]


def record_error(
    db: Database,
    source: str,
    kind: str,
    message: str,
    *,
    detail: Mapping[str, Any] | None = None,
    exc: BaseException | None = None,
) -> int | None:
    """Store one error. Returns the row id, or ``None`` if it could not be stored.

    Never raises: the error log must not turn one failure into two.
    """
    try:
        if source not in ERROR_SOURCES:
            raise ValueError(f"unknown error source {source!r}")
        kind = _cap(kind.strip() or "error", MAX_KIND_CHARS)
        data: dict[str, Any] = dict(detail or {})
        if exc is not None:
            data.setdefault("traceback", _traceback_tail(exc))
        safe = redact_detail(data)
        if len(json.dumps(safe, default=str)) > MAX_DETAIL_JSON_CHARS:
            safe = {"truncated": True, "keys": sorted(str(key) for key in data)[:MAX_DETAIL_ITEMS]}
        row = ErrorLog(
            source=source,
            kind=kind,
            message=_cap(redact_text(message), MAX_MESSAGE_CHARS),
            detail=safe,
            fingerprint=fingerprint(source, kind, message, exc),
        )
        with db.session() as session:
            session.add(row)
            session.flush()
            return row.id
    except Exception:
        log.exception("could not record an error from %s", source)
        return None
