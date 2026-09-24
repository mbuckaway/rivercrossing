# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the one card-code -> display-text mapping.

``ui/card_text.py`` is the single home for the mapping the console's
Cards column, ``results_win``'s "Best 5" cell and ``dialogs``'s
void-card confirm all render through -- it replaced two identical
private copies. It is a pure module with no ``wx`` import: R-71's
constraint, because ``ui/rider_columns.py`` imports it and the
presenters import that.

It also owns the one card -> colour decision (:func:`card_markup`), as
wx markup: the console's card cells are text glyphs, and a cell holding
several cards needs each of them coloured on its own -- a
``wx.dataview.DataViewItemAttr`` carries one colour per *cell*, so a
mixed hand cannot use it. The pure markup builder is the test seam:
nothing in a headless suite can observe painting.

Written tests-first: the module did not exist when this file landed.
"""

import html
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import card_text
from rivercrossing.ui.card_text import (
    JOKER_CODE,
    JOKER_DISPLAY,
    JOKER_STEEL,
    SUIT_INK,
    SUIT_RED,
    card_markup,
    cards_markup,
    format_card,
    is_red_suit,
)

# ------------------------------------------------------- public surface


def test_card_text_given_the_shared_formatter_exports_its_public_names() -> None:
    """The code/display pair, the mapper, the palette and its tests."""
    assert card_text.__all__ == [
        "JOKER_CODE",
        "JOKER_DISPLAY",
        "JOKER_STEEL",
        "SUIT_INK",
        "SUIT_RED",
        "card_markup",
        "cards_markup",
        "format_card",
        "is_red_suit",
    ]


def test_card_text_given_the_joker_constants_are_the_canvas_spellings() -> None:
    """The stored joker code and its starred display glyph, exactly."""
    assert (JOKER_CODE, JOKER_DISPLAY) == ("JK", "JK★")


# ---------------------------------------------------------- format_card

DISPLAY_CASES = (
    ("9H", "9♥"),  # hearts
    ("10S", "10♠"),  # spades; the stored ten is "10" (Card.code())
    ("KD", "K♦"),  # diamonds
    ("2C", "2♣"),  # clubs
    ("JK", JOKER_DISPLAY),  # the joker, which carries no suit letter
)


@pytest.mark.parametrize(("code", "expected"), DISPLAY_CASES)
def test_format_card_given_a_stored_code_returns_its_canvas_glyph_text(
    code: str, expected: str
) -> None:
    """Every suit glyph, the ten's "10" rank, the joker marker."""
    assert format_card(code) == expected


def test_format_card_given_the_ten_code_returns_the_10_rank_glyph() -> None:
    """The text "10" is exactly the bitmap's own "10" asset rank."""
    assert format_card("10D") == "10♦"


def test_format_card_given_the_joker_code_returns_the_starred_display() -> None:
    """The joker is untouched by the ten's spelling: still "JK★"."""
    assert format_card(JOKER_CODE) == "JK★"


def test_format_card_given_an_empty_code_raises_index_error() -> None:
    """T-5/T-12: an empty code has no suit to read -- fail loud."""
    with pytest.raises(IndexError, match=re.escape("string index out of range")):
        format_card("")


def test_format_card_given_an_unknown_suit_letter_raises_key_error() -> None:
    """T-5/T-12: a corrupt suit letter is a KeyError, never a glyph."""
    with pytest.raises(KeyError, match=re.escape("'X'")):
        format_card("9X")


_VALID_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
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


# --------------------------------- the card-face palette and its markup
#
# The app's cards are text glyphs, so their suit colour is a *text*
# colour. A ``wx.dataview.DataViewItemAttr`` carries one colour per
# CELL (`GetAttrByRow` + `SetColour`), which cannot colour a cell that
# holds several cards of different suits, so every card cell renders
# wx markup instead: one `<span color="...">` per card. This module owns
# the one code -> colour decision, beside the code -> glyph decision it
# must agree with, and the markup builder the DataView models render
# through.

