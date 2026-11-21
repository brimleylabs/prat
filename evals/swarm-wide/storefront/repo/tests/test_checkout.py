import unittest
from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order


class CheckoutTest(unittest.TestCase):
    def test_total_with_coupon_and_tax(self) -> None:
        cart = Cart()
        cart.add("MUG")
        cart.add("LAMP")
        order = place_order(cart, date(2026, 11, 9), coupon="SAVE10")
        self.assertEqual(order.discount_cents, 1000)
        self.assertEqual(order.tax_cents, 720)
        self.assertEqual(order.total_cents, 9720)
