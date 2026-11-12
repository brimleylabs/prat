"""Dolt as PRAT's store: the managed server, and later versioning and branches.

When a Dolt binary is available PRAT keeps its records in a Dolt database, so
every meaningful moment can be a commit: history, time travel and rollback of
what agents did. Without one, PRAT uses SQLite and those features report that
they need the Dolt store.
"""

from prat.dolt.server import DoltError, ManagedDolt, ServerInfo, find_dolt

__all__ = ["DoltError", "ManagedDolt", "ServerInfo", "find_dolt"]
