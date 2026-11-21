"""The hidden check for the gift cards feature: the agents never see it.

Run from the repository root: python hidden_check.py
"""

import unittest
from datetime import date

from tinyshop import giftcards
from tinyshop.api import handle_checkout, handle_gift_card
from tinyshop.cart import Cart
from tinyshop.checkout import place_order
from tinyshop.emails import confirmation_email
from tinyshop.inventory import STOCK
from tinyshop.refunds import process_return, refund_cents

DAY = date(2026, 11, 9)


def cart(*skus: str) -> Cart:
    c = Cart()
    for sku in skus:
        c.add(sku)
    return c


class Cards(unittest.TestCase):
    def test_issue_balance_redeem_credit(self) -> None:
        giftcards.issue("HC-A", 5000)
        self.assertEqual(giftcards.balance("hc-a"), 5000)
        self.assertEqual(giftcards.redeem("HC-A", 2000), 2000)
        self.assertEqual(giftcards.redeem("hc-a", 9999), 3000)
        self.assertEqual(giftcards.balance("HC-A"), 0)
        giftcards.credit("HC-A", 700)
        self.assertEqual(giftcards.balance("HC-A"), 700)

    def test_bad_cards(self) -> None:
        giftcards.issue("HC-B", 100)
        with self.assertRaises(ValueError):
            giftcards.issue("hc-b", 100)
        with self.assertRaises(ValueError):
            giftcards.issue("HC-ZERO", 0)
        with self.assertRaises(KeyError):
            giftcards.balance("HC-NOPE")


class Checkout(unittest.TestCase):
    def test_card_pays_part(self) -> None:
        giftcards.issue("HC-C", 5000)
        order = place_order(cart("MUG", "LAMP"), DAY, gift_card="hc-c")
        self.assertEqual(order.total_cents, 10800)
        self.assertEqual(order.gift_card_cents, 5000)
        self.assertEqual(order.amount_due_cents, 5800)
        self.assertEqual(giftcards.balance("HC-C"), 0)
        self.assertEqual(order.gift_card.upper(), "HC-C")

    def test_card_pays_all(self) -> None:
        giftcards.issue("HC-D", 50000)
        order = place_order(cart("PEN"), DAY, gift_card="HC-D")
        self.assertEqual(order.gift_card_cents, order.total_cents)
        self.assertEqual(order.amount_due_cents, 0)
        self.assertEqual(giftcards.balance("HC-D"), 50000 - order.total_cents)

    def test_no_card_unchanged(self) -> None:
        order = place_order(cart("MUG"), DAY, coupon="SAVE10")
        self.assertEqual(order.gift_card_cents, 0)
        self.assertEqual(order.amount_due_cents, order.total_cents)

    def test_unknown_card_reserves_nothing(self) -> None:
        before = dict(STOCK)
        with self.assertRaises(KeyError):
            place_order(cart("LAMP"), DAY, gift_card="HC-UNKNOWN")
        self.assertEqual(STOCK, before)


class Returns(unittest.TestCase):
    def test_card_paid_back_first_across_returns(self) -> None:
        giftcards.issue("HC-E", 5000)
        order = place_order(cart("MUG", "LAMP"), DAY, gift_card="HC-E")
        mug = refund_cents(order, ["MUG"])
        lamp = refund_cents(order, ["LAMP"])
        # The mug's refund goes back onto the card entirely.
        self.assertEqual(process_return(order, ["MUG"]), 0)
        self.assertEqual(giftcards.balance("HC-E"), mug)
        # The card gets back only what it is still owed; the rest is paid out.
        self.assertEqual(process_return(order, ["LAMP"]), lamp - (5000 - mug))
        self.assertEqual(giftcards.balance("HC-E"), 5000)

    def test_without_a_card_returns_pay_out(self) -> None:
        order = place_order(cart("MUG", "LAMP"), DAY)
        self.assertEqual(process_return(order, ["MUG"]), refund_cents(order, ["MUG"]))


class Api(unittest.TestCase):
    def test_issue_and_balance(self) -> None:
        issued = handle_gift_card({"action": "issue", "code": "HC-F", "amount_cents": 2500})
        self.assertEqual(issued["balance_cents"], 2500)
        self.assertEqual(handle_gift_card({"action": "balance", "code": "hc-f"})["balance_cents"], 2500)
        self.assertEqual(
            handle_gift_card({"action": "balance", "code": "HC-MISSING"}),
            {"error": "unknown gift card"},
        )

    def test_checkout_with_card(self) -> None:
        handle_gift_card({"action": "issue", "code": "HC-G", "amount_cents": 1000})
        out = handle_checkout({"items": {"MUG": 1}, "date": "2026-11-09", "gift_card": "HC-G"})
        self.assertEqual(out["total_cents"], 3240)
        self.assertEqual(out["amount_due_cents"], 2240)

    def test_checkout_without_card(self) -> None:
        out = handle_checkout({"items": {"PEN": 1}, "date": "2026-11-09"})
        self.assertEqual(out["amount_due_cents"], out["total_cents"])


class Email(unittest.TestCase):
    def test_lines(self) -> None:
        giftcards.issue("HC-H", 1000)
        order = place_order(cart("MUG"), DAY, gift_card="HC-H")
        lines = confirmation_email(order).splitlines()
        total = next(i for i, line in enumerate(lines) if line.startswith("Total: "))
        self.assertEqual(lines[total + 1], "Gift card: -$10.00")
        self.assertEqual(lines[total + 2], "To pay: $22.40")

    def test_no_card_no_lines(self) -> None:
        order = place_order(cart("PEN"), DAY)
        text = confirmation_email(order)
        self.assertNotIn("Gift card", text)
        self.assertNotIn("To pay", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
