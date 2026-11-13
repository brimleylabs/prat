import unittest

from mathkit import mean


class MeanTest(unittest.TestCase):
    def test_mean(self) -> None:
        self.assertEqual(mean([1, 2, 3]), 2)


if __name__ == "__main__":
    unittest.main()
