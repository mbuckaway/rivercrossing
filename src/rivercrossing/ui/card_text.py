# SPDX-License-Identifier: GPL-3.0-only
"""The one card-code -> canvas display-text mapping.

A stored card code (``Card.code()``: ``"KS"``, ``"10D"``, ``"JK"``)
renders on the canvas as the rank text plus a suit glyph --
``"K♠"``, ``"10♦"``, and ``"JK★"`` for the joker. Three call sites
draw that text: ``ui/rider_columns.py``'s Cards cell, ``results_win``'s
"Best 5" column and ``dialogs``'s void-card confirm. This module is
the one implementation they share; it replaced two identical private
copies (``results_win``'s own ``_SUIT_SYMBOLS``/``format_card`` pair
and ``dialogs``'s own private copy).

It also owns the *colour* of that text (:func:`card_markup`): the
console's cards are glyphs, so a face's "suit colour" is a text colour,
and the code -> colour decision belongs beside the code -> glyph
decision it must agree with. The colour is always redundant with the
glyph the cell draws -- the suit is spelled, not implied -- so no
surface ever depends on it alone (CODINGSTANDARDS-UX-DESKTOP.md §7).

The colour travels as **wx markup**, one ``<span color="...">`` per
card, because a ``wx.dataview.DataViewItemAttr`` carries ONE text
colour per CELL: fine for a cell holding a single card, wrong for one
holding several (the standings' five-card "Best 5" hand, the console
Riders sidebar's Cards cell), and unable to express the joker's steel
at all. The markup's *text* is escaped, so a corrupt stored rank can
never inject a tag into the renderer.

No ``wx`` import may ever land here: ``ui/rider_columns.py`` imports
this module and the presenters import that (R-71), so it must stay
importable headless.
"""

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
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

_SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

# The two suits a card face draws red; ♠/♣ stay ink and the joker's star
# steel (it is not a suit at all).
_RED_SUITS = frozenset({"H", "D"})

JOKER_CODE = "JK"
JOKER_DISPLAY = "JK★"

# The industry tokens as the ``#rrggbb`` spelling wx markup needs --
# the same values the HTML chip (``.chip``/``.chip.r``/``.chip.j``) and
# the PDF (``_INK``/``_SUIT_RED``/``_STEEL``) already draw: ink
# ``#1d1f20`` for spades/clubs and body text, the suit red ``#c0392b``
# for hearts/diamonds card faces, steel ``#416180`` for the joker.
SUIT_RED = "#c0392b"
SUIT_INK = "#1d1f20"
JOKER_STEEL = "#416180"


def is_red_suit(code: str) -> bool:
    """Return whether *code*'s card face draws in the suit red.

    ``"9H"``/``"10D"`` -> ``True``; ♠/♣ and the joker (``"JK"``,
    whose ``★`` is no suit) -> ``False``. A code that names no suit
    at all (empty, corrupt) is ink rather than an error: this marks
    a glyph, so a code drawing no red-suit glyph draws no red.
    """
    return code[-1:] in _RED_SUITS


def format_card(code: str) -> str:
    """Return one stored card code's canvas display text.

    ``"KS"`` -> ``"K♠"``; the joker -> ``"JK★"``. The rank text is
    already in its display form (``Card.code()``'s stored form spells
    the ten "10", module-skeletons.md S4), so only the suit letter
    needs converting to a glyph.

    Raises:
        KeyError: If the last character is not a known suit letter
            (``S``/``H``/``D``/``C``).
        IndexError: If *code* is empty.
    """
    if code == JOKER_CODE:
        return JOKER_DISPLAY
    rank, suit = code[:-1], code[-1]
    return f"{rank}{_SUIT_SYMBOLS[suit]}"


def _card_colour(code: str) -> str:
    """Return *code*'s markup colour: red, ink, or the joker steel."""
    if code == JOKER_CODE:
        return JOKER_STEEL
    return SUIT_RED if is_red_suit(code) else SUIT_INK


def card_markup(code: str) -> str:
    """Return one card's DataView cell value: its glyph, coloured.

    ``"KS"`` -> ``'<span color="#1d1f20">K♠</span>'``. The markup is the
    element-content form -- the bald ``<span text="K♠" color="..."/>``
    renders *nothing* (measured on wxPython 4.3.1 / wxWidgets 3.3.3).

    The display text is escaped: the rank half of a stored code is
    echoed verbatim by :func:`format_card`, so a corrupt code could
    otherwise put a ``<``/``&`` into the renderer's markup.

    Raises:
        KeyError: If the code's last character is not a suit letter.
        IndexError: If *code* is empty.

    Both come from :func:`format_card` -- one code -> glyph decision,
    never a second copy.
    """
    return f'<span color="{_card_colour(code)}">{escape(format_card(code), quote=False)}</span>'


def cards_markup(codes: Sequence[str]) -> str:
    """Return a cell's DataView value: every card in its own colour.

    Space-joined, one span per card: the multi-card cell a per-cell
    attribute could not draw. An empty hand is the empty cell.
    """
    return " ".join(card_markup(code) for code in codes)
