import unittest

from greet import farewell


class FarewellTest(unittest.TestCase):
    def test_farewell(self) -> None:
        self.assertEqual(farewell("Ada"), "Goodbye, Ada!")


if __name__ == "__main__":
    unittest.main()
