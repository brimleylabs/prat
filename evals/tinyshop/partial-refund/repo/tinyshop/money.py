"""Money is kept in integer cents."""


def percent_of(amount_cents: int, percent: int) -> int:
    """``percent`` % of an amount, rounded half up."""
    return (amount_cents * percent + 50) // 100


def basis_points_of(amount_cents: int, basis_points: int) -> int:
    """An amount times a rate in basis points, rounded half up."""
    return (amount_cents * basis_points + 5000) // 10000


def format_cents(amount_cents: int) -> str:
    return f"${amount_cents // 100}.{amount_cents % 100:02d}"
