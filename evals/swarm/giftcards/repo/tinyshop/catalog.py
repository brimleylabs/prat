"""The products for sale."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int


PRODUCTS = {
    "MUG": Product("MUG", "Stoneware mug", 3000),
    "LAMP": Product("LAMP", "Desk lamp", 7000),
    "PEN": Product("PEN", "Fountain pen", 1250),
}


def get_product(sku: str) -> Product:
    try:
        return PRODUCTS[sku]
    except KeyError:
        raise KeyError(f"unknown product {sku}") from None
