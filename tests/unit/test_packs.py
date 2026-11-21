"""Fact packs (prat.packs): what is kept of a pack when the card or the code has moved on."""

from __future__ import annotations

from pathlib import Path

from prat import packs

PACK = """\
## Data model
- `app/models.py:10-40` holds `Order`, with `account_id` as its key to `Account`.
- Orders join to customers through `account_id`, not `customer_id` (`app/models.py:22`).

## Signatures the change calls
`app/services/orders.py`, class `OrderService`:
- `total(self, order) -> Decimal` (`app/services/orders.py:55`).
- `refund(self, order, amount)`: validates first.
  - It raises on a negative amount (`:71`).
  - It writes an audit row (`app/audit.py:12`).

## Traps
- Tests need the `db` fixture (`tests/conftest.py:8`).
- Money is stored in cents everywhere.
"""


def a_pack(**values: object) -> packs.Pack:
    base: dict[str, object] = dict(card_id=1, card_hash="h", commit="c1", text=PACK)
    return packs.Pack(**{**base, **values})  # type: ignore[arg-type]


def test_a_card_is_the_same_until_its_words_change() -> None:
    one = packs.card_hash("Refunds", "Add refunds.", "- works")
    assert one == packs.card_hash(" Refunds ", "Add refunds.\n", "- works")
    assert one != packs.card_hash("Refunds", "Add partial refunds.", "- works")


def test_the_files_a_pack_cites() -> None:
    assert packs.cited_files(PACK) == {
        "app/models.py",
        "app/services/orders.py",
        "app/audit.py",
        "tests/conftest.py",
    }
    assert packs.cited_files("at 12:30 see http://x.io/a.py:8 and `src/a.tsx:3-9`") == {"src/a.tsx"}


def test_a_verifiers_answer_is_the_pack_and_its_notes() -> None:
    text, notes = packs.split_notes("## Traps\n- a\n\n## Verification notes\n- Corrected: a\n")
    assert text == "## Traps\n- a" and notes == "- Corrected: a"
    assert packs.split_notes("## Traps\n- a\n") == ("## Traps\n- a", "")


def test_a_pack_holds_while_nothing_it_cites_changed() -> None:
    checked = packs.preflight(a_pack(), current_hash="h", changed=["README.md", "app/other.py"])
    assert checked.text == PACK and checked.dropped == 0 and checked.kept == 6


def test_statements_about_a_changed_file_are_left_out() -> None:
    checked = packs.preflight(a_pack(), current_hash="h", changed=["app/services/orders.py"])
    assert checked.text is not None and (checked.dropped, checked.kept) == (2, 4)
    # Both statements about the file go, with what was written under them (a bare line number
    # stands for the file named before it); the rest stays, headings included.
    assert "total(self" not in checked.text and "negative amount" not in checked.text
    assert "audit row" not in checked.text
    assert "`app/models.py:22`" in checked.text and "## Signatures the change calls" in checked.text
    assert "Money is stored in cents" in checked.text
    assert checked.text.endswith("changed after the research: 2 statements.")


def test_a_pack_about_code_that_mostly_changed_is_not_used() -> None:
    checked = packs.preflight(
        a_pack(), current_hash="h", changed=["app/models.py", "app/services/orders.py"]
    )
    assert checked.text is None and checked.reason == "too much of the code changed"


def test_a_pack_is_not_used_when_the_card_changed_or_it_was_never_checked() -> None:
    assert packs.preflight(a_pack(), current_hash="other", changed=[]).text is None
    assert packs.preflight(a_pack(status="unverified"), current_hash="h", changed=[]).text is None
    assert packs.preflight(a_pack(), current_hash="h", changed=None).text is None
    assert packs.preflight(None, current_hash="h", changed=[]).reason == "no pack"


def test_a_pack_is_kept_on_disk_per_card(tmp_path: Path) -> None:
    assert packs.load(tmp_path, 4) is None
    packs.save(tmp_path, a_pack(card_id=4, corrections=3))
    again = packs.load(tmp_path, 4)
    assert again is not None and again.corrections == 3 and again.usable
    assert packs.render(again.text).startswith(packs.HEADING)
