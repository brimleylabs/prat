"""Customer emails."""

from tinyshop.money import format_cents
from tinyshop.orders import Order
from tinyshop.shipping import delivery_estimate


def confirmation_email(order: Order) -> str:
    arrives = delivery_estimate(order.placed_on)
    return (
        f"Thank you for order #{order.id}.\n"
        f"Total: {format_cents(order.total_cents)}\n"
        f"Arrives by {arrives:%A %d %B %Y}.\n"
    )
