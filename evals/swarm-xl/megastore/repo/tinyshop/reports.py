"""Sales reports."""

from collections import defaultdict
from datetime import date

from tinyshop.orders import Order


def daily_sales(orders: list[Order]) -> dict[date, int]:
    totals: dict[date, int] = defaultdict(int)
    for order in orders:
        totals[order.placed_on] += order.total_cents
    return dict(totals)
