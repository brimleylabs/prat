"""A shopping cart."""

from tinyshop.catalog import get_product


class Cart:
    def __init__(self) -> None:
        self.lines: dict[str, int] = {}

    def add(self, sku: str, quantity: int = 1) -> None:
        get_product(sku)
        self.lines[sku] = self.lines.get(sku, 0) + quantity

    def remove(self, sku: str) -> None:
        self.lines.pop(sku, None)

    def subtotal_cents(self) -> int:
        return sum(get_product(sku).price_cents * qty for sku, qty in self.lines.items())
