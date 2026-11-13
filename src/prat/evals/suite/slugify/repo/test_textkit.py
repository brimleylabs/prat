import unittest

from textkit import word_count


class WordCountTest(unittest.TestCase):
    def test_words(self) -> None:
        self.assertEqual(word_count("one two  three"), 3)


if __name__ == "__main__":
    unittest.main()
