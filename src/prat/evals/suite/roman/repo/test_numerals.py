import unittest

from numerals import ordinal


class OrdinalTest(unittest.TestCase):
    def test_ordinals(self) -> None:
        self.assertEqual([ordinal(n) for n in (1, 2, 3, 4, 11)], ["1st", "2nd", "3rd", "4th", "11th"])


if __name__ == "__main__":
    unittest.main()
