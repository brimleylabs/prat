import unittest

from tinyshop.cart import Cart


class CartTest(unittest.TestCase):
    def test_subtotal(self) -> None:
        cart = Cart()
        cart.add("MUG", 2)
        cart.add("PEN")
        self.assertEqual(cart.subtotal_cents(), 7250)

    def test_unknown_product(self) -> None:
        with self.assertRaises(KeyError):
            Cart().add("NOPE")
