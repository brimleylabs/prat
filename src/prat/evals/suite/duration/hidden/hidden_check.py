"""The hidden check for this case. The agent never sees this file."""

import unittest

from timeparse import parse_clock, parse_duration


class HiddenDuration(unittest.TestCase):
    def test_values(self) -> None:
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("45s"), 45)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1h2m3s"), 3723)

    def test_errors(self) -> None:
        for text in ("", "5x", "30m1h", "h", "1h 30m"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_duration(text)

    def test_clock_unchanged(self) -> None:
        self.assertEqual(parse_clock("09:30"), 570)


if __name__ == "__main__":
    unittest.main()
