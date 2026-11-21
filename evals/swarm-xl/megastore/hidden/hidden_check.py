"""The hidden check for the megastore epic: the agents never see it.

Run from the repository root: python hidden_check.py
"""

import dataclasses
import io
import unittest
from datetime import date, timedelta

from tinyshop import (
    bundles,
    inventory,
    invoices,
    loyalty,
    promotions,
    returns_policy,
    search,
    shipping_rates,
    subscriptions,
)
from tinyshop.invoices import InvoiceBook, InvoiceLine
from tinyshop.loyalty import InsufficientPoints, LoyaltyAccount
from tinyshop.promotions import Promotion
from tinyshop.returns_policy import InvalidTransition, ReturnWindowClosed, Rma
from tinyshop.subscriptions import Subscription


def d(text: str) -> date:
    return date.fromisoformat(text)


class ExistingTests(unittest.TestCase):
    def test_existing_suite_still_passes(self) -> None:
        suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        self.assertGreaterEqual(result.testsRun, 5, "the repository's own tests are missing")
        self.assertTrue(result.wasSuccessful(), stream.getvalue())


# --------------------------------------------------------------------------- 1. loyalty


class Loyalty(unittest.TestCase):
    def test_tier_bounds(self) -> None:
        self.assertEqual(loyalty.tier_for(0), "bronze")
        self.assertEqual(loyalty.tier_for(49999), "bronze")
        self.assertEqual(loyalty.tier_for(50000), "silver")
        self.assertEqual(loyalty.tier_for(199999), "silver")
        self.assertEqual(loyalty.tier_for(200000), "gold")
        self.assertEqual(loyalty.tier_for(5000000), "gold")
        with self.assertRaises(ValueError):
            loyalty.tier_for(-1)

    def test_next_tier(self) -> None:
        self.assertEqual(loyalty.next_tier(49999), ("silver", 1))
        self.assertEqual(loyalty.next_tier(0), ("silver", 50000))
        self.assertEqual(loyalty.next_tier(50000), ("gold", 150000))
        self.assertEqual(loyalty.next_tier(199999), ("gold", 1))
        self.assertIsNone(loyalty.next_tier(200000))
        self.assertIsNone(loyalty.next_tier(999999))
        with self.assertRaises(ValueError):
            loyalty.next_tier(-1)

    def test_points_for_order(self) -> None:
        self.assertEqual(loyalty.points_for_order(1999, "bronze"), 19)
        self.assertEqual(loyalty.points_for_order(250, "silver"), 3)
        self.assertEqual(loyalty.points_for_order(1300, "silver"), 16)
        self.assertEqual(loyalty.points_for_order(600, "silver"), 8)
        self.assertEqual(loyalty.points_for_order(700, "gold"), 11)
        self.assertEqual(loyalty.points_for_order(99, "gold"), 0)
        self.assertEqual(loyalty.points_for_order(0, "bronze"), 0)

    def test_points_for_order_errors(self) -> None:
        with self.assertRaises(ValueError):
            loyalty.points_for_order(-1, "bronze")
        for tier in ("platinum", "Gold", ""):
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                loyalty.points_for_order(1000, tier)

    def test_new_account(self) -> None:
        account = LoyaltyAccount("ann")
        self.assertEqual(account.customer, "ann")
        self.assertEqual(account.lifetime_spend_cents, 0)
        self.assertEqual(account.tier, "bronze")
        self.assertEqual(account.balance(d("2026-11-09")), 0)
        other = LoyaltyAccount("bob")
        other.earn(10000, d("2026-11-09"))
        self.assertEqual(account.balance(d("2026-11-09")), 0)
        self.assertEqual(other.balance(d("2026-11-09")), 100)

    def test_earn_uses_the_tier_before_the_order(self) -> None:
        account = LoyaltyAccount("ann")
        self.assertEqual(account.earn(60000, d("2026-03-01")), 600)
        self.assertEqual(account.lifetime_spend_cents, 60000)
        self.assertEqual(account.tier, "silver")
        self.assertEqual(account.earn(1000, d("2026-03-02")), 13)
        self.assertEqual(account.earn(139000, d("2026-03-03")), 1738)
        self.assertEqual(account.tier, "gold")
        self.assertEqual(account.earn(700, d("2026-03-04")), 11)
        self.assertEqual(account.balance(d("2026-03-04")), 600 + 13 + 1738 + 11)
        with self.assertRaises(ValueError):
            account.earn(-1, d("2026-03-05"))
        self.assertEqual(account.lifetime_spend_cents, 200700)

    def test_expiry_boundary(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(10000, d("2026-01-10"))
        self.assertEqual(account.balance(d("2026-01-10")), 100)
        self.assertEqual(account.balance(d("2027-01-10")), 100)
        self.assertEqual(account.balance(d("2027-01-11")), 0)
        self.assertEqual(account.balance(d("2025-12-01")), 100)

    def test_redeem(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(50000, d("2026-02-01"))
        self.assertEqual(account.redeem(200, d("2026-02-02")), 500)
        self.assertEqual(account.balance(d("2026-02-02")), 300)
        self.assertEqual(account.redeem(300, d("2026-02-03")), 750)
        self.assertEqual(account.balance(d("2026-02-03")), 0)
        self.assertEqual(account.lifetime_spend_cents, 50000)

    def test_redeem_errors_change_nothing(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(50000, d("2026-02-01"))
        for points in (0, -100, 150, 50):
            with self.subTest(points=points), self.assertRaises(ValueError):
                account.redeem(points, d("2026-02-02"))
        with self.assertRaises(InsufficientPoints):
            account.redeem(600, d("2026-02-02"))
        self.assertEqual(account.balance(d("2026-02-02")), 500)
        self.assertTrue(issubclass(InsufficientPoints, Exception))
        self.assertFalse(issubclass(InsufficientPoints, ValueError))

    def test_redeem_takes_the_oldest_lot_first(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(30000, d("2026-01-01"))
        account.earn(30000, d("2026-06-01"))
        self.assertEqual(account.redeem(400, d("2026-07-01")), 1000)
        # The January lot (300) is used up and 100 came off the June lot.
        self.assertEqual(account.expiring(d("2026-07-01"), 200), 0)
        self.assertEqual(account.balance(d("2027-01-02")), 200)

    def test_redeem_orders_lots_by_date_not_by_call(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(30000, d("2026-06-01"))
        account.earn(20000, d("2026-01-01"))
        account.redeem(200, d("2026-07-01"))
        self.assertEqual(account.balance(d("2026-07-01")), 300)
        self.assertEqual(account.balance(d("2027-01-02")), 300)

    def test_expired_lots_cannot_be_redeemed(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(10000, d("2025-01-01"))
        account.earn(20000, d("2026-01-01"))
        self.assertEqual(account.balance(d("2026-01-01")), 300)
        self.assertEqual(account.balance(d("2026-06-01")), 200)
        with self.assertRaises(InsufficientPoints):
            account.redeem(300, d("2026-06-01"))
        self.assertEqual(account.redeem(200, d("2026-06-01")), 500)
        self.assertEqual(account.balance(d("2026-06-01")), 0)

    def test_expiring(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(10000, d("2026-01-01"))
        account.earn(20000, d("2026-03-01"))
        on = d("2026-12-25")
        self.assertEqual(account.expiring(on, 0), 0)
        self.assertEqual(account.expiring(on, 6), 0)
        self.assertEqual(account.expiring(on, 7), 100)
        self.assertEqual(account.expiring(on, 65), 100)
        self.assertEqual(account.expiring(on, 66), 300)
        # A lot that has already expired is not "expiring".
        self.assertEqual(account.expiring(d("2027-01-02"), 400), 200)
        with self.assertRaises(ValueError):
            account.expiring(on, -1)

    def test_max_redeemable(self) -> None:
        account = LoyaltyAccount("ann")
        account.earn(60000, d("2026-02-01"))
        on = d("2026-02-02")
        self.assertEqual(account.max_redeemable(on, 2000), 400)
        self.assertEqual(account.max_redeemable(on, 500), 100)
        self.assertEqual(account.max_redeemable(on, 499), 0)
        self.assertEqual(account.max_redeemable(on, 100000), 600)
        self.assertEqual(account.max_redeemable(on, 0), 0)
        self.assertEqual(account.max_redeemable(d("2028-01-01"), 100000), 0)
        with self.assertRaises(ValueError):
            account.max_redeemable(on, -1)
        other = LoyaltyAccount("bob")
        other.earn(25000, d("2026-02-01"))
        self.assertEqual(other.max_redeemable(on, 100000), 200)


# ------------------------------------------------------------------------ 2. promotions


class Promotions(unittest.TestCase):
    def test_subtotal(self) -> None:
        self.assertEqual(promotions.subtotal_cents({"MUG": 2, "PEN": 1}), 7250)
        self.assertEqual(promotions.subtotal_cents({}), 0)

    def test_bad_carts(self) -> None:
        promo = Promotion("TEN", "percent", 10)
        for lines, error in (({"NOPE": 1}, KeyError), ({"MUG": 0}, ValueError), ({"MUG": -2}, ValueError)):
            with self.subTest(lines=lines):
                with self.assertRaises(error):
                    promotions.subtotal_cents(lines)
                with self.assertRaises(error):
                    promotions.discount_for(promo, lines)
                with self.assertRaises(error):
                    promotions.applicable([promo], lines)
                with self.assertRaises(error):
                    promotions.best_combination([promo], lines)
                with self.assertRaises(error):
                    promotions.applicable([], lines)

    def test_promotion_fields_and_defaults(self) -> None:
        promo = Promotion("X", "spend", 500)
        self.assertEqual(
            (promo.code, promo.kind, promo.value, promo.sku, promo.buy, promo.get),
            ("X", "spend", 500, None, 0, 0),
        )
        self.assertEqual((promo.min_spend_cents, promo.stackable), (0, False))
        positional = Promotion("B", "bogo", 0, "MUG", 2, 1, 1000, True)
        self.assertEqual(positional, Promotion(code="B", kind="bogo", sku="MUG", buy=2, get=1, min_spend_cents=1000, stackable=True))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            promo.value = 600  # type: ignore[misc]
        Promotion("FULL", "percent", 100)
        Promotion("ONE", "percent", 1)

    def test_promotion_validation(self) -> None:
        bad = [
            dict(code="", kind="percent", value=10),
            dict(code="A", kind="half", value=10),
            dict(code="A", kind="percent", value=0),
            dict(code="A", kind="percent", value=101),
            dict(code="A", kind="bogo", buy=2, get=1),
            dict(code="A", kind="bogo", sku="MUG", buy=0, get=1),
            dict(code="A", kind="bogo", sku="MUG", buy=2, get=0),
            dict(code="A", kind="spend", value=0),
            dict(code="A", kind="spend", value=500, sku="MUG"),
            dict(code="A", kind="percent", value=10, min_spend_cents=-1),
        ]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Promotion(**fields)

    def test_percent(self) -> None:
        self.assertEqual(promotions.discount_for(Promotion("P", "percent", 10), {"MUG": 2, "PEN": 1}), 725)
        self.assertEqual(promotions.discount_for(Promotion("P", "percent", 15), {"PEN": 1}), 188)
        self.assertEqual(promotions.discount_for(Promotion("P", "percent", 100), {"PEN": 3}), 3750)
        self.assertEqual(promotions.discount_for(Promotion("P", "percent", 10), {}), 0)
        promo = Promotion("L", "percent", 10, sku="LAMP")
        self.assertEqual(promotions.discount_for(promo, {"LAMP": 2, "MUG": 1}), 1400)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 1}), 0)
        self.assertEqual(promotions.discount_for(Promotion("T", "percent", 33, sku="PEN"), {"PEN": 1, "LAMP": 1}), 413)

    def test_bogo(self) -> None:
        promo = Promotion("B", "bogo", sku="MUG", buy=2, get=1)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 7}), 6000)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 2}), 0)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 3}), 3000)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 5, "LAMP": 1}), 3000)
        self.assertEqual(promotions.discount_for(promo, {"LAMP": 3}), 0)
        twin = Promotion("T", "bogo", sku="PEN", buy=1, get=1)
        self.assertEqual(promotions.discount_for(twin, {"PEN": 5}), 2500)
        triple = Promotion("G", "bogo", sku="PEN", buy=1, get=2)
        self.assertEqual(promotions.discount_for(triple, {"PEN": 7}), 5000)

    def test_spend(self) -> None:
        promo = Promotion("S", "spend", 500, min_spend_cents=5000)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 1, "PEN": 1}), 0)
        self.assertEqual(promotions.discount_for(promo, {"PEN": 4}), 500)
        self.assertEqual(promotions.discount_for(promo, {"MUG": 1, "PEN": 2}), 500)
        self.assertEqual(promotions.discount_for(Promotion("BIG", "spend", 2000), {"PEN": 1}), 1250)
        self.assertEqual(promotions.discount_for(Promotion("BIG", "spend", 2000), {}), 0)

    def test_min_spend_applies_to_every_kind(self) -> None:
        percent = Promotion("P", "percent", 10, min_spend_cents=10000)
        self.assertEqual(promotions.discount_for(percent, {"LAMP": 1}), 0)
        self.assertEqual(promotions.discount_for(percent, {"LAMP": 1, "MUG": 1}), 1000)
        bogo = Promotion("B", "bogo", sku="PEN", buy=1, get=1, min_spend_cents=2501)
        self.assertEqual(promotions.discount_for(bogo, {"PEN": 2}), 0)
        self.assertEqual(promotions.discount_for(bogo, {"PEN": 3}), 1250)

    def test_applicable(self) -> None:
        promos = [
            Promotion("ZED", "percent", 5),
            Promotion("BOGO", "bogo", sku="MUG", buy=2, get=1),
            Promotion("LAMP10", "percent", 10, sku="LAMP"),
            Promotion("BIG", "spend", 1000, min_spend_cents=20000),
            Promotion("ALPHA", "spend", 100),
        ]
        self.assertEqual(promotions.applicable(promos, {"MUG": 3}), ["ALPHA", "BOGO", "ZED"])
        self.assertEqual(promotions.applicable(promos, {"LAMP": 3}), ["ALPHA", "BIG", "LAMP10", "ZED"])
        self.assertEqual(promotions.applicable(promos, {}), [])
        self.assertEqual(promotions.applicable([], {"MUG": 1}), [])
        with self.assertRaises(ValueError):
            promotions.applicable([Promotion("A", "percent", 5), Promotion("A", "spend", 100)], {"MUG": 1})

    def test_best_stack_beats_an_exclusive_promotion(self) -> None:
        lines = {"LAMP": 1, "MUG": 3}
        promos = [
            Promotion("D", "spend", 1000, min_spend_cents=15000, stackable=True),
            Promotion("A", "percent", 20),
            Promotion("C", "bogo", sku="MUG", buy=2, get=1, stackable=True),
            Promotion("B", "percent", 10, stackable=True),
        ]
        before = list(promos)
        result = promotions.best_combination(promos, lines)
        self.assertEqual(result, {"codes": ["B", "C", "D"], "discount_cents": 5600, "total_cents": 10400})
        self.assertEqual(promos, before)
        self.assertEqual(lines, {"LAMP": 1, "MUG": 3})

    def test_best_exclusive_beats_the_stack(self) -> None:
        promos = [
            Promotion("B", "percent", 10, stackable=True),
            Promotion("C", "bogo", sku="MUG", buy=2, get=1, stackable=True),
            Promotion("D", "spend", 1000, min_spend_cents=15000, stackable=True),
            Promotion("A", "percent", 40),
        ]
        result = promotions.best_combination(promos, {"LAMP": 1, "MUG": 3})
        self.assertEqual(result, {"codes": ["A"], "discount_cents": 6400, "total_cents": 9600})

    def test_stacking_rules(self) -> None:
        lines = {"LAMP": 1, "MUG": 3}
        b = Promotion("B", "percent", 10, stackable=True)
        e = Promotion("E", "percent", 15, stackable=True)
        d_ = Promotion("D", "spend", 1000, stackable=True)
        a = Promotion("A", "percent", 20)
        # Two of one kind never stack.
        self.assertEqual(
            promotions.best_combination([b, e], lines),
            {"codes": ["E"], "discount_cents": 2400, "total_cents": 13600},
        )
        self.assertEqual(
            promotions.best_combination([b, d_, e], lines),
            {"codes": ["D", "E"], "discount_cents": 3400, "total_cents": 12600},
        )
        # A promotion that is not stackable only ever applies alone.
        self.assertEqual(
            promotions.best_combination([a, d_], lines),
            {"codes": ["A"], "discount_cents": 3200, "total_cents": 12800},
        )
        # A promotion that does not apply is never part of the result.
        late = Promotion("LATE", "spend", 9000, min_spend_cents=99999, stackable=True)
        self.assertEqual(promotions.best_combination([late, b], lines)["codes"], ["B"])

    def test_best_ties(self) -> None:
        x = Promotion("X", "spend", 500)
        w = Promotion("W", "spend", 500)
        self.assertEqual(
            promotions.best_combination([x, w], {"MUG": 1}),
            {"codes": ["W"], "discount_cents": 500, "total_cents": 2500},
        )
        # The stack is capped at the subtotal, so the single promotion ties with it and wins.
        full = Promotion("P", "percent", 100, stackable=True)
        some = Promotion("S", "spend", 300, stackable=True)
        self.assertEqual(
            promotions.best_combination([some, full], {"PEN": 1}),
            {"codes": ["P"], "discount_cents": 1250, "total_cents": 0},
        )

    def test_best_with_nothing_applicable(self) -> None:
        self.assertEqual(
            promotions.best_combination([], {"MUG": 1}),
            {"codes": [], "discount_cents": 0, "total_cents": 3000},
        )
        late = Promotion("LATE", "spend", 500, min_spend_cents=99999)
        self.assertEqual(
            promotions.best_combination([late], {"MUG": 1, "PEN": 2}),
            {"codes": [], "discount_cents": 0, "total_cents": 5500},
        )
        self.assertEqual(
            promotions.best_combination([Promotion("P", "percent", 10)], {}),
            {"codes": [], "discount_cents": 0, "total_cents": 0},
        )
        with self.assertRaises(ValueError):
            promotions.best_combination([Promotion("A", "percent", 5), Promotion("A", "percent", 6)], {"MUG": 1})