# The industry tokens the HTML chip (``.chip``/``.chip.r``/``.chip.j``)
# and the PDF (``_INK``/``_SUIT_RED``/``_STEEL``) already use -- one
# value per surface, in the ``#rrggbb`` spelling wx markup needs.
PALETTE_CASES = (
    (SUIT_RED, "#c0392b", (192, 57, 43)),
    (SUIT_INK, "#1d1f20", (29, 31, 32)),
    (JOKER_STEEL, "#416180", (65, 97, 128)),
)


@pytest.mark.parametrize(
    ("markup_colour", "html_colour", "rgb"),
    PALETTE_CASES,
    ids=["suit_red", "ink", "joker_steel"],
)
def test_card_colour_given_the_shared_palette_matches_the_html_and_pdf_values(
    markup_colour: str, html_colour: str, rgb: tuple[int, int, int]
) -> None:
    """One palette: #c0392b / #1d1f20 / #416180, byte-identical."""
    assert markup_colour == html_colour
    assert markup_colour == "#{:02x}{:02x}{:02x}".format(*rgb)


# ♥/♦ red, ♠/♣ ink (never red), the joker's star steel (no suit at
# all), and anything malformed ink rather than crash the cell.
RED_SUIT_CASES = (
    ("9H", True),
    ("10D", True),
    ("AH", True),
    ("2D", True),
    ("AS", False),
    ("KC", False),
    ("2C", False),
    (JOKER_CODE, False),
    ("", False),
    # A rank-less code still draws a heart glyph (``format_card`` reads
    # only the suit letter), so it takes the suit red like any other ♥.
    ("H", True),
    ("ZZ", False),
)


@pytest.mark.parametrize(
    ("code", "expected"),
    RED_SUIT_CASES,
    ids=[
        "nine_hearts",
        "ten_diamonds",
        "ace_hearts",
        "two_diamonds",
        "ace_spades",
        "king_clubs",
        "two_clubs",
        "joker",
        "empty",
        "bare_hearts_suit",
        "corrupt",
    ],
)
def test_is_red_suit_given_a_card_code_returns_whether_its_suit_draws_red(
    code: str,
    expected: bool,  # noqa: FBT001 -- a parametrize row's value
) -> None:
    """T-4: both red suits, both ink suits, the joker, and the edges."""
    assert is_red_suit(code) is expected


@given(_valid_card_codes)
def test_is_red_suit_given_any_natural_code_agrees_with_the_glyph_it_draws(code: str) -> None:
    """Property (T-7): the colour matches the glyph the cell draws."""
    assert is_red_suit(code) is (format_card(code)[-1] in "♥♦")


# ---------------------------------------------------------- card_markup

# Every colour arm the one builder has: both red suits, both ink suits,
# the joker's steel, the ten's two-character rank, and the edges T-4
# asks for.
CARD_MARKUP_CASES = (
    ("9H", f'<span color="{SUIT_RED}">9♥</span>'),
    ("10D", f'<span color="{SUIT_RED}">10♦</span>'),
    ("AH", f'<span color="{SUIT_RED}">A♥</span>'),
    ("AS", f'<span color="{SUIT_INK}">A♠</span>'),
    ("2C", f'<span color="{SUIT_INK}">2♣</span>'),
    ("10S", f'<span color="{SUIT_INK}">10♠</span>'),
    (JOKER_CODE, f'<span color="{JOKER_STEEL}">JK★</span>'),
)


@pytest.mark.parametrize(
    ("code", "expected"),
    CARD_MARKUP_CASES,
    ids=[
        "nine_hearts",
        "ten_diamonds",
        "ace_hearts",
        "ace_spades",
        "two_clubs",
        "ten_spades",
        "joker",
    ],
)
def test_card_markup_given_a_card_code_wraps_its_glyph_in_its_own_colour(
    code: str, expected: str
) -> None:
    """T-3: every arm -- ♥/♦ red, ♠/♣ ink, the joker steel."""
    assert card_markup(code) == expected


def test_card_markup_given_an_unmappable_suit_raises_key_error() -> None:
    """T-5/T-12: the glyph decision is not duplicated; it raises."""
    with pytest.raises(KeyError, match=re.escape("'X'")):
        card_markup("9X")


