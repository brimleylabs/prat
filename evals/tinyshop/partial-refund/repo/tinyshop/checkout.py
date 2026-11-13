"""Turning a cart into an order."""

from datetime import date
from itertools import count

from tinyshop.cart import Cart
from tinyshop.catalog import get_product
from tinyshop.discounts import discount_cents
from tinyshop.inventory import reserve
from tinyshop.orders import ORDERS, Order, OrderLine
from tinyshop.tax import tax_cents

_ids = count(1)


def place_order(cart: Cart, placed_on: date, region: str = "DEFAULT", coupon: str | None = None) -> Order:
    if not cart.lines:
        raise ValueError("the cart is empty")
    lines = [OrderLine(sku, get_product(sku).price_cents, qty) for sku, qty in cart.lines.items()]
    for line in lines:
        reserve(line.sku, line.quantity)
    subtotal = sum(line.total_cents for line in lines)
    discount = discount_cents(subtotal, coupon)
    order = Order(
        id=next(_ids),
        placed_on=placed_on,
        region=region,
        lines=lines,
        coupon=coupon,
        subtotal_cents=subtotal,
        discount_cents=discount,
        tax_cents=tax_cents(subtotal - discount, region),
    )
    ORDERS[order.id] = order
    return order
