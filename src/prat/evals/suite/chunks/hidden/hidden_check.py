"""The hidden check for this case. The agent never sees this file."""

import unittest

from listkit import chunked


class HiddenChunked(unittest.TestCase):
    def test_chunks(self) -> None:
        self.assertEqual(chunked([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        self.assertEqual(chunked([1, 2, 3], 3), [[1, 2, 3]])
        self.assertEqual(chunked([1, 2], 5), [[1, 2]])

    def test_empty(self) -> None:
        self.assertEqual(chunked([], 3), [])

    def test_size_below_one(self) -> None:
        for size in (0, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                chunked([1], size)


if __name__ == "__main__":
    unittest.main()