# -------------------------------------------------------------------- 3. shipping rates


class ShippingRates(unittest.TestCase):
    def test_base_rate_bands(self) -> None:
        expected = {1: 399, 500: 399, 501: 599, 2000: 599, 2001: 999, 10000: 999, 10001: 1999, 30000: 1999}
        for weight, rate in expected.items():
            with self.subTest(weight=weight):
                self.assertEqual(shipping_rates.base_rate("local", weight), rate)
        self.assertEqual(shipping_rates.base_rate("national", 500), 599)
        self.assertEqual(shipping_rates.base_rate("national", 501), 899)
        self.assertEqual(shipping_rates.base_rate("national", 10000), 1499)
        self.assertEqual(shipping_rates.base_rate("national", 30000), 2999)
        self.assertEqual(shipping_rates.base_rate("international", 1), 1499)
        self.assertEqual(shipping_rates.base_rate("international", 2000), 2499)
        self.assertEqual(shipping_rates.base_rate("international", 2001), 4999)
        self.assertEqual(shipping_rates.base_rate("international", 30000), 9999)

    def test_base_rate_errors(self) -> None:
        for weight in (0, -5, 30001):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                shipping_rates.base_rate("local", weight)
        with self.assertRaises(KeyError):
            shipping_rates.base_rate("mars", 100)
        with self.assertRaises(KeyError):
            shipping_rates.base_rate("Local", 100)

    def test_plain_quote(self) -> None:
        self.assertEqual(
            shipping_rates.quote("national", 1500, 2000),
            {
                "free_shipping": False,
                "base_cents": 899,
                "fragile_cents": 0,
                "remote_cents": 0,
                "express_cents": 0,
                "total_cents": 899,
            },
        )

    def test_free_shipping_thresholds(self) -> None:
        below = shipping_rates.quote("local", 400, 4999)
        self.assertIs(below["free_shipping"], False)
        self.assertEqual((below["base_cents"], below["total_cents"]), (399, 399))
        at = shipping_rates.quote("local", 400, 5000)
        self.assertIs(at["free_shipping"], True)
        self.assertEqual((at["base_cents"], at["total_cents"]), (0, 0))
        self.assertIs(shipping_rates.quote("national", 400, 9999)["free_shipping"], False)
        self.assertIs(shipping_rates.quote("national", 400, 10000)["free_shipping"], True)
        far = shipping_rates.quote("international", 400, 1000000)
        self.assertIs(far["free_shipping"], False)
        self.assertEqual(far["base_cents"], 1499)

    def test_all_surcharges(self) -> None:
        self.assertEqual(
            shipping_rates.quote("national", 3000, 0, express=True, fragile=True, remote=True),
            {
                "free_shipping": False,
                "base_cents": 1499,
                "fragile_cents": 250,
                "remote_cents": 225,
                "express_cents": 750,
                "total_cents": 2724,
            },
        )

    def test_express_minimum_and_rounding(self) -> None:
        self.assertEqual(shipping_rates.quote("local", 400, 0, express=True)["express_cents"], 500)
        self.assertEqual(shipping_rates.quote("national", 1500, 0, express=True)["express_cents"], 500)
        self.assertEqual(shipping_rates.quote("local", 5000, 0, express=True)["express_cents"], 500)
        self.assertEqual(shipping_rates.quote("international", 400, 0, express=True)["express_cents"], 750)
        self.assertEqual(shipping_rates.quote("local", 20000, 0, express=True)["express_cents"], 1000)
        self.assertEqual(shipping_rates.quote("international", 20000, 0, express=True)["express_cents"], 5000)

    def test_remote_rounding(self) -> None:
        self.assertEqual(shipping_rates.quote("local", 400, 0, remote=True)["remote_cents"], 60)
        self.assertEqual(shipping_rates.quote("national", 400, 0, remote=True)["remote_cents"], 90)
        self.assertEqual(shipping_rates.quote("international", 2000, 0, remote=True)["remote_cents"], 375)
        self.assertEqual(shipping_rates.quote("international", 2000, 0, remote=True)["total_cents"], 2874)

    def test_surcharges_use_the_table_rate_when_shipping_is_free(self) -> None:
        self.assertEqual(
            shipping_rates.quote("local", 5000, 6000, express=True, fragile=True, remote=True),
            {
                "free_shipping": True,
                "base_cents": 0,
                "fragile_cents": 250,
                "remote_cents": 150,
                "express_cents": 500,
                "total_cents": 900,
            },
        )
        self.assertEqual(shipping_rates.quote("national", 30000, 10000, express=True)["total_cents"], 1500)

    def test_quote_errors_and_positional_flags(self) -> None:
        with self.assertRaises(ValueError):
            shipping_rates.quote("local", 400, -1)
        with self.assertRaises(ValueError):
            shipping_rates.quote("local", 0, 1000)
        with self.assertRaises(ValueError):
            shipping_rates.quote("local", 30001, 1000)
        with self.assertRaises(KeyError):
            shipping_rates.quote("mars", 400, 1000)
        self.assertEqual(
            shipping_rates.quote("local", 400, 0, True, False, True),
            shipping_rates.quote("local", 400, 0, express=True, remote=True),
        )
        self.assertEqual(shipping_rates.quote("local", 400, 0, False, True)["fragile_cents"], 250)

    def test_split_parcels(self) -> None:
        self.assertEqual(shipping_rates.split_parcels(65000), [30000, 30000, 5000])
        self.assertEqual(shipping_rates.split_parcels(30000), [30000])
        self.assertEqual(shipping_rates.split_parcels(60000), [30000, 30000])
        self.assertEqual(shipping_rates.split_parcels(1), [1])
        self.assertEqual(shipping_rates.split_parcels(2500, 1000), [1000, 1000, 500])
        self.assertEqual(shipping_rates.split_parcels(999, max_parcel_grams=1000), [999])
        with self.assertRaises(ValueError):
            shipping_rates.split_parcels(0)
        with self.assertRaises(ValueError):
            shipping_rates.split_parcels(100, 0)

    def test_quote_parcels(self) -> None:
        self.assertEqual(
            shipping_rates.quote_parcels("national", [30000, 30000, 5000], 2000, express=True, fragile=True),
            {
                "free_shipping": False,
                "base_cents": 7497,
                "fragile_cents": 750,
                "remote_cents": 0,
                "express_cents": 3750,
                "total_cents": 11997,
            },
        )
        self.assertEqual(
            shipping_rates.quote_parcels("local", [400, 400], 5000, express=True),
            {
                "free_shipping": True,
                "base_cents": 0,
                "fragile_cents": 0,
                "remote_cents": 0,
                "express_cents": 1000,
                "total_cents": 1000,
            },
        )
        self.assertEqual(
            shipping_rates.quote_parcels("international", [400], 0), shipping_rates.quote("international", 400, 0)
        )
        with self.assertRaises(ValueError):
            shipping_rates.quote_parcels("local", [], 1000)
        with self.assertRaises(ValueError):
            shipping_rates.quote_parcels("local", [400, 30001], 1000)

    def test_business_days(self) -> None:
        self.assertEqual(shipping_rates.business_days_for("local"), 1)
        self.assertEqual(shipping_rates.business_days_for("national"), 3)
        self.assertEqual(shipping_rates.business_days_for("international"), 7)
        self.assertEqual(shipping_rates.business_days_for("local", express=True), 1)
        self.assertEqual(shipping_rates.business_days_for("national", True), 2)
        self.assertEqual(shipping_rates.business_days_for("international", True), 4)
        with self.assertRaises(KeyError):
            shipping_rates.business_days_for("mars")

    def test_add_working_days(self) -> None:
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-06"), 1), d("2026-11-09"))
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-09"), 5), d("2026-11-16"))
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-07"), 0), d("2026-11-07"))
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-07"), 1), d("2026-11-09"))
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-08"), 2), d("2026-11-10"))
        with self.assertRaises(ValueError):
            shipping_rates.add_working_days(d("2026-11-09"), -1)
        thanksgiving = d("2026-11-26")
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-25"), 1, [thanksgiving]), d("2026-11-27"))
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-25"), 2, {thanksgiving}), d("2026-11-30"))
        self.assertEqual(
            shipping_rates.add_working_days(d("2026-11-25"), 1, holidays=(thanksgiving, d("2026-11-27"))),
            d("2026-11-30"),
        )
        # A holiday at the weekend changes nothing, and neither does one on the start day.
        self.assertEqual(shipping_rates.add_working_days(d("2026-11-06"), 1, [d("2026-11-07")]), d("2026-11-09"))
        self.assertEqual(shipping_rates.add_working_days(thanksgiving, 1, [thanksgiving]), d("2026-11-27"))
        self.assertEqual(shipping_rates.add_working_days(thanksgiving, 0, [thanksgiving]), thanksgiving)

    def test_estimate_delivery(self) -> None:
        self.assertEqual(shipping_rates.estimate_delivery("national", d("2026-11-05")), d("2026-11-10"))
        self.assertEqual(shipping_rates.estimate_delivery("national", d("2026-11-05"), express=True), d("2026-11-09"))
        self.assertEqual(shipping_rates.estimate_delivery("local", d("2026-11-07")), d("2026-11-09"))
        self.assertEqual(
            shipping_rates.estimate_delivery("international", d("2026-11-23"), holidays=[d("2026-11-26")]),
            d("2026-12-03"),
        )
        self.assertEqual(
            shipping_rates.estimate_delivery("international", d("2026-11-23"), True, [d("2026-11-26")]),
            d("2026-11-30"),
        )
        with self.assertRaises(KeyError):
            shipping_rates.estimate_delivery("mars", d("2026-11-05"))


