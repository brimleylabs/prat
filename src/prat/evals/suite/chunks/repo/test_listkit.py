import unittest

from listkit import first


class FirstTest(unittest.TestCase):
    def test_first(self) -> None:
        self.assertEqual(first([3, 4]), 3)
        self.assertIsNone(first([]))


if __name__ == "__main__":
    unittest.main()
