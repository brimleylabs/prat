import unittest
from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.emails import confirmation_email


def email_for(day: date) -> str:
    cart = Cart()
    cart.add("PEN")
    return confirmation_email(place_order(cart, day))


class HiddenDelivery(unittest.TestCase):
    def test_dates(self) -> None:
        expected = {
            date(2026, 11, 9): "Wednesday 11 November 2026",
            date(2026, 11, 11): "Friday 13 November 2026",
            date(2026, 11, 12): "Monday 16 November 2026",
            date(2026, 11, 13): "Tuesday 17 November 2026",
            date(2026, 11, 14): "Tuesday 17 November 2026",
            date(2026, 11, 15): "Tuesday 17 November 2026",
        }
        for day, arrives in expected.items():
            with self.subTest(day=day):
                self.assertIn(f"Arrives by {arrives}.", email_for(day))


if __name__ == "__main__":
    unittest.main()
