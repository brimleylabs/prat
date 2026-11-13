"""Small list helpers."""

from typing import TypeVar

T = TypeVar("T")


def first(items: list[T], default: T | None = None) -> T | None:
    """The first item, or ``default`` for an empty list."""
    return items[0] if items else default
