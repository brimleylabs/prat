"""Request handlers."""

from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.emails import confirmation_email
from tinyshop.orders import ORDERS
from tinyshop.refunds import process_return


def handle_checkout(payload: dict) -> dict:
    cart = Cart()
    for sku, quantity in payload["items"].items():
        cart.add(sku, quantity)
    order = place_order(
        cart, date.fromisoformat(payload["date"]), payload.get("region", "DEFAULT"), payload.get("coupon")
    )
    return {"order": order.id, "total_cents": order.total_cents, "email": confirmation_email(order)}


def handle_return(payload: dict) -> dict:
    order = ORDERS[payload["order"]]
    return {"refund_cents": process_return(order, payload["skus"])}
