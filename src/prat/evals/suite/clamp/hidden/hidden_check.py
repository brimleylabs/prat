"""The hidden check for this case. The agent never sees this file."""

import unittest

from mathkit import clamp, mean


class HiddenClamp(unittest.TestCase):
    def test_values(self) -> None:
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-1, 0, 10), 0)
        self.assertEqual(clamp(11, 0, 10), 10)
        self.assertEqual(clamp(2.5, 1.5, 2.0), 2.0)

    def test_reversed_range(self) -> None:
        with self.assertRaises(ValueError):
            clamp(1, 10, 0)

    def test_mean_unchanged(self) -> None:
        self.assertEqual(mean([2, 4]), 3)


if __name__ == "__main__":
    unittest.main()
