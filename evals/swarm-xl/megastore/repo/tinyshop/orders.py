"""Placed orders."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class OrderLine:
    sku: str
    unit_price_cents: int
    quantity: int

    @property
    def total_cents(self) -> int:
        return self.unit_price_cents * self.quantity


@dataclass
class Order:
    id: int
    placed_on: date
    region: str
    lines: list[OrderLine] = field(default_factory=list)
    coupon: str | None = None
    subtotal_cents: int = 0
    discount_cents: int = 0
    tax_cents: int = 0

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents + self.tax_cents


ORDERS: dict[int, Order] = {}
