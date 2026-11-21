import unittest
from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.refunds import refund_cents


class RefundTest(unittest.TestCase):
    def test_full_refund(self) -> None:
        cart = Cart()
        cart.add("MUG")
        cart.add("LAMP")
        order = place_order(cart, date(2026, 11, 9), coupon="SAVE10")
        self.assertEqual(refund_cents(order, ["MUG", "LAMP"]), order.total_cents)
