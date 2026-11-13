"""The hidden check for this case. The agent never sees this file."""

import unittest

from numerals import to_roman


class HiddenRoman(unittest.TestCase):
    def test_numbers(self) -> None:
        expected = {
            1: "I", 4: "IV", 9: "IX", 14: "XIV", 40: "XL", 90: "XC",
            400: "CD", 900: "CM", 1994: "MCMXCIV", 2024: "MMXXIV", 3999: "MMMCMXCIX",
        }
        for n, numeral in expected.items():
            with self.subTest(n=n):
                self.assertEqual(to_roman(n), numeral)

    def test_out_of_range(self) -> None:
        for n in (0, -1, 4000):
            with self.subTest(n=n), self.assertRaises(ValueError):
                to_roman(n)


if __name__ == "__main__":
    unittest.main()
