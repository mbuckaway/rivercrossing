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

It also owns the *red* of that text (:func:`card_markup`): the
console's cards are glyphs, so a red suit's colour is a text colour,
and the code -> red decision belongs beside the code -> glyph decision
it must agree with. **Only the red is explicit.** ♠/♣ and the joker's
``★`` render as bare text, so they take the control's own adaptive
foreground and follow the appearance -- hardcoding their ink is what
made those cells vanish on a dark list. The red is always redundant
with the glyph the cell draws -- the suit is spelled, not implied --
so no surface ever depends on it alone
(CODINGSTANDARDS-UX-DESKTOP.md §7), and it arrives as a caller's value
because a dark appearance needs a different red (:data:`DARK_RED`;
``theme.card_red()`` resolves it).

The red travels as **wx markup**, one ``<span color="...">`` per red
card, because a ``wx.dataview.DataViewItemAttr`` carries ONE text
colour per CELL: fine for a cell holding a single card, wrong for one
holding several (the standings' five-card "Best 5" hand, the console
Riders sidebar's Cards cell) -- a whole-cell red would paint that
hand's ♠/♣ faces red. The markup's *text* is escaped, so a corrupt
stored rank can never inject a tag into the renderer, bare cell
included: an unspanned run still goes to the markup parser.

:func:`contrast_ratio` is the WCAG 2.x measurement §7's 4.5:1 floor is
checked with; it lives here because the two reds it justifies do.

No ``wx`` import may ever land here: ``ui/rider_columns.py`` imports
this module and the presenters import that (R-71), so it must stay
importable headless.
"""

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
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

_SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

# The two suits a card face draws red; every other code -- ♠, ♣, the
# joker's star, anything malformed -- draws bare text.
_RED_SUITS = frozenset({"H", "D"})

JOKER_CODE = "JK"
JOKER_DISPLAY = "JK★"

# The suit red the exports already draw (``pdfexport``'s
# ``_SUIT_RED``, the HTML chip's ``.chip.r``, the bitmap generator's
# ``RED``) -- the light appearance's card red.
SUIT_RED = "#c0392b"
# App-only: the card red a dark appearance draws (#c0392b is 3.07:1 on
# a dark list, under §7's floor). The exports are light-only by design,
# so this has no export counterpart -- never "unify" the two reds.
DARK_RED = "#e57373"

# WCAG 2.x's sRGB breakpoint: at or below it the channel curve is
# linear, above it a power curve.
_SRGB_LINEAR_MAX = 0.04045


def is_red_suit(code: str) -> bool:
    """Return whether *code*'s card face draws in the suit red.

    ``"9H"``/``"10D"`` -> ``True``; ♠/♣ and the joker (``"JK"``,
    whose ``★`` is no suit) -> ``False``. A code that names no suit
    at all (empty, corrupt) draws bare text rather than erroring:
    this marks a glyph, so a code drawing no red-suit glyph draws no
    red.
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


def card_markup(code: str, red: str = SUIT_RED) -> str:
    """Return one card's DataView cell value: its glyph, red when red.

    ``"AH"`` -> ``'<span color="#c0392b">A♥</span>'``; ``"KS"`` ->
    ``"K♠"``. *red* is the appearance's own red (``theme.card_red()``,
    which every model takes as a value at construction); it reaches a
    red suit's span and nothing else -- ♠/♣ and the joker draw bare
    text and inherit the control's foreground, so those faces follow
    the appearance instead of being painted in an invisible ink.

    The markup is the element-content form -- the bald
    ``<span text="K♠" color="..."/>`` renders *nothing* (measured on
    wxPython 4.3.1 / wxWidgets 3.3.3).

    The display text is escaped: the rank half of a stored code is
    echoed verbatim by :func:`format_card`, so a corrupt code could
    otherwise put a ``<``/``&`` into the renderer's markup -- a bare
    cell included, since an unspanned run still reaches that parser.

    Raises:
        KeyError: If the code's last character is not a suit letter.
        IndexError: If *code* is empty.

    Both come from :func:`format_card` -- one code -> glyph decision,
    never a second copy.
    """
    text = escape(format_card(code), quote=False)
    if not is_red_suit(code):
        return text
    return f'<span color="{red}">{text}</span>'


def cards_markup(codes: Sequence[str], red: str = SUIT_RED) -> str:
    """Return a cell's DataView value: every red card in its own red.

    Space-joined, one span per red card: the multi-card cell a per-cell
    attribute could not draw. A ♠/♣ card is its own bare glyph, so a
    mixed hand's black faces take the list's foreground. An empty hand
    is the empty cell.
    """
    return " ".join(card_markup(code, red) for code in codes)


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG 2.x contrast ratio of two ``#rrggbb`` colours.

    The measurement §7's floor is checked with
    (CODINGSTANDARDS-UX-DESKTOP.md: "every custom-drawn surface must be
    measured"): ``#c0392b`` on white is 5.4384:1, and the dark red is
    4.6664:1 on ``#2c2c2e``, its worst measured dark list surface.
    Both clear 4.5:1, which is why the app carries two reds -- no
    single colour clears it on both backgrounds (the luminance windows
    are disjoint, so no hue would).

    Args:
        foreground: The text colour.
        background: The colour it is drawn on.

    Returns:
        The ratio, 1.0 (identical colours) to 21.0 (black on white).
        Always the lighter colour over the darker, so the argument
        order does not matter.

    Raises:
        ValueError: If either colour is not ``#rrggbb`` -- raised by
            ``bytes.fromhex``, never a silently wrong number.
    """
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _relative_luminance(colour: str) -> float:
    """Return *colour*'s WCAG 2.x relative luminance (0.0 to 1.0)."""
    channels = bytes.fromhex(colour.removeprefix("#"))
    linear = [_linearise(channel / 255) for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _linearise(channel: float) -> float:
    """Return one sRGB channel's linear value (WCAG 2.x)."""
    if channel <= _SRGB_LINEAR_MAX:
        return channel / 12.92
    # float(): mypy types ``float ** float`` as Any (int/float/complex
    # overloads), which the caller's declared return would reject.
    return float(((channel + 0.055) / 1.055) ** 2.4)
