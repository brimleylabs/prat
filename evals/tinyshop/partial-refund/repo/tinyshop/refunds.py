"""Refunds for returned products."""

from tinyshop.inventory import release
from tinyshop.orders import Order
from tinyshop.tax import tax_cents


def refund_cents(order: Order, skus: list[str]) -> int:
    """What the customer gets back for returning the products ``skus`` of ``order``."""
    returned = [line for line in order.lines if line.sku in skus]
    if not returned:
        return 0
    if len(returned) == len(order.lines):
        return order.total_cents
    goods = sum(line.total_cents for line in returned)
    # The returned products' share of the order's discount.
    discount = order.discount_cents * (goods // order.subtotal_cents)
    taxable = goods - discount
    return taxable + tax_cents(taxable, order.region)


def process_return(order: Order, skus: list[str]) -> int:
    amount = refund_cents(order, skus)
    for line in order.lines:
        if line.sku in skus:
            release(line.sku, line.quantity)
    return amount
