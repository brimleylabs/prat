import unittest
from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.emails import confirmation_email


class EmailTest(unittest.TestCase):
    def test_monday_order(self) -> None:
        cart = Cart()
        cart.add("PEN")
        order = place_order(cart, date(2026, 11, 9))
        self.assertIn("Arrives by Wednesday 11 November 2026.", confirmation_email(order))
