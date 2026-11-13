"""The hidden check for this case. The agent never sees this file."""

import unittest

from greet import farewell, greet


class HiddenGreet(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_spaces_are_removed(self) -> None:
        self.assertEqual(greet("  Ada "), "Hello, Ada!")

    def test_no_name(self) -> None:
        self.assertEqual(greet(""), "Hello, world!")
        self.assertEqual(greet("   "), "Hello, world!")

    def test_farewell_unchanged(self) -> None:
        self.assertEqual(farewell("Ada"), "Goodbye, Ada!")


if __name__ == "__main__":
    unittest.main()
