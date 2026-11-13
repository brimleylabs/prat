"""Writing numbers."""


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th and so on."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
