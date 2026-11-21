"""Stock levels and reservations."""

STOCK = {"MUG": 40, "LAMP": 12, "PEN": 100}


class OutOfStock(Exception):
    pass


def reserve(sku: str, quantity: int) -> None:
    available = STOCK.get(sku, 0)
    if quantity > available:
        raise OutOfStock(f"only {available} {sku} left")
    STOCK[sku] = available - quantity


def release(sku: str, quantity: int) -> None:
    STOCK[sku] = STOCK.get(sku, 0) + quantity
