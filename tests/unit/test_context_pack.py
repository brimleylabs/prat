"""The files a card names, read for its builder (prat.engine.context_pack)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from prat.engine.context_pack import card_names, pack


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_names_are_code_like_words() -> None:
    names = card_names("Make place_order take a gift_card and update tinyshop/api.py and Order")
    assert {"place_order", "gift_card", "tinyshop/api.py"} <= set(names)
    assert "Make" not in names and "take" not in names


def test_the_files_naming_the_cards_words_are_packed(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "shop/checkout.py": "def place_order(cart):\n    return cart\n",
            "shop/other.py": "def unrelated():\n    pass\n",
        },
    )
    text = pack(root, "place_order takes a gift_card", 10_000)
    assert '<file path="shop/checkout.py">' in text
    assert "def place_order" in text
    assert "shop/other.py" not in text


def test_nothing_is_packed_without_a_budget_or_a_match(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    assert pack(root, "place_order", 0) == ""
    assert pack(root, "place_order", 10_000) == ""