# -------------------------------------------------------------------------- 4. invoices

EXAMPLE_INVOICE = (
    "INVOICE INV-2026-0001\n"
    "Customer: Ann Lee\n"
    "Issued: 2026-11-09\n"
    "Due: 2026-12-09\n"
    "\n"
    "Qty  Description                   Unit       Total\n"
    "---------------------------------------------------\n"
    "  2  Stoneware mug               $30.00      $60.00\n"
    "  1  Gift wrapping                $4.50       $4.50\n"
    "---------------------------------------------------\n"
    "Subtotal                                     $64.50\n"
    "Tax 0.00%                                     $0.00\n"
    "Tax 8.25%                                     $4.95\n"
    "Total                                        $69.45\n"
)

MIXED_LINES = [
    ("Chair", 1, 1000, 825),
    ("Cushion", 2, 333, 825),
    ("Book", 1, 500, 0),
    ("Service", 1, 999, 2000),
]


class Invoices(unittest.TestCase):
    def mixed(self) -> list:
        return [InvoiceLine(*fields) for fields in MIXED_LINES]

    def test_format_money_and_rate(self) -> None:
        self.assertEqual(invoices.format_money(123456), "$1234.56")
        self.assertEqual(invoices.format_money(5), "$0.05")
        self.assertEqual(invoices.format_money(0), "$0.00")
        self.assertEqual(invoices.format_money(100), "$1.00")
        self.assertEqual(invoices.format_money(100000000), "$1000000.00")
        self.assertEqual(invoices.format_rate(825), "8.25%")
        self.assertEqual(invoices.format_rate(0), "0.00%")
        self.assertEqual(invoices.format_rate(2000), "20.00%")
        self.assertEqual(invoices.format_rate(5), "0.05%")
        self.assertEqual(invoices.format_rate(10000), "100.00%")

    def test_invoice_line(self) -> None:
        line = InvoiceLine("Stoneware mug", 2, 3000)
        self.assertEqual(line.tax_rate_bp, 0)
        self.assertEqual(line.total_cents, 6000)
        self.assertEqual(InvoiceLine("Free sample", 3, 0).total_cents, 0)
        self.assertEqual(
            InvoiceLine("a", 1, 2, 3),
            InvoiceLine(description="a", quantity=1, unit_price_cents=2, tax_rate_bp=3),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            line.quantity = 3  # type: ignore[misc]
        for fields in (("x", 0, 100), ("x", 1, -1), ("x", 1, 100, -1)):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                InvoiceLine(*fields)

    def test_subtotal(self) -> None:
        self.assertEqual(invoices.subtotal_cents(self.mixed()), 3165)
        self.assertEqual(invoices.subtotal_cents([]), 0)

    def test_tax_breakdown(self) -> None:
        self.assertEqual(
            invoices.tax_breakdown(self.mixed()),
            [(0, 500, 0), (825, 1666, 137), (2000, 999, 200)],
        )
        self.assertEqual(invoices.tax_breakdown([]), [])

    def test_tax_is_rounded_once_per_rate(self) -> None:
        one = InvoiceLine("Chair", 1, 1000, 825)
        self.assertEqual(invoices.tax_breakdown([one]), [(825, 1000, 83)])
        self.assertEqual(invoices.tax_breakdown([one, one]), [(825, 2000, 165)])
        self.assertEqual(invoices.total_cents([one, one]), 2165)

    def test_total(self) -> None:
        self.assertEqual(invoices.total_cents(self.mixed()), 3502)
        self.assertEqual(invoices.total_cents([InvoiceLine("Book", 2, 500)]), 1000)
        self.assertEqual(invoices.total_cents([]), 0)

    def test_due_date(self) -> None:
        self.assertEqual(invoices.due_date(d("2026-11-09")), d("2026-12-09"))
        self.assertEqual(invoices.due_date(d("2026-11-06"), 0), d("2026-11-06"))
        self.assertEqual(invoices.due_date(d("2026-11-06"), 1), d("2026-11-09"))
        self.assertEqual(invoices.due_date(d("2026-11-06"), 2), d("2026-11-09"))
        self.assertEqual(invoices.due_date(d("2026-11-06"), 3), d("2026-11-09"))
        self.assertEqual(invoices.due_date(d("2026-11-06"), terms_days=7), d("2026-11-13"))
        self.assertEqual(invoices.due_date(d("2026-11-07"), 0), d("2026-11-09"))
        with self.assertRaises(ValueError):
            invoices.due_date(d("2026-11-06"), -1)

    def test_late_fee(self) -> None:
        due = d("2026-12-09")
        self.assertEqual(invoices.late_fee_cents(10000, due, due), 0)
        self.assertEqual(invoices.late_fee_cents(10000, due, d("2026-12-01")), 0)
        self.assertEqual(invoices.late_fee_cents(10000, due, d("2026-12-10")), 150)
        self.assertEqual(invoices.late_fee_cents(10000, due, due + timedelta(days=30)), 150)
        self.assertEqual(invoices.late_fee_cents(10000, due, due + timedelta(days=31)), 300)
        self.assertEqual(invoices.late_fee_cents(10000, due, due + timedelta(days=90)), 450)

    def test_late_fee_rounding(self) -> None:
        due = d("2026-12-09")
        on = d("2026-12-10")
        self.assertEqual(invoices.late_fee_cents(100, due, on), 2)
        self.assertEqual(invoices.late_fee_cents(3333, due, on), 50)
        self.assertEqual(invoices.late_fee_cents(33, due, on), 0)
        self.assertEqual(invoices.late_fee_cents(100, due, due + timedelta(days=61)), 5)

    def test_invoice_numbers(self) -> None:
        book = InvoiceBook()
        self.assertEqual(book.issued(), [])
        self.assertEqual(book.next_number(d("2026-11-09")), "INV-2026-0001")
        self.assertEqual(book.next_number(d("2026-12-31")), "INV-2026-0002")
        self.assertEqual(book.next_number(d("2027-01-01")), "INV-2027-0001")
        self.assertEqual(book.next_number(d("2026-01-01")), "INV-2026-0003")
        self.assertEqual(book.next_number(d("2025-06-01")), "INV-2025-0001")
        issued = book.issued()
        self.assertEqual(
            issued, ["INV-2026-0001", "INV-2026-0002", "INV-2027-0001", "INV-2026-0003", "INV-2025-0001"]
        )
        issued.append("junk")
        self.assertEqual(len(book.issued()), 5)

    def test_invoice_books_are_separate(self) -> None:
        credit = InvoiceBook("CN")
        other = InvoiceBook(prefix="INV")
        self.assertEqual(credit.next_number(d("2026-11-09")), "CN-2026-0001")
        self.assertEqual(credit.next_number(d("2026-11-09")), "CN-2026-0002")
        self.assertEqual(other.next_number(d("2026-11-09")), "INV-2026-0001")
        self.assertEqual(other.issued(), ["INV-2026-0001"])
        busy = InvoiceBook()
        numbers = [busy.next_number(d("2026-01-01")) for _ in range(10000)]
        self.assertEqual(numbers[9998], "INV-2026-9999")
        self.assertEqual(numbers[9999], "INV-2026-10000")

    def test_render_the_example(self) -> None:
        lines = [InvoiceLine("Stoneware mug", 2, 3000, 825), InvoiceLine("Gift wrapping", 1, 450)]
        self.assertEqual(invoices.render_invoice("INV-2026-0001", "Ann Lee", d("2026-11-09"), lines), EXAMPLE_INVOICE)

    def test_render_long_descriptions_terms_and_big_amounts(self) -> None:
        lines = [
            InvoiceLine("Extra long description of a thing", 12, 123456, 2000),
            InvoiceLine("Exactly twenty-four chrs", 100, 1, 2000),
        ]
        text = invoices.render_invoice("CN-2026-0042", "Bob & Co", d("2026-11-09"), lines, 10)
        self.assertEqual(
            text,
            "INVOICE CN-2026-0042\n"
            "Customer: Bob & Co\n"
            "Issued: 2026-11-09\n"
            "Due: 2026-11-19\n"
            "\n"
            "Qty  Description                   Unit       Total\n"
            "---------------------------------------------------\n"
            " 12  Extra long description o  $1234.56   $14814.72\n"
            "100  Exactly twenty-four chrs     $0.01       $1.00\n"
            "---------------------------------------------------\n"
            "Subtotal                                  $14815.72\n"
            "Tax 20.00%                                 $2963.14\n"
            "Total                                     $17778.86\n",
        )

    def test_render_rules(self) -> None:
        with self.assertRaises(ValueError):
            invoices.render_invoice("INV-2026-0001", "Ann Lee", d("2026-11-09"), [])
        text = invoices.render_invoice("INV-2026-0007", "Ann Lee", d("2026-11-06"), self.mixed(), terms_days=1)
        rows = text.split("\n")
        self.assertEqual(rows[-1], "")
        self.assertEqual(len(rows) - 1, 17)
        self.assertEqual(rows[3], "Due: 2026-11-09")
        self.assertEqual(rows[4], "")
        for row in rows[:-1]:
            self.assertEqual(row, row.rstrip())
        for index in range(5, 17):
            self.assertEqual(len(rows[index]), 51, rows[index])
        self.assertEqual(rows[7], "  1  Chair" + " " * 23 + "$10.00" + " " * 6 + "$10.00")
        self.assertEqual(rows[12], "Subtotal" + " " * 37 + "$31.65")
        self.assertEqual(rows[13], "Tax 0.00%" + " " * 37 + "$0.00")
        self.assertEqual(rows[14], "Tax 8.25%" + " " * 37 + "$1.37")
        self.assertEqual(rows[15], "Tax 20.00%" + " " * 36 + "$2.00")
        self.assertEqual(rows[16], "Total" + " " * 40 + "$35.02")


# --------------------------------------------------------------------------- 5. returns


class Returns(unittest.TestCase):
    def test_window_days(self) -> None:
        self.assertEqual(returns_policy.window_days("changed_mind"), 30)
        self.assertEqual(returns_policy.window_days("wrong_item"), 60)
        self.assertEqual(returns_policy.window_days("defective"), 90)
        for reason in ("bored", "Defective", ""):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                returns_policy.window_days(reason)

    def test_is_within_window(self) -> None:
        delivered = d("2026-10-01")
        check = returns_policy.is_within_window
        self.assertIs(check(delivered, d("2026-10-01"), "changed_mind"), True)
        self.assertIs(check(delivered, d("2026-10-31"), "changed_mind"), True)
        self.assertIs(check(delivered, d("2026-11-01"), "changed_mind"), False)
        self.assertIs(check(delivered, d("2026-09-30"), "changed_mind"), False)
        self.assertIs(check(delivered, d("2026-11-30"), "wrong_item"), True)
        self.assertIs(check(delivered, d("2026-12-01"), "wrong_item"), False)
        self.assertIs(check(delivered, d("2026-12-30"), "defective"), True)
        self.assertIs(check(delivered, d("2026-12-31"), "defective"), False)
        with self.assertRaises(ValueError):
            check(delivered, d("2026-10-02"), "bored")

    def test_restocking_fee(self) -> None:
        fee = returns_policy.restocking_fee_cents
        self.assertEqual(fee(1250, "changed_mind"), 188)
        self.assertEqual(fee(1250, "changed_mind", True), 313)
        self.assertEqual(fee(1250, "changed_mind", opened=False), 188)
        self.assertEqual(fee(7000, "changed_mind"), 1050)
        self.assertEqual(fee(0, "changed_mind"), 0)
        self.assertEqual(fee(1250, "defective", True), 0)
        self.assertEqual(fee(1250, "wrong_item"), 0)
        with self.assertRaises(ValueError):
            fee(-1, "changed_mind")
        with self.assertRaises(ValueError):
            fee(1000, "bored")

    def test_refund_changed_mind(self) -> None:
        items = [(3000, 2), (1250, 1)]
        self.assertEqual(
            returns_policy.refund_amount(items, "changed_mind", shipping_paid_cents=599),
            {
                "goods_cents": 7250,
                "restocking_fee_cents": 1088,
                "label_fee_cents": 500,
                "shipping_refund_cents": 0,
                "refund_cents": 5662,
            },
        )
        self.assertEqual(items, [(3000, 2), (1250, 1)])
        self.assertEqual(
            returns_policy.refund_amount(items, "changed_mind", True, 599),
            {
                "goods_cents": 7250,
                "restocking_fee_cents": 1813,
                "label_fee_cents": 500,
                "shipping_refund_cents": 0,
                "refund_cents": 4937,
            },
        )

    def test_refund_fee_is_worked_out_on_the_sum(self) -> None:
        # Per item this would be 188 + 188; on the sum it is 375.
        result = returns_policy.refund_amount([(1250, 1), (1250, 1)], "changed_mind")
        self.assertEqual(result["restocking_fee_cents"], 375)
        self.assertEqual(result["refund_cents"], 1625)

    def test_refund_defective_and_wrong_item(self) -> None:
        items = [(3000, 2), (1250, 1)]
        expected = {
            "goods_cents": 7250,
            "restocking_fee_cents": 0,
            "label_fee_cents": 0,
            "shipping_refund_cents": 599,
            "refund_cents": 7849,
        }
        self.assertEqual(returns_policy.refund_amount(items, "defective", shipping_paid_cents=599), expected)
        self.assertEqual(returns_policy.refund_amount(items, "wrong_item", True, 599), expected)
        self.assertEqual(returns_policy.refund_amount(items, "defective")["refund_cents"], 7250)

    def test_refund_is_never_negative(self) -> None:
        self.assertEqual(
            returns_policy.refund_amount([(400, 1)], "changed_mind"),
            {
                "goods_cents": 400,
                "restocking_fee_cents": 60,
                "label_fee_cents": 500,
                "shipping_refund_cents": 0,
                "refund_cents": 0,
            },
        )
        self.assertEqual(returns_policy.refund_amount([(588, 1)], "changed_mind")["refund_cents"], 0)
        self.assertEqual(returns_policy.refund_amount([(600, 1)], "changed_mind")["refund_cents"], 10)

    def test_refund_errors(self) -> None:
        with self.assertRaises(ValueError):
            returns_policy.refund_amount([], "defective")
        with self.assertRaises(ValueError):
            returns_policy.refund_amount([(-1, 1)], "defective")
        with self.assertRaises(ValueError):
            returns_policy.refund_amount([(100, 0)], "defective")
        with self.assertRaises(ValueError):
            returns_policy.refund_amount([(100, 1)], "defective", shipping_paid_cents=-1)
        with self.assertRaises(ValueError):
            returns_policy.refund_amount([(100, 1)], "bored")

    def test_can_transition(self) -> None:
        allowed = {
            ("requested", "approved"),
            ("requested", "rejected"),
            ("requested", "cancelled"),
            ("approved", "shipped"),
            ("approved", "cancelled"),
            ("shipped", "received"),
            ("received", "refunded"),
        }
        states = ["requested", "approved", "rejected", "cancelled", "shipped", "received", "refunded", "nope"]
        for origin in states:
            for target in states:
                with self.subTest(origin=origin, target=target):
                    self.assertIs(returns_policy.can_transition(origin, target), (origin, target) in allowed)

    def test_new_rma(self) -> None:
        rma = Rma("R-1", d("2026-10-01"), d("2026-10-31"), "changed_mind")
        self.assertEqual(
            (rma.rma_id, rma.delivered_on, rma.requested_on, rma.reason),
            ("R-1", d("2026-10-01"), d("2026-10-31"), "changed_mind"),
        )
        self.assertEqual(rma.state, "requested")
        self.assertEqual(rma.history, [("requested", d("2026-10-31"))])
        self.assertIs(rma.closed, False)
        self.assertEqual(rma.allowed_next(), ["approved", "cancelled", "rejected"])
        with self.assertRaises(ReturnWindowClosed):
            Rma("R-2", d("2026-10-01"), d("2026-11-01"), "changed_mind")
        with self.assertRaises(ReturnWindowClosed):
            Rma("R-3", d("2026-10-01"), d("2026-09-30"), "defective")
        with self.assertRaises(ValueError):
            Rma("R-4", d("2026-10-01"), d("2026-10-02"), "bored")
        Rma("R-5", d("2026-10-01"), d("2026-11-01"), "defective")
        for error in (ReturnWindowClosed, InvalidTransition):
            self.assertTrue(issubclass(error, Exception))
            self.assertFalse(issubclass(error, ValueError))

    def test_full_path(self) -> None:
        rma = Rma("R-1", d("2026-10-01"), d("2026-10-05"), "defective")
        rma.transition("approved", d("2026-10-05"))
        self.assertEqual(rma.allowed_next(), ["cancelled", "shipped"])
        rma.transition("shipped", d("2026-10-07"))
        self.assertEqual(rma.allowed_next(), ["received"])
        rma.transition("received", d("2026-10-12"))
        self.assertEqual(rma.allowed_next(), ["refunded"])
        self.assertIs(rma.closed, False)
        self.assertIsNone(rma.transition("refunded", d("2026-10-12")))
        self.assertEqual(rma.state, "refunded")
        self.assertEqual(rma.allowed_next(), [])
        self.assertIs(rma.closed, True)
        self.assertEqual(
            rma.history,
            [
                ("requested", d("2026-10-05")),
                ("approved", d("2026-10-05")),
                ("shipped", d("2026-10-07")),
                ("received", d("2026-10-12")),
                ("refunded", d("2026-10-12")),
            ],
        )

    def test_invalid_transitions_change_nothing(self) -> None:
        rma = Rma("R-1", d("2026-10-01"), d("2026-10-05"), "wrong_item")
        for target in ("shipped", "requested", "refunded", "nope"):
            with self.subTest(target=target), self.assertRaises(InvalidTransition):
                rma.transition(target, d("2026-10-06"))
        self.assertEqual(rma.state, "requested")
        self.assertEqual(rma.history, [("requested", d("2026-10-05"))])
        rma.transition("rejected", d("2026-10-06"))
        self.assertIs(rma.closed, True)
        for target in ("approved", "cancelled", "rejected"):
            with self.subTest(target=target), self.assertRaises(InvalidTransition):
                rma.transition(target, d("2026-10-07"))
        self.assertEqual(len(rma.history), 2)
        other = Rma("R-2", d("2026-10-01"), d("2026-10-05"), "wrong_item")
        other.transition("cancelled", d("2026-10-05"))
        self.assertEqual((other.state, other.closed), ("cancelled", True))

    def test_transition_dates(self) -> None:
        rma = Rma("R-1", d("2026-10-01"), d("2026-10-05"), "wrong_item")
        with self.assertRaises(ValueError):
            rma.transition("approved", d("2026-10-04"))
        self.assertEqual(rma.state, "requested")
        self.assertEqual(len(rma.history), 1)
        # The transition check comes before the date check.
        with self.assertRaises(InvalidTransition):
            rma.transition("shipped", d("2026-10-04"))
        rma.transition("approved", d("2026-10-05"))
        with self.assertRaises(ValueError):
            rma.transition("shipped", d("2026-10-04"))
        self.assertEqual(rma.state, "approved")

    def test_ship_within_14_days_of_approval(self) -> None:
        on_time = Rma("R-1", d("2026-10-01"), d("2026-10-20"), "changed_mind")
        on_time.transition("approved", d("2026-11-02"))
        on_time.transition("shipped", d("2026-11-16"))
        self.assertEqual(on_time.state, "shipped")
        late = Rma("R-2", d("2026-10-01"), d("2026-10-20"), "changed_mind")
        late.transition("approved", d("2026-11-02"))
        with self.assertRaises(ReturnWindowClosed):
            late.transition("shipped", d("2026-11-17"))
        self.assertEqual(late.state, "approved")
        self.assertEqual(late.history, [("requested", d("2026-10-20")), ("approved", d("2026-11-02"))])
        late.transition("cancelled", d("2026-11-17"))
        self.assertEqual(late.state, "cancelled")


# ---------------------------------------------------------------------------- 6. search


def shelf() -> list[dict]:
    return [
        {
            "sku": "MUG-BLU",
            "name": "Blue Stoneware Mug",
            "description": "A sturdy mug for coffee and tea",
            "category": "kitchen",
            "price_cents": 3000,
            "tags": ["ceramic", "blue", "gift"],
            "in_stock": True,
        },
        {
            "sku": "MUG-RED",
            "name": "Red Travel Mug",
            "description": "Insulated steel, keeps coffee hot",
            "category": "kitchen",
            "price_cents": 4500,
            "tags": ["steel", "red", "travel"],
            "in_stock": False,
        },
        {
            "sku": "LAMP-DSK",
            "name": "Desk Lamp",
            "description": "Warm light for the desk with a blue shade",
            "category": "lighting",
            "price_cents": 7000,
            "tags": ["led", "office"],
            "in_stock": True,
        },
        {
            "sku": "PEN-FTN",
            "name": "Fountain Pen",
            "description": "Smooth steel nib",
            "category": "office",
            "price_cents": 1250,
            "tags": ["gift", "office", "steel nib"],
            "in_stock": True,
        },
        {
            "sku": "NOTE-A5",
            "name": "Notebook A5",
            "description": "Dotted pages, travel size",
            "category": "office",
            "price_cents": 1250,
            "tags": ["paper", "travel", "gift", "gift"],
            "in_stock": True,
        },
        {
            "sku": "MAT-DSK",
            "name": "desk mat",
            "description": "Mugs and pens stay put",
            "category": "office",
            "price_cents": 2500,
            "tags": ["office"],
            "in_stock": False,
        },
    ]


def skus(result: dict) -> list[str]:
    return [product["sku"] for product in result["items"]]


class Search(unittest.TestCase):
    def setUp(self) -> None:
        self.products = shelf()
        self.by_sku = {product["sku"]: product for product in self.products}

    def test_tokenize(self) -> None:
        self.assertEqual(search.tokenize("The Blue-Green mug, 2 of 3!"), ["blue", "green", "mug", "2", "3"])
        self.assertEqual(search.tokenize(""), [])
        self.assertEqual(search.tokenize("the of AND With a An FOR"), [])
        self.assertEqual(search.tokenize("Mug mug MUG"), ["mug", "mug", "mug"])
        self.assertEqual(search.tokenize("A5-size  note_book"), ["a5", "size", "note", "book"])
        self.assertEqual(search.tokenize("café au lait 50%"), ["caf", "au", "lait", "50"])
        self.assertEqual(search.tokenize("theatre android"), ["theatre", "android"])

    def test_score_parts(self) -> None:
        mug = self.by_sku["MUG-BLU"]
        self.assertEqual(search.score(mug, "mug"), 6)
        self.assertEqual(search.score(mug, "blue"), 8)
        self.assertEqual(search.score(mug, "blue mug"), 14)
        self.assertEqual(search.score(mug, "gift"), 3)
        self.assertEqual(search.score(self.by_sku["LAMP-DSK"], "blue"), 1)
        self.assertEqual(search.score(self.by_sku["PEN-FTN"], "steel"), 4)
        self.assertEqual(search.score(self.by_sku["MUG-RED"], "travel"), 8)

    def test_score_prefix_of_a_name_token(self) -> None:
        self.assertEqual(search.score(self.by_sku["PEN-FTN"], "fount"), 2)
        self.assertEqual(search.score(self.by_sku["MUG-BLU"], "st"), 2)
        # A prefix only counts against the name: "mug" is not the description token "mugs".
        self.assertEqual(search.score(self.by_sku["MAT-DSK"], "mug"), 0)
        self.assertEqual(search.score(self.by_sku["MAT-DSK"], "mugs"), 1)
        self.assertEqual(search.score(self.by_sku["NOTE-A5"], "trav"), 0)
        # "desk" is a name token (5) and a description token (1): the prefix points are not added.
        self.assertEqual(search.score(self.by_sku["LAMP-DSK"], "desk"), 6)

    def test_score_zero_cases(self) -> None:
        mug = self.by_sku["MUG-BLU"]
        self.assertEqual(search.score(mug, ""), 0)
        self.assertEqual(search.score(mug, "the"), 0)
        self.assertEqual(search.score(mug, "blue lamp"), 0)
        self.assertEqual(search.score(mug, "mug mug Mug"), 6)
        self.assertEqual(search.score(mug, "The MUG!"), 6)

    def test_search_without_a_query(self) -> None:
        result = search.search(self.products)
        self.assertEqual(set(result), {"total", "pages", "page", "items", "facets"})
        self.assertEqual((result["total"], result["pages"], result["page"]), (6, 1, 1))
        self.assertEqual(skus(result), ["LAMP-DSK", "MAT-DSK", "MUG-BLU", "MUG-RED", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(result["items"][0], self.by_sku["LAMP-DSK"])
        self.assertEqual(skus(search.search(self.products, "the")), skus(result))
        self.assertEqual(self.products, shelf())

    def test_search_by_relevance(self) -> None:
        self.assertEqual(skus(search.search(self.products, "mug")), ["MUG-BLU", "MUG-RED"])
        self.assertEqual(skus(search.search(self.products, "travel")), ["MUG-RED", "NOTE-A5"])
        self.assertEqual(skus(search.search(self.products, "desk")), ["LAMP-DSK", "MAT-DSK"])
        self.assertEqual(skus(search.search(self.products, "steel")), ["MUG-RED", "PEN-FTN"])
        self.assertEqual(skus(search.search(self.products, "gift")), ["MUG-BLU", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(skus(search.search(self.products, "blue")), ["MUG-BLU", "LAMP-DSK"])
        self.assertEqual(skus(search.search(self.products, "Blue MUG")), ["MUG-BLU"])
        self.assertEqual(skus(search.search(self.products, query="m")), ["MAT-DSK", "MUG-BLU", "MUG-RED"])

    def test_no_matches(self) -> None:
        result = search.search(self.products, "zzz")
        self.assertEqual(
            result,
            {"total": 0, "pages": 0, "page": 1, "items": [], "facets": {"category": {}, "tags": {}}},
        )
        self.assertEqual(search.search([], "mug")["total"], 0)
        self.assertEqual(search.search([])["pages"], 0)

    def test_filters(self) -> None:
        find = lambda **filters: skus(search.search(self.products, **filters))  # noqa: E731
        self.assertEqual(find(category="office"), ["MAT-DSK", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(find(category="Office"), [])
        self.assertEqual(find(min_price_cents=1250, max_price_cents=3000), ["MAT-DSK", "MUG-BLU", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(find(min_price_cents=4500), ["LAMP-DSK", "MUG-RED"])
        self.assertEqual(find(max_price_cents=1249), [])
        self.assertEqual(find(in_stock_only=True), ["LAMP-DSK", "MUG-BLU", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(find(category="office", in_stock_only=True, max_price_cents=1250), ["NOTE-A5", "PEN-FTN"])

    def test_tag_filter(self) -> None:
        find = lambda tags: skus(search.search(self.products, tags=tags))  # noqa: E731
        self.assertEqual(find(["gift", "office"]), ["PEN-FTN"])
        self.assertEqual(find(["gift"]), ["MUG-BLU", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(find(["steel"]), ["MUG-RED"])
        self.assertEqual(find(["Gift"]), [])
        self.assertEqual(len(find([])), 6)
        self.assertEqual(skus(search.search(self.products, "mug", tags=["travel"])), ["MUG-RED"])

    def test_sorts(self) -> None:
        order = lambda sort: skus(search.search(self.products, sort=sort))  # noqa: E731
        self.assertEqual(order("price_asc"), ["NOTE-A5", "PEN-FTN", "MAT-DSK", "MUG-BLU", "MUG-RED", "LAMP-DSK"])
        self.assertEqual(order("price_desc"), ["LAMP-DSK", "MUG-RED", "MUG-BLU", "MAT-DSK", "NOTE-A5", "PEN-FTN"])
        self.assertEqual(order("name"), ["MUG-BLU", "LAMP-DSK", "MAT-DSK", "PEN-FTN", "NOTE-A5", "MUG-RED"])
        self.assertEqual(skus(search.search(self.products, "mug", sort="price_desc")), ["MUG-RED", "MUG-BLU"])
        self.assertEqual(skus(search.search(self.products, "gift", sort="name")), ["MUG-BLU", "PEN-FTN", "NOTE-A5"])
        for sort in ("newest", "Relevance", ""):
            with self.subTest(sort=sort), self.assertRaises(ValueError):
                search.search(self.products, sort=sort)

    def test_pagination(self) -> None:
        first = search.search(self.products, page_size=4)
        self.assertEqual((first["total"], first["pages"], first["page"]), (6, 2, 1))
        self.assertEqual(skus(first), ["LAMP-DSK", "MAT-DSK", "MUG-BLU", "MUG-RED"])
        second = search.search(self.products, page=2, page_size=4)
        self.assertEqual((second["total"], second["pages"], second["page"]), (6, 2, 2))
        self.assertEqual(skus(second), ["NOTE-A5", "PEN-FTN"])
        third = search.search(self.products, page=3, page_size=4)
        self.assertEqual((third["total"], third["pages"], third["page"], third["items"]), (6, 2, 3, []))
        self.assertEqual(search.search(self.products, page_size=6)["pages"], 1)
        self.assertEqual(search.search(self.products, page_size=5)["pages"], 2)
        self.assertEqual(search.search(self.products, page_size=1)["pages"], 6)
        with self.assertRaises(ValueError):
            search.search(self.products, page=0)
        with self.assertRaises(ValueError):
            search.search(self.products, page_size=0)

    def test_facets_over_everything(self) -> None:
        facets = search.search(self.products)["facets"]
        self.assertEqual(facets["category"], {"kitchen": 2, "lighting": 1, "office": 3})
        self.assertEqual(
            facets["tags"],
            {
                "ceramic": 1,
                "blue": 1,
                "gift": 3,
                "steel": 1,
                "red": 1,
                "travel": 2,
                "led": 1,
                "office": 3,
                "steel nib": 1,
                "paper": 1,
            },
        )
        self.assertEqual(set(facets), {"category", "tags"})

    def test_facets_follow_the_matches_not_the_page(self) -> None:
        result = search.search(self.products, "gift", page_size=1)
        self.assertEqual((result["total"], result["pages"], skus(result)), (3, 3, ["MUG-BLU"]))
        self.assertEqual(result["facets"]["category"], {"kitchen": 1, "office": 2})
        self.assertEqual(
            result["facets"]["tags"],
            {"ceramic": 1, "blue": 1, "gift": 3, "office": 1, "steel nib": 1, "paper": 1, "travel": 1},
        )
        filtered = search.search(self.products, category="kitchen", in_stock_only=True, page=5)
        self.assertEqual(filtered["items"], [])
        self.assertEqual(filtered["facets"], {"category": {"kitchen": 1}, "tags": {"ceramic": 1, "blue": 1, "gift": 1}})

    def test_suggest(self) -> None:
        self.assertEqual(search.suggest(self.products, "m"), ["mug", "mat"])
        self.assertEqual(search.suggest(self.products, "m", 1), ["mug"])
        self.assertEqual(search.suggest(self.products, "  La "), ["lamp"])
        self.assertEqual(search.suggest(self.products, "desk"), ["desk"])
        self.assertEqual(search.suggest(self.products, ""), [])
        self.assertEqual(search.suggest(self.products, "   "), [])
        self.assertEqual(search.suggest(self.products, "x"), [])
        # Only names are used: "coffee" and "steel" appear in descriptions and tags.
        self.assertEqual(search.suggest(self.products, "co"), [])
        self.assertEqual(search.suggest(self.products, "ste"), [])
        with self.assertRaises(ValueError):
            search.suggest(self.products, "m", limit=0)
        names = ["Pencil case", "Pen", "Paper pad", "Pen holder", "The Thermos", "Pad pad pad", "Plum", "Pin", "Pot"]
        products = [dict(shelf()[0], sku=f"S{index}", name=name) for index, name in enumerate(names)]
        self.assertEqual(search.suggest(products, "p"), ["pad", "pen", "paper", "pencil", "pin"])
        self.assertEqual(search.suggest(products, "P", limit=7), ["pad", "pen", "paper", "pencil", "pin", "plum", "pot"])
        self.assertEqual(search.suggest(products, "pen"), ["pen", "pencil"])
        self.assertEqual(search.suggest(products, "th"), ["thermos"])


# --------------------------------------------------------------------- 7. subscriptions


class Subscriptions(unittest.TestCase):
    def weekly(self) -> Subscription:
        return Subscription(d("2026-11-02"), "weekly", 1500)

    def test_add_months(self) -> None:
        add = subscriptions.add_months
        self.assertEqual(add(d("2026-01-15"), 1), d("2026-02-15"))
        self.assertEqual(add(d("2026-01-31"), 1), d("2026-02-28"))
        self.assertEqual(add(d("2024-01-31"), 1), d("2024-02-29"))
        self.assertEqual(add(d("2026-11-30"), 3), d("2027-02-28"))
        self.assertEqual(add(d("2026-12-31"), 2), d("2027-02-28"))
        self.assertEqual(add(d("2026-05-31"), 0), d("2026-05-31"))
        self.assertEqual(add(d("2026-05-31"), 12), d("2027-05-31"))
        self.assertEqual(add(d("2026-05-31"), 25), d("2028-06-30"))
        add = subscriptions.add_months
        self.assertEqual(add(d("2026-02-28"), 1, 31), d("2026-03-31"))
        self.assertEqual(add(d("2026-02-28"), 2, anchor_day=31), d("2026-04-30"))
        self.assertEqual(add(d("2026-03-15"), 1, 1), d("2026-04-01"))
        self.assertEqual(add(d("2026-03-15"), 0, 31), d("2026-03-31"))
        for months, anchor in ((-1, None), (1, 0), (1, 32)):
            with self.subTest(months=months, anchor=anchor), self.assertRaises(ValueError):
                add(d("2026-03-15"), months, anchor)

    def test_schedule_in_days(self) -> None:
        self.assertEqual(
            subscriptions.schedule(d("2026-11-09"), "weekly", 3),
            [d("2026-11-09"), d("2026-11-16"), d("2026-11-23")],
        )
        self.assertEqual(
            subscriptions.schedule(d("2026-11-09"), "biweekly", 4),
            [d("2026-11-09"), d("2026-11-23"), d("2026-12-07"), d("2026-12-21")],
        )
        self.assertEqual(subscriptions.schedule(d("2026-11-09"), "weekly", 1), [d("2026-11-09")])

    def test_schedule_in_months_is_clamped_from_the_start_date(self) -> None:
        self.assertEqual(
            subscriptions.schedule(d("2026-01-31"), "monthly", 5),
            [d("2026-01-31"), d("2026-02-28"), d("2026-03-31"), d("2026-04-30"), d("2026-05-31")],
        )
        self.assertEqual(
            subscriptions.schedule(d("2024-01-30"), "monthly", 3),
            [d("2024-01-30"), d("2024-02-29"), d("2024-03-30")],
        )
        self.assertEqual(
            subscriptions.schedule(d("2026-11-15"), "monthly", 3),
            [d("2026-11-15"), d("2026-12-15"), d("2027-01-15")],
        )
        self.assertEqual(
            subscriptions.schedule(d("2026-11-30"), "quarterly", 4),
            [d("2026-11-30"), d("2027-02-28"), d("2027-05-30"), d("2027-08-30")],
        )
        self.assertEqual(
            subscriptions.schedule(d("2024-02-29"), "yearly", 5),
            [d("2024-02-29"), d("2025-02-28"), d("2026-02-28"), d("2027-02-28"), d("2028-02-29")],
        )

    def test_schedule_edges(self) -> None:
        self.assertEqual(subscriptions.schedule(d("2026-11-09"), "monthly", 0), [])
        with self.assertRaises(ValueError):
            subscriptions.schedule(d("2026-11-09"), "monthly", -1)
        for interval in ("daily", "Monthly", ""):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                subscriptions.schedule(d("2026-11-09"), interval, 2)

    def test_prorated_refund(self) -> None:
        refund = subscriptions.prorated_refund
        start, end = d("2026-11-01"), d("2026-12-01")
        self.assertEqual(refund(3000, start, end, d("2026-11-16")), 1500)
        self.assertEqual(refund(3000, start, end, start), 3000)
        self.assertEqual(refund(3000, start, end, d("2026-10-20")), 3000)
        self.assertEqual(refund(3000, start, end, d("2026-11-30")), 100)
        self.assertEqual(refund(3000, start, end, end), 0)
        self.assertEqual(refund(3000, start, end, d("2026-12-25")), 0)
        self.assertEqual(refund(1000, d("2026-11-01"), d("2026-11-08"), d("2026-11-05")), 429)
        self.assertEqual(refund(999, d("2026-11-01"), d("2026-11-03"), d("2026-11-02")), 500)
        self.assertEqual(refund(0, start, end, start), 0)
        with self.assertRaises(ValueError):
            refund(3000, start, start, start)
        with self.assertRaises(ValueError):
            refund(3000, end, start, start)
        with self.assertRaises(ValueError):
            refund(-1, start, end, start)

    def test_new_subscription(self) -> None:
        sub = Subscription(d("2026-01-31"), "monthly", 1000)
        self.assertEqual((sub.start, sub.interval, sub.price_cents), (d("2026-01-31"), "monthly", 1000))
        self.assertIs(sub.is_paused(d("2026-02-01")), False)
        Subscription(d("2026-01-31"), "yearly", 0)
        with self.assertRaises(ValueError):
            Subscription(d("2026-01-31"), "daily", 1000)
        with self.assertRaises(ValueError):
            Subscription(d("2026-01-31"), "monthly", -1)

    def test_charges_between(self) -> None:
        sub = Subscription(d("2026-01-31"), "monthly", 1000)
        self.assertEqual(
            sub.charges_between(d("2026-02-01"), d("2026-04-30")),
            [d("2026-02-28"), d("2026-03-31"), d("2026-04-30")],
        )
        self.assertEqual(sub.charges_between(d("2026-01-31"), d("2026-01-31")), [d("2026-01-31")])
        self.assertEqual(sub.charges_between(d("2025-01-01"), d("2026-02-27")), [d("2026-01-31")])
        self.assertEqual(sub.charges_between(d("2026-02-01"), d("2026-02-27")), [])
        self.assertEqual(sub.charges_between(d("2026-04-30"), d("2026-02-01")), [])
        self.assertEqual(sub.charges_between(d("2025-01-01"), d("2026-01-30")), [])

    def test_pause_and_resume(self) -> None:
        sub = self.weekly()
        self.assertIsNone(sub.pause(d("2026-11-09")))
        self.assertIsNone(sub.resume(d("2026-11-23")))
        self.assertEqual(
            sub.charges_between(d("2026-11-01"), d("2026-12-07")),
            [d("2026-11-02"), d("2026-11-23"), d("2026-11-30"), d("2026-12-07")],
        )
        self.assertIs(sub.is_paused(d("2026-11-08")), False)
        self.assertIs(sub.is_paused(d("2026-11-09")), True)
        self.assertIs(sub.is_paused(d("2026-11-22")), True)
        self.assertIs(sub.is_paused(d("2026-11-23")), False)
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-23"))

    def test_open_pause(self) -> None:
        sub = self.weekly()
        sub.pause(d("2026-11-10"))
        self.assertEqual(sub.charges_between(d("2026-11-01"), d("2026-12-31")), [d("2026-11-02"), d("2026-11-09")])
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-09"))
        self.assertIsNone(sub.next_charge(d("2026-11-09")))
        self.assertIs(sub.is_paused(d("2026-11-09")), False)
        self.assertIs(sub.is_paused(d("2026-11-10")), True)
        self.assertIs(sub.is_paused(d("2030-01-01")), True)
        sub.resume(d("2026-11-30"))
        self.assertEqual(sub.next_charge(d("2026-11-09")), d("2026-11-30"))

    def test_next_charge(self) -> None:
        sub = self.weekly()
        self.assertEqual(sub.next_charge(d("2026-10-01")), d("2026-11-02"))
        self.assertEqual(sub.next_charge(d("2026-11-01")), d("2026-11-02"))
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-09"))
        self.assertEqual(sub.next_charge(d("2026-11-08")), d("2026-11-09"))
        monthly = Subscription(d("2026-01-31"), "monthly", 1000)
        self.assertEqual(monthly.next_charge(d("2026-02-28")), d("2026-03-31"))
        self.assertEqual(monthly.next_charge(d("2027-01-31")), d("2027-02-28"))

    def test_cancel(self) -> None:
        sub = self.weekly()
        self.assertIsNone(sub.cancel(d("2026-11-16")))
        self.assertEqual(sub.charges_between(d("2026-11-01"), d("2026-12-31")), [d("2026-11-02"), d("2026-11-09")])
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-09"))
        self.assertIsNone(sub.next_charge(d("2026-11-09")))
        self.assertIsNone(sub.next_charge(d("2027-01-01")))
        for change in (sub.cancel, sub.pause, sub.resume):
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                change(d("2026-11-20"))
        self.assertIs(sub.is_paused(d("2026-11-20")), False)
        day_after = self.weekly()
        day_after.cancel(d("2026-11-17"))
        self.assertEqual(day_after.next_charge(d("2026-11-09")), d("2026-11-16"))

    def test_pause_and_resume_errors(self) -> None:
        sub = self.weekly()
        with self.assertRaises(ValueError):
            sub.resume(d("2026-11-09"))
        sub.pause(d("2026-11-09"))
        with self.assertRaises(ValueError):
            sub.pause(d("2026-11-12"))
        with self.assertRaises(ValueError):
            sub.resume(d("2026-11-08"))
        self.assertIs(sub.is_paused(d("2026-12-25")), True)
        sub.resume(d("2026-11-20"))
        with self.assertRaises(ValueError):
            sub.pause(d("2026-11-19"))
        self.assertIs(sub.is_paused(d("2026-11-19")), True)
        self.assertIs(sub.is_paused(d("2026-11-20")), False)
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-23"))
        # Pausing again on the day of the resume is fine, and so is resuming on the day of the pause.
        sub.pause(d("2026-11-20"))
        sub.resume(d("2026-11-20"))
        self.assertEqual(sub.next_charge(d("2026-11-02")), d("2026-11-23"))

    def test_several_pauses(self) -> None:
        sub = self.weekly()
        sub.pause(d("2026-11-09"))
        sub.resume(d("2026-11-10"))
        sub.pause(d("2026-11-30"))
        sub.resume(d("2026-12-08"))
        self.assertEqual(
            sub.charges_between(d("2026-11-01"), d("2026-12-20")),
            [d("2026-11-02"), d("2026-11-16"), d("2026-11-23"), d("2026-12-14")],
        )
        self.assertEqual(sub.next_charge(d("2026-11-23")), d("2026-12-14"))
        sub.cancel(d("2026-12-14"))
        self.assertEqual(
            sub.charges_between(d("2026-11-01"), d("2026-12-20")),
            [d("2026-11-02"), d("2026-11-16"), d("2026-11-23")],
        )
        self.assertIsNone(sub.next_charge(d("2026-11-23")))

    def test_period_containing(self) -> None:
        sub = Subscription(d("2026-01-31"), "monthly", 1000)
        self.assertEqual(sub.period_containing(d("2026-03-15")), (d("2026-02-28"), d("2026-03-31")))
        self.assertEqual(sub.period_containing(d("2026-02-28")), (d("2026-02-28"), d("2026-03-31")))
        self.assertEqual(sub.period_containing(d("2026-02-27")), (d("2026-01-31"), d("2026-02-28")))
        self.assertEqual(sub.period_containing(d("2026-01-31")), (d("2026-01-31"), d("2026-02-28")))
        self.assertIsNone(sub.period_containing(d("2026-01-30")))
        sub.pause(d("2026-02-01"))
        sub.resume(d("2026-06-01"))
        sub.cancel(d("2026-06-02"))
        self.assertEqual(sub.period_containing(d("2026-07-04")), (d("2026-06-30"), d("2026-07-31")))
        weekly = self.weekly()
        self.assertEqual(weekly.period_containing(d("2026-11-15")), (d("2026-11-09"), d("2026-11-16")))


# --------------------------------------------------------------------------- 8. bundles


class Bundles(unittest.TestCase):
    def setUp(self) -> None:
        self.stock_before = dict(inventory.STOCK)
        self.set_stock(MUG=40, LAMP=12, PEN=100)
        bundles.reset()
        bundles.define_bundle("desk", {"LAMP": 1, "PEN": 2}, 10)
        bundles.define_bundle("trio", {"MUG": 1, "LAMP": 1, "PEN": 1}, 7)

    def tearDown(self) -> None:
        bundles.reset()
        self.set_stock(**self.stock_before)

    def set_stock(self, **levels: int) -> None:
        # Changed in place, so a module that holds a reference to STOCK sees it too.
        inventory.STOCK.clear()
        inventory.STOCK.update(levels)

    def test_define_get_list_reset(self) -> None:
        self.assertEqual(
            bundles.get_bundle("desk"),
            {"id": "desk", "components": {"LAMP": 1, "PEN": 2}, "discount_percent": 10},
        )
        self.assertIsNone(bundles.define_bundle("cafe", {"MUG": 2}))
        self.assertEqual(bundles.get_bundle("cafe")["discount_percent"], 0)
        self.assertEqual(bundles.list_bundles(), ["cafe", "desk", "trio"])
        self.assertIsNone(bundles.reset())
        self.assertEqual(bundles.list_bundles(), [])
        with self.assertRaises(KeyError):
            bundles.get_bundle("desk")
        bundles.define_bundle("desk", {"PEN": 1}, 100)
        self.assertEqual(bundles.bundle_price_cents("desk"), 0)

    def test_the_module_keeps_its_own_copies(self) -> None:
        components = {"MUG": 2}
        bundles.define_bundle("cafe", components, 5)
        components["MUG"] = 9
        components["PEN"] = 1
        self.assertEqual(bundles.get_bundle("cafe")["components"], {"MUG": 2})
        fetched = bundles.get_bundle("cafe")
        fetched["components"]["LAMP"] = 3
        fetched["discount_percent"] = 50
        self.assertEqual(bundles.get_bundle("cafe"), {"id": "cafe", "components": {"MUG": 2}, "discount_percent": 5})
        self.assertEqual(bundles.list_price_cents("cafe"), 6000)

    def test_define_errors(self) -> None:
        with self.assertRaises(ValueError):
            bundles.define_bundle("desk", {"MUG": 1})
        with self.assertRaises(ValueError):
            bundles.define_bundle("empty", {})
        with self.assertRaises(ValueError):
            bundles.define_bundle("zero", {"MUG": 0})
        with self.assertRaises(ValueError):
            bundles.define_bundle("neg", {"MUG": 1}, -1)
        with self.assertRaises(ValueError):
            bundles.define_bundle("over", {"MUG": 1}, 101)
        with self.assertRaises(KeyError):
            bundles.define_bundle("ghost", {"MUG": 1, "NOPE": 1})
        self.assertEqual(bundles.list_bundles(), ["desk", "trio"])
        self.assertEqual(bundles.get_bundle("desk")["components"], {"LAMP": 1, "PEN": 2})

    def test_unknown_bundle(self) -> None:
        calls = [
            lambda: bundles.get_bundle("nope"),
            lambda: bundles.list_price_cents("nope"),
            lambda: bundles.discount_cents("nope"),
            lambda: bundles.bundle_price_cents("nope"),
            lambda: bundles.discount_breakdown("nope"),
            lambda: bundles.component_prices("nope"),
            lambda: bundles.available("nope"),
            lambda: bundles.missing_components("nope"),
            lambda: bundles.reserve_bundle("nope"),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index), self.assertRaises(KeyError):
                call()

    def test_prices(self) -> None:
        self.assertEqual(bundles.list_price_cents("desk"), 9500)
        self.assertEqual(bundles.discount_cents("desk"), 950)
        self.assertEqual(bundles.bundle_price_cents("desk"), 8550)
        self.assertEqual(bundles.list_price_cents("trio"), 11250)
        self.assertEqual(bundles.discount_cents("trio"), 788)
        self.assertEqual(bundles.bundle_price_cents("trio"), 10462)
        bundles.define_bundle("pen15", {"PEN": 1}, 15)
        self.assertEqual(bundles.discount_cents("pen15"), 188)
        self.assertEqual(bundles.bundle_price_cents("pen15"), 1062)
        bundles.define_bundle("plain", {"PEN": 3})
        self.assertEqual((bundles.discount_cents("plain"), bundles.bundle_price_cents("plain")), (0, 3750))

    def test_allocate(self) -> None:
        self.assertEqual(bundles.allocate(100, {"a": 1, "b": 1, "c": 1}), {"a": 34, "b": 33, "c": 33})
        self.assertEqual(bundles.allocate(101, {"a": 1, "b": 1, "c": 1}), {"a": 34, "b": 34, "c": 33})
        self.assertEqual(bundles.allocate(99, {"a": 1, "b": 1, "c": 1}), {"a": 33, "b": 33, "c": 33})
        self.assertEqual(bundles.allocate(10, {"a": 1, "b": 2, "c": 4}), {"a": 1, "b": 3, "c": 6})
        self.assertEqual(bundles.allocate(0, {"a": 3, "b": 4}), {"a": 0, "b": 0})
        self.assertEqual(bundles.allocate(10, {"x": 0, "y": 5}), {"x": 0, "y": 10})
        self.assertEqual(bundles.allocate(500, {"only": 7}), {"only": 500})

    def test_allocate_ties_go_to_the_lower_key(self) -> None:
        self.assertEqual(bundles.allocate(7, {"b": 1, "a": 1}), {"a": 4, "b": 3})
        self.assertEqual(bundles.allocate(1, {"z": 5, "m": 5, "k": 5}), {"z": 0, "m": 0, "k": 1})
        self.assertEqual(bundles.allocate(5, {"B": 1, "a": 1, "A": 1}), {"A": 2, "B": 2, "a": 1})
        weights = {"q": 2, "p": 2, "r": 3}
        result = bundles.allocate(100, weights)
        self.assertEqual(result, {"p": 29, "q": 28, "r": 43})
        self.assertEqual(weights, {"q": 2, "p": 2, "r": 3})

    def test_allocate_errors(self) -> None:
        with self.assertRaises(ValueError):
            bundles.allocate(-1, {"a": 1})
        with self.assertRaises(ValueError):
            bundles.allocate(100, {})
        with self.assertRaises(ValueError):
            bundles.allocate(100, {"a": 2, "b": -1})
        with self.assertRaises(ValueError):
            bundles.allocate(100, {"a": 0, "b": 0})

    def test_discount_breakdown_and_component_prices(self) -> None:
        self.assertEqual(bundles.discount_breakdown("trio"), {"MUG": 210, "LAMP": 490, "PEN": 88})
        self.assertEqual(bundles.component_prices("trio"), {"MUG": 2790, "LAMP": 6510, "PEN": 1162})
        self.assertEqual(bundles.discount_breakdown("desk"), {"LAMP": 700, "PEN": 250})
        self.assertEqual(bundles.component_prices("desk"), {"LAMP": 6300, "PEN": 2250})

    def test_breakdowns_always_add_up(self) -> None:
        for percent in range(0, 101):
            bundle_id = f"mix{percent}"
            bundles.define_bundle(bundle_id, {"MUG": 2, "LAMP": 1, "PEN": 3}, percent)
            shares = bundles.discount_breakdown(bundle_id)
            prices = bundles.component_prices(bundle_id)
            with self.subTest(percent=percent):
                self.assertEqual(set(shares), {"MUG", "LAMP", "PEN"})
                self.assertEqual(sum(shares.values()), bundles.discount_cents(bundle_id))
                self.assertEqual(sum(prices.values()), bundles.bundle_price_cents(bundle_id))
                self.assertEqual(prices["MUG"] + shares["MUG"], 6000)
        self.assertEqual(bundles.discount_breakdown("mix33"), {"MUG": 1980, "LAMP": 2310, "PEN": 1238})
        self.assertEqual(bundles.component_prices("mix33"), {"MUG": 4020, "LAMP": 4690, "PEN": 2512})

    def test_available(self) -> None:
        self.assertEqual(bundles.available("desk", {"LAMP": 3, "PEN": 7}), 3)
        self.assertEqual(bundles.available("desk", {"LAMP": 5, "PEN": 3}), 1)
        self.assertEqual(bundles.available("desk", stock={"LAMP": 5, "PEN": 1}), 0)
        self.assertEqual(bundles.available("desk", {"LAMP": 5}), 0)
        self.assertEqual(bundles.available("desk", {}), 0)
        self.assertEqual(bundles.available("desk"), 12)
        inventory.STOCK["PEN"] = 5
        self.assertEqual(bundles.available("desk"), 2)

    def test_missing_components(self) -> None:
        stock = {"LAMP": 1, "PEN": 4}
        self.assertEqual(bundles.missing_components("desk", 1, stock), {})
        self.assertEqual(bundles.missing_components("desk", 2, stock), {"LAMP": 1})
        self.assertEqual(bundles.missing_components("desk", 3, stock), {"LAMP": 2, "PEN": 2})
        self.assertEqual(bundles.missing_components("desk", stock={}), {"LAMP": 1, "PEN": 2})
        self.assertEqual(stock, {"LAMP": 1, "PEN": 4})
        self.assertEqual(bundles.missing_components("desk", 12), {})
        self.assertEqual(bundles.missing_components("desk", quantity=13), {"LAMP": 1})
        with self.assertRaises(ValueError):
            bundles.missing_components("desk", 0, stock)

    def test_reserve_bundle(self) -> None:
        self.assertIsNone(bundles.reserve_bundle("desk", 3))
        self.assertEqual(inventory.STOCK, {"MUG": 40, "LAMP": 9, "PEN": 94})
        bundles.reserve_bundle("trio")
        self.assertEqual(inventory.STOCK, {"MUG": 39, "LAMP": 8, "PEN": 93})
        bundles.reserve_bundle("desk", quantity=8)
        self.assertEqual(inventory.STOCK, {"MUG": 39, "LAMP": 0, "PEN": 77})

    def test_reserve_bundle_is_all_or_nothing(self) -> None:
        self.set_stock(MUG=40, LAMP=12, PEN=3)
        with self.assertRaises(inventory.OutOfStock):
            bundles.reserve_bundle("desk", 2)
        self.assertEqual(inventory.STOCK, {"MUG": 40, "LAMP": 12, "PEN": 3})
        self.set_stock(MUG=40, PEN=100)
        with self.assertRaises(inventory.OutOfStock):
            bundles.reserve_bundle("desk")
        self.assertEqual(inventory.STOCK, {"MUG": 40, "PEN": 100})
        with self.assertRaises(ValueError):
            bundles.reserve_bundle("desk", 0)
        self.assertEqual(inventory.STOCK, {"MUG": 40, "PEN": 100})


if __name__ == "__main__":
    unittest.main(verbosity=1)
