import unittest

from timeparse import parse_clock


class ClockTest(unittest.TestCase):
    def test_clock(self) -> None:
        self.assertEqual(parse_clock("09:30"), 570)


if __name__ == "__main__":
    unittest.main()
