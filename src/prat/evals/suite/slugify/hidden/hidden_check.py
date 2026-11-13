"""The hidden check for this case. The agent never sees this file."""

import unittest

from textkit import slugify, word_count


class HiddenSlugify(unittest.TestCase):
    def test_title(self) -> None:
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_no_hyphens_at_the_ends(self) -> None:
        self.assertEqual(slugify("  --Already--Slugged--  "), "already-slugged")

    def test_cut_never_ends_with_a_hyphen(self) -> None:
        self.assertEqual(slugify("x" * 39 + " y"), "x" * 39)
        self.assertLessEqual(len(slugify("word " * 30)), 40)
        self.assertFalse(slugify("word " * 30).endswith("-"))

    def test_word_count_unchanged(self) -> None:
        self.assertEqual(word_count("a b"), 2)


if __name__ == "__main__":
    unittest.main()
