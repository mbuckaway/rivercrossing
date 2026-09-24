# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the one card-code -> display-text mapping.

``ui/card_text.py`` is the single home for the mapping the console's
Cards column, ``results_win``'s "Best 5" cell and ``dialogs``'s
void-card confirm all render through -- it replaced two identical
private copies. It is a pure module with no ``wx`` import: R-71's
constraint, because ``ui/rider_columns.py`` imports it and the
presenters import that.

It also owns the one card -> red decision (:func:`card_markup`), as wx
markup: the console's card cells are text glyphs, and a cell holding
several cards needs each red card coloured on its own -- a
``wx.dataview.DataViewItemAttr`` carries one colour per *cell*, so a
mixed hand cannot use it. Only the red is explicit: ♠/♣ and the joker's
``★`` render as bare text and inherit the control's own adaptive
foreground, which is what kept them visible on a dark list before the
card-colour change and is restored here. The pure markup builder is the
test seam: nothing in a headless suite can observe painting.

The module also carries the WCAG 2.x contrast measurement the desktop
standard's §7 floor is checked with (:func:`contrast_ratio`).

Written tests-first: the module did not exist when this file landed.
"""

import html
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import card_text
from rivercrossing.ui.card_text import (
    DARK_RED,
    JOKER_CODE,
    JOKER_DISPLAY,
    SUIT_RED,
    card_markup,
    cards_markup,
    contrast_ratio,
    format_card,
    is_red_suit,
)

# ------------------------------------------------------- public surface


def test_card_text_given_the_shared_formatter_exports_its_public_names() -> None:
    """The code/display pair, the two reds, the mapper and its tests."""
    assert card_text.__all__ == [
        "DARK_RED",
        "JOKER_CODE",
        "JOKER_DISPLAY",
        "SUIT_RED",
        "card_markup",
        "cards_markup",
        "contrast_ratio",
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


# ------------------------------- the card-face reds and their markup
#
# The app's cards are text glyphs, so a red suit's colour is a *text*
# colour. A ``wx.dataview.DataViewItemAttr`` carries one colour per CELL
# (`GetAttrByRow` + `SetColour`), which cannot colour a cell that holds
# several cards of different suits, so every card cell renders wx markup
# instead: one `<span color="...">` per ♥/♦ card.
#
# Only the red is explicit. ♠/♣ and the joker's ★ come through bare and
# inherit the control's own foreground, so they follow the appearance --
# hardcoding their ink is exactly the defect that made them invisible on
# a dark list. Two reds exist because no single colour clears the 4.5:1
# floor on both backgrounds (the luminance windows are disjoint, so no
# hue could): the exports' own light red and an app-only dark red.

# The list backgrounds each red is measured against: a light list is
# white, a dark list is #1e1e1e, and #2c2c2e is the lighter dark surface
# (hover/alternating row) that is the dark red's worst case.
LIGHT_LIST_BG = "#ffffff"
DARK_LIST_BG = "#1e1e1e"
DARK_LIST_WORST_BG = "#2c2c2e"

# CODINGSTANDARDS-UX-DESKTOP.md §7: text at least 4.5:1.
CONTRAST_FLOOR = 4.5


def test_card_text_given_the_two_reds_is_the_light_export_red_and_the_app_dark_one() -> None:
    """#c0392b is the exports' red; #e57373 is the app's dark red."""
    assert (SUIT_RED, DARK_RED) == ("#c0392b", "#e57373")


CONTRAST_CASES = (
    (SUIT_RED, LIGHT_LIST_BG, 5.4384),
    (DARK_RED, DARK_LIST_BG, 5.5820),
    (DARK_RED, DARK_LIST_WORST_BG, 4.6664),
)


@pytest.mark.parametrize(
    ("red", "background", "expected"),
    CONTRAST_CASES,
    ids=["light_red_on_white", "dark_red_on_1e1e1e", "dark_red_on_2c2c2e"],
)
def test_contrast_ratio_given_each_red_on_its_list_background_clears_the_floor(
    red: str,
    background: str,
    expected: float,
) -> None:
    """§7: every custom-drawn colour is measured; each clears 4.5:1."""
    ratio = contrast_ratio(red, background)

    assert ratio == pytest.approx(expected, abs=0.001)
    assert ratio >= CONTRAST_FLOOR


