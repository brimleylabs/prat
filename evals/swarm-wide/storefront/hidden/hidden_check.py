"""The hidden check for the storefront epic: the agents never see it.

Run from the repository root: python hidden_check.py
"""

import unittest
from datetime import date

from tinyshop import inventory, reviews, wishlist
from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.emails import confirmation_email
from tinyshop.reports import sales_csv


def cart(*skus: str) -> Cart:
    c = Cart()
    for sku in skus:
        c.add(sku)
    return c


class Wishlist(unittest.TestCase):
    def test_add_remove_items(self) -> None:
        self.assertEqual(wishlist.items("ann"), [])
        wishlist.add("ann", "PEN")
        wishlist.add("ann", "MUG")
        wishlist.add("ann", "PEN")
        self.assertEqual(wishlist.items("ann"), ["MUG", "PEN"])
        wishlist.remove("ann", "PEN")
        wishlist.remove("ann", "LAMP")
        self.assertEqual(wishlist.items("ann"), ["MUG"])
        self.assertEqual(wishlist.items("bob"), [])

    def test_unknown_product(self) -> None:
        with self.assertRaises(KeyError):
            wishlist.add("ann", "NOPE")


class Reviews(unittest.TestCase):
    def test_average_and_count(self) -> None:
        self.assertIsNone(reviews.average_rating("LAMP"))
        self.assertEqual(reviews.review_count("LAMP"), 0)
        reviews.add_review("LAMP", 5, "bright")
        reviews.add_review("LAMP", 4, "good")
        reviews.add_review("LAMP", 4, "fine")
        self.assertEqual(reviews.average_rating("LAMP"), 4.3)
        self.assertEqual(reviews.review_count("LAMP"), 3)

    def test_bad_reviews(self) -> None:
        for rating in (0, 6, 3.5):
            with self.subTest(rating=rating), self.assertRaises(ValueError):
                reviews.add_review("MUG", rating, "x")
        with self.assertRaises(KeyError):
            reviews.add_review("NOPE", 3, "x")


class Stock(unittest.TestCase):
    def test_low_stock_and_restock(self) -> None:
        inventory.STOCK.update({"MUG": 40, "LAMP": 5, "PEN": 2})
        self.assertEqual(inventory.low_stock(), ["LAMP", "PEN"])
        self.assertEqual(inventory.low_stock(threshold=2), ["PEN"])
        inventory.restock("PEN", 10)
        self.assertEqual(inventory.STOCK["PEN"], 12)
        self.assertEqual(inventory.low_stock(), ["LAMP"])
        with self.assertRaises(ValueError):
            inventory.restock("PEN", 0)
        inventory.STOCK.update({"MUG": 40, "LAMP": 12, "PEN": 100})


class Notes(unittest.TestCase):
    def test_note_recorded_and_emailed(self) -> None:
        order = place_order(cart("MUG"), date(2026, 11, 9), note="  Leave at the door  ")
        self.assertEqual(order.note, "Leave at the door")
        lines = confirmation_email(order).splitlines()
        total = next(i for i, line in enumerate(lines) if line.startswith("Total: "))
        self.assertEqual(lines[total + 1], "Note: Leave at the door")

    def test_blank_and_missing_notes(self) -> None:
        self.assertIsNone(place_order(cart("PEN"), date(2026, 11, 9), note="   ").note)
        order = place_order(cart("PEN"), date(2026, 11, 9))
        self.assertIsNone(order.note)
        self.assertNotIn("Note:", confirmation_email(order))

    def test_long_note_reserves_nothing(self) -> None:
        before = dict(inventory.STOCK)
        with self.assertRaises(ValueError):
            place_order(cart("LAMP"), date(2026, 11, 9), note="x" * 201)
        self.assertEqual(inventory.STOCK, before)


class Export(unittest.TestCase):
    def test_csv(self) -> None:
        a = place_order(cart("PEN"), date(2026, 11, 10))
        b = place_order(cart("MUG"), date(2026, 11, 9))
        c = place_order(cart("PEN", "MUG"), date(2026, 11, 10))
        text = sales_csv([a, b, c])
        self.assertEqual(
            text,
            "date,orders,total_cents\n"
            f"2026-11-09,1,{b.total_cents}\n"
            f"2026-11-10,2,{a.total_cents + c.total_cents}\n",
        )

    def test_empty(self) -> None:
        self.assertEqual(sales_csv([]), "date,orders,total_cents\n")


if __name__ == "__main__":
    unittest.main(verbosity=1)
