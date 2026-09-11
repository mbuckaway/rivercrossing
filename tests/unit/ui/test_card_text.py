# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the one card-code -> display-text mapping.

``ui/card_text.py`` is the single home for the mapping the console's
Cards column, ``results_win``'s "Best 5" cell and ``dialogs``'s
void-card confirm all render through -- it replaced two identical
private copies. It is a pure module with no ``wx`` import: R-71's
constraint, because ``ui/rider_columns.py`` imports it and the
presenters import that.

Written tests-first: the module did not exist when this file landed.
"""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import card_text
from rivercrossing.ui.card_text import JOKER_CODE, JOKER_DISPLAY, format_card

# ------------------------------------------------------- public surface


def test_card_text_given_the_shared_formatter_exports_the_three_names() -> None:
    """One code/display pair plus the mapper; suit map private."""
    assert card_text.__all__ == ["JOKER_CODE", "JOKER_DISPLAY", "format_card"]


def test_card_text_given_the_joker_constants_are_the_canvas_spellings() -> None:
    """The stored joker code and its starred display glyph, exactly."""
    assert (JOKER_CODE, JOKER_DISPLAY) == ("JK", "JK★")


# ---------------------------------------------------------- format_card

DISPLAY_CASES = (
    ("9H", "9♥"),  # hearts
    ("TS", "T♠"),  # spades; "T" is the stored ten (Card.code())
    ("KD", "K♦"),  # diamonds
    ("2C", "2♣"),  # clubs
    ("JK", JOKER_DISPLAY),  # the joker, which carries no suit letter
)


@pytest.mark.parametrize(("code", "expected"), DISPLAY_CASES)
def test_format_card_given_a_stored_code_returns_its_canvas_glyph_text(
    code: str, expected: str
) -> None:
    """Every suit glyph, the "T" ten boundary, and the joker marker."""
    assert format_card(code) == expected


def test_format_card_given_an_empty_code_raises_index_error() -> None:
    """T-5/T-12: an empty code has no suit to read -- fail loud."""
    with pytest.raises(IndexError, match=re.escape("string index out of range")):
        format_card("")


def test_format_card_given_an_unknown_suit_letter_raises_key_error() -> None:
    """T-5/T-12: a corrupt suit letter is a KeyError, never a glyph."""
    with pytest.raises(KeyError, match=re.escape("'X'")):
        format_card("9X")


_VALID_RANKS = tuple("23456789TJQKA")
_VALID_SUITS = tuple("SHDC")
_SUIT_GLYPHS = frozenset({"♠", "♥", "♦", "♣"})


def _join_rank_suit(rank: str, suit: str) -> str:
    """Build one stored card code from a rank and a suit letter."""
    return f"{rank}{suit}"


_valid_card_codes = st.builds(
    _join_rank_suit, st.sampled_from(_VALID_RANKS), st.sampled_from(_VALID_SUITS)
)


@given(_valid_card_codes)
def test_format_card_given_any_natural_code_converts_only_the_suit(code: str) -> None:
    """Property (T-7): only the suit changes; rank/size stay."""
    text = format_card(code)

    assert text[:-1] == code[:-1]
    assert len(text) == len(code)
    assert text[-1] in _SUIT_GLYPHS


@given(_valid_card_codes)
def test_format_card_given_any_natural_code_is_never_the_joker_display(code: str) -> None:
    """Property (T-7): only ``"JK"`` renders as the joker marker."""
    assert format_card(code) != JOKER_DISPLAY
