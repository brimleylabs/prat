"""Dates."""

from datetime import date, timedelta


def add_business_days(start: date, days: int) -> date:
    """The date ``days`` business days after ``start``."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() != 6:
            added += 1
    return current


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5