def test_contrast_ratio_given_one_colour_against_itself_is_one_to_one() -> None:
    """T-4 boundary: no colour contrasts with itself."""
    assert contrast_ratio(LIGHT_LIST_BG, LIGHT_LIST_BG) == 1.0


def test_contrast_ratio_given_a_colour_that_is_not_hex_raises_value_error() -> None:
    """T-5/T-12: a malformed colour raises ValueError, not a number."""
    with pytest.raises(ValueError, match=re.escape("non-hexadecimal number found")):
        contrast_ratio("#zzzzzz", LIGHT_LIST_BG)


_hex_colour = st.from_regex(r"#[0-9a-f]{6}", fullmatch=True)


@given(_hex_colour, _hex_colour)
def test_contrast_ratio_given_any_two_colours_is_symmetric_and_at_least_one(
    first: str,
    second: str,
) -> None:
    """Property (T-7): the ratio is order-independent and >= 1.0."""
    assert contrast_ratio(first, second) == contrast_ratio(second, first)
    assert contrast_ratio(first, second) >= 1.0


# ♥/♦ red, ♠/♣ and the joker's star bare (no suit to colour at all), and
# anything malformed bare rather than crash the cell.
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

# Every arm the one builder has: both red suits spanned in the caller's
# red, black suits and the joker bare, the ten's two-character rank,
# and the edges T-4 asks for.
CARD_MARKUP_CASES = (
    ("9H", f'<span color="{SUIT_RED}">9♥</span>'),
    ("10D", f'<span color="{SUIT_RED}">10♦</span>'),
    ("AH", f'<span color="{SUIT_RED}">A♥</span>'),
    ("AS", "A♠"),
    ("2C", "2♣"),
    ("10S", "10♠"),
    (JOKER_CODE, "JK★"),
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
def test_card_markup_given_a_card_code_spans_its_glyph_only_when_the_suit_is_red(
    code: str, expected: str
) -> None:
    """T-3: ♥/♦ take the red span; ♠/♣ and the joker stay bare text."""
    assert card_markup(code) == expected


def test_card_markup_given_a_dark_red_uses_it_for_a_red_suit() -> None:
    """The caller's red is what a red suit's own span carries."""
    assert card_markup("AH", DARK_RED) == f'<span color="{DARK_RED}">A♥</span>'


def test_card_markup_given_a_dark_red_leaves_a_black_suit_bare() -> None:
    """T-3 negative: a ♠/♣ cell never carries the dark red either."""
    assert card_markup("KS", DARK_RED) == "K♠"


def test_card_markup_given_an_unmappable_suit_raises_key_error() -> None:
    """T-5/T-12: the glyph decision is not duplicated; it raises."""
    with pytest.raises(KeyError, match=re.escape("'X'")):
        card_markup("9X")


def test_card_markup_given_an_empty_code_raises_index_error() -> None:
    """T-5/T-12: an empty code has no suit to colour."""
    with pytest.raises(IndexError, match=re.escape("string index out of range")):
        card_markup("")


@given(_valid_card_codes)
def test_card_markup_given_any_natural_code_spans_it_exactly_when_its_suit_is_red(
    code: str,
) -> None:
    """Invariant (T-7): one span for a red suit, none for the rest."""
    markup = card_markup(code)

    assert markup.count("<span ") == int(is_red_suit(code))
    assert format_card(code) in markup


@given(_valid_card_codes)
def test_card_markup_given_any_natural_code_carries_the_suit_red_only_where_it_draws_it(
    code: str,
) -> None:
    """Invariant (T-7): no cell is coloured unless its glyph asks."""
    assert (f'color="{SUIT_RED}"' in card_markup(code)) is is_red_suit(code)


@given(st.sampled_from(_VALID_RANKS), st.sampled_from(("S", "C")))
def test_card_markup_given_any_black_suit_card_returns_its_bare_glyph_text(
    rank: str,
    suit: str,
) -> None:
    """Invariant (T-7): a ♠/♣ cell is bare, so the list paints it."""
    assert card_markup(f"{rank}{suit}") == format_card(f"{rank}{suit}")


def test_card_markup_given_a_rank_carrying_markup_characters_escapes_them() -> None:
    """A corrupt rank prefix is echoed by ``format_card``, so escape it.

    The stored code's suit letter must be real for a glyph to exist at
    all, but the rank prefix is passed through verbatim, so a corrupt
    code *can* carry markup syntax. A bare cell still passes through the
    markup renderer, so it must never lose text (or paint) to it.
    """
    assert card_markup("<&S") == "&lt;&amp;♠"


def test_card_markup_given_a_spanned_red_card_escapes_its_rank_too() -> None:
    """The same escaping holds inside the red span."""
    assert card_markup("<&H") == f'<span color="{SUIT_RED}">&lt;&amp;♥</span>'


@given(st.text(max_size=8))
def test_card_markup_given_any_rank_prefix_never_lets_it_break_the_cell(rank: str) -> None:
    """Property (T-7): the body is the escaped rank + its glyph."""
    markup = card_markup(f"{rank}S")

    assert markup == f"{html.escape(rank, quote=False)}♠"


@given(st.text(max_size=8))
def test_card_markup_given_any_rank_prefix_never_escapes_a_red_suits_span(rank: str) -> None:
    """Property (T-7): the span leaves the escaped body untouched."""
    markup = card_markup(f"{rank}H")

    assert markup == f'<span color="{SUIT_RED}">{html.escape(rank, quote=False)}♥</span>'


# ------------------------------------------------------- cards_markup


def _plain_text(cell: str) -> str:
    """Return *cell*'s visible text: the span tags removed."""
    return re.sub(r"</?span[^>]*>", "", cell)


def test_cards_markup_given_no_cards_returns_the_empty_cell() -> None:
    """T-4 collection boundary: an empty hand draws nothing at all."""
    assert cards_markup(()) == ""


def test_cards_markup_given_one_card_returns_its_cell_alone() -> None:
    """T-4 collection boundary: one card, one cell, no separator."""
    assert cards_markup(("AS",)) == "A♠"


def test_cards_markup_given_one_red_card_returns_its_span_alone() -> None:
    """T-4 collection boundary: one red card, one span, no separator."""
    assert cards_markup(("9H",)) == f'<span color="{SUIT_RED}">9♥</span>'


def test_cards_markup_given_several_cards_spans_only_the_red_ones() -> None:
    """The five-card mixed hand a single cell colour could not draw."""
    assert cards_markup(("KS", "KC", "KD", "JK", "9H")) == (
        f'K♠ K♣ <span color="{SUIT_RED}">K♦</span> JK★ <span color="{SUIT_RED}">9♥</span>'
    )


def test_cards_markup_given_a_repeated_suit_colours_every_red_card_its_own() -> None:
    """The console's Cards cell: A♠ bare, K♥ red, 10♦ red, one cell."""
    assert cards_markup(("AS", "KH", "10D")) == (
        f'A♠ <span color="{SUIT_RED}">K♥</span> <span color="{SUIT_RED}">10♦</span>'
    )


def test_cards_markup_given_a_dark_red_uses_it_for_every_red_card() -> None:
    """The caller's own red reaches every red card in the hand."""
    assert cards_markup(("2C", "KH"), DARK_RED) == (f'2♣ <span color="{DARK_RED}">K♥</span>')


@given(st.lists(_valid_card_codes, min_size=1, max_size=8))
def test_cards_markup_given_any_hand_keeps_every_cards_glyph_text(cards: list[str]) -> None:
    """Property (T-7): the cell reads as the hand's own plain glyphs."""
    markup = cards_markup(tuple(cards))

    assert _plain_text(markup) == " ".join(format_card(card) for card in cards)


@given(st.lists(_valid_card_codes, min_size=1, max_size=8))
def test_cards_markup_given_any_hand_spans_exactly_its_red_cards(cards: list[str]) -> None:
    """Property (T-7): a span per red card, and for no other card."""
    markup = cards_markup(tuple(cards))

    assert markup.count("<span ") == sum(is_red_suit(card) for card in cards)


@given(st.lists(st.sampled_from(_VALID_SUITS), min_size=1, max_size=8))
def test_cards_markup_given_any_hand_colours_every_red_suit_and_no_other(
    suits: list[str],
) -> None:
    """Property (T-7): the spans are the hand's own red sequence."""
    codes = [f"9{suit}" for suit in suits]

    markup = cards_markup(tuple(codes))

    assert markup.count(f'color="{SUIT_RED}"') == sum(1 for suit in suits if suit in "HD")
