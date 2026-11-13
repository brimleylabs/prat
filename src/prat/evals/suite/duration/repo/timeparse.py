"""Reading times that people write."""


def parse_clock(text: str) -> int:
    """Minutes after midnight for a time like "09:30"."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)