def test_card_markup_given_an_empty_code_raises_index_error() -> None:
    """T-5/T-12: an empty code has no suit to colour."""
    with pytest.raises(IndexError, match=re.escape("string index out of range")):
        card_markup("")


@given(_valid_card_codes)
def test_card_markup_given_any_natural_code_renders_exactly_its_own_glyph_text(code: str) -> None:
    """Invariant (T-7): the span holds the plain glyph, nothing else."""
    markup = card_markup(code)

    assert markup.endswith(f">{format_card(code)}</span>")
    assert markup.startswith('<span color="')


@given(_valid_card_codes)
def test_card_markup_given_any_natural_code_colours_the_span_its_suit_needs(code: str) -> None:
    """Invariant (T-7): the colour is what the glyph's suit asks."""
    expected = SUIT_RED if is_red_suit(code) else SUIT_INK

    assert f'color="{expected}"' in card_markup(code)


def test_card_markup_given_a_rank_carrying_markup_characters_escapes_them() -> None:
    """A corrupt rank prefix is echoed by ``format_card``, so escape it.

    The stored code's suit letter must be real for a glyph to exist at
    all, but the rank prefix is passed through verbatim, so a corrupt
    code *can* carry markup syntax. The cell must never lose text (or
    paint) to it.
    """
    assert card_markup("<&S") == f'<span color="{SUIT_INK}">&lt;&amp;♠</span>'


@given(st.text(max_size=8))
def test_card_markup_given_any_rank_prefix_never_lets_it_break_the_span(rank: str) -> None:
    """Property (T-7): the body is the escaped rank + its glyph."""
    markup = card_markup(f"{rank}S")

    assert markup == f'<span color="{SUIT_INK}">{html.escape(rank, quote=False)}♠</span>'


# ------------------------------------------------------- cards_markup


def test_cards_markup_given_no_cards_returns_the_empty_cell() -> None:
    """T-4 collection boundary: an empty hand draws nothing at all."""
    assert cards_markup(()) == ""


def test_cards_markup_given_one_card_returns_its_span_alone() -> None:
    """T-4 collection boundary: one card, one span, no separator."""
    assert cards_markup(("AS",)) == f'<span color="{SUIT_INK}">A♠</span>'


def test_cards_markup_given_several_cards_gives_each_its_own_span() -> None:
    """The five-card mixed hand a single cell colour could not draw."""
    assert cards_markup(("KS", "KC", "KD", "JK", "9H")) == (
        f'<span color="{SUIT_INK}">K♠</span> '
        f'<span color="{SUIT_INK}">K♣</span> '
        f'<span color="{SUIT_RED}">K♦</span> '
        f'<span color="{JOKER_STEEL}">JK★</span> '
        f'<span color="{SUIT_RED}">9♥</span>'
    )


def test_cards_markup_given_a_repeated_suit_colours_every_card_its_own() -> None:
    """The console's Cards cell: A♠ ink, K♥ red, 10♦ red, one cell."""
    assert cards_markup(("AS", "KH", "10D")) == (
        f'<span color="{SUIT_INK}">A♠</span> '
        f'<span color="{SUIT_RED}">K♥</span> '
        f'<span color="{SUIT_RED}">10♦</span>'
    )


@given(st.lists(_valid_card_codes, min_size=1, max_size=8))
def test_cards_markup_given_any_hand_preserves_its_card_count(cards: list[str]) -> None:
    """Property (T-7): length preservation -- one span per card."""
    markup = cards_markup(tuple(cards))

    assert markup.count("<span ") == len(cards)


@given(st.lists(st.sampled_from(_VALID_SUITS), min_size=1, max_size=8))
def test_cards_markup_given_any_hand_colours_every_card_by_its_suit(suits: list[str]) -> None:
    """Property (T-7): the spans are the hand's own colour sequence."""
    codes = [f"9{suit}" for suit in suits]

    markup = cards_markup(tuple(codes))

    assert markup.count(f'color="{SUIT_RED}"') == sum(1 for suit in suits if suit in "HD")
    assert markup.count(f'color="{SUIT_INK}"') == sum(1 for suit in suits if suit in "SC")
