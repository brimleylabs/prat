"""Coupons."""

from tinyshop.config import COUPONS
from tinyshop.money import percent_of


def discount_cents(subtotal_cents: int, code: str | None) -> int:
    if not code:
        return 0
    try:
        kind, amount = COUPONS[code.upper()]
    except KeyError:
        raise ValueError(f"unknown coupon {code}") from None
    if kind == "percent":
        return percent_of(subtotal_cents, amount)
    return min(amount, subtotal_cents)
