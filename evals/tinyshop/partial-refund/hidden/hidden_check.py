import unittest
from datetime import date

from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.refunds import refund_cents


def order(coupon: str | None):
    cart = Cart()
    cart.add("MUG")
    cart.add("LAMP")
    return place_order(cart, date(2026, 11, 9), coupon=coupon)


class HiddenRefund(unittest.TestCase):
    def test_partial_with_coupon(self) -> None:
        placed = order("SAVE10")
        self.assertEqual(refund_cents(placed, ["MUG"]), 2916)
        self.assertEqual(refund_cents(placed, ["LAMP"]), 6804)

    def test_parts_add_up(self) -> None:
        for coupon in ("SAVE10", "FIVEOFF", None):
            with self.subTest(coupon=coupon):
                placed = order(coupon)
                parts = refund_cents(placed, ["MUG"]) + refund_cents(placed, ["LAMP"])
                self.assertLessEqual(abs(parts - placed.total_cents), 1)

    def test_full_and_none(self) -> None:
        placed = order("SAVE10")
        self.assertEqual(refund_cents(placed, ["MUG", "LAMP"]), placed.total_cents)
        self.assertEqual(refund_cents(placed, []), 0)


if __name__ == "__main__":
    unittest.main()
