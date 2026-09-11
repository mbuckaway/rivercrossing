# SPDX-License-Identifier: GPL-3.0-only
"""The one card-code -> canvas display-text mapping.

A stored card code (``Card.code()``: ``"KS"``, ``"9H"``, ``"JK"``)
renders on the canvas as the rank character plus a suit glyph --
``"K♠"``, ``"9♥"``, and ``"JK★"`` for the joker. Three call sites
draw that text: ``ui/rider_columns.py``'s Cards cell, ``results_win``'s
"Best 5" column and ``dialogs``'s void-card confirm. This module is
the one implementation they share; it replaced two identical private
copies (``results_win``'s own ``_SUIT_SYMBOLS``/``format_card`` pair
and ``dialogs``'s ``_format_card_code``).

No ``wx`` import may ever land here: ``ui/rider_columns.py`` imports
this module and the presenters import that (R-71), so it must stay
importable headless.
"""

__all__ = ["JOKER_CODE", "JOKER_DISPLAY", "format_card"]

_SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

JOKER_CODE = "JK"
JOKER_DISPLAY = "JK★"


def format_card(code: str) -> str:
    """Return one stored card code's canvas display text.

    ``"KS"`` -> ``"K♠"``; the joker -> ``"JK★"``. The rank character
    is already in its display form (``Card.code()``'s stored form uses
    "T" for ten, module-skeletons.md S4), so only the suit letter needs
    converting to a glyph.

    Raises:
        KeyError: If the last character is not a known suit letter
            (``S``/``H``/``D``/``C``).
        IndexError: If *code* is empty.
    """
    if code == JOKER_CODE:
        return JOKER_DISPLAY
    rank, suit = code[:-1], code[-1]
    return f"{rank}{_SUIT_SYMBOLS[suit]}"
