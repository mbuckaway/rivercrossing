# SPDX-License-Identifier: GPL-3.0-only
"""Pure logic behind the crossings feed DataView model (E1.5.1).

``crossings_list`` is authored in ``main.xrc`` as a bare
``wxDataViewCtrl`` shell; xrc-windows.md's own code-side footnote
puts its columns, rows and per-row attributes in code. The wx-facing
half of that -- ``CrossingsFeedModel``, a ``wx.dataview.
DataViewIndexListModel`` subclass -- lives in ``views/main_frame.py``
alongside its one consumer (SIMPLECODE Rule 7: no file split without
a second real consumer). *This* module is deliberately ``wx``-free:
it holds the column layout and the two decisions
``CrossingsFeedModel`` delegates to, so they are testable headlessly
(``tests/unit/ui/test_feed_model.py``) without a display, the same
split ``cards_imagelist.py`` draws between its pure helpers and
``CardImageList`` itself.
"""

from typing import TYPE_CHECKING

from rivercrossing.ui.cards_imagelist import UnknownCardCodeError, asset_key

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.ui.presenters.data_source import FeedRow

__all__ = [
    "COLUMN_LABELS",
    "COL_CARD",
    "COL_ENTRY",
    "COL_LAP",
    "COL_LAP_TIME",
    "COL_PLATE",
    "COL_TIME",
    "COL_TOTAL",
    "TIME_COLUMNS",
    "card_asset_key_or_none",
    "edited_row_indexes",
    "flagged_row_indexes",
]

COL_TIME = 0
COL_PLATE = 1
COL_ENTRY = 2
COL_LAP = 3
COL_LAP_TIME = 4
COL_TOTAL = 5
COL_CARD = 6

# xrc-windows.md section A's exact column order: "Time | Plate |
# Entry | Lap | Lap time | Total | Card".
COLUMN_LABELS: tuple[str, ...] = ("Time", "Plate", "Entry", "Lap", "Lap time", "Total", "Card")

# R-37: the two columns hide-times removes; the clock stays untouched.
TIME_COLUMNS: tuple[int, ...] = (COL_LAP_TIME, COL_TOTAL)


def card_asset_key_or_none(card: str) -> str | None:
    """Return *card*'s imagelist key, or ``None`` if it names no card.

    A held crossing (R-34) reports the literal placeholder string
    ``"held"`` in place of a dealt code -- this is the seam that
    tells the two apart without needing a ``CardImageList`` (or
    ``wx``) at all.
    """
    try:
        return asset_key(card)
    except UnknownCardCodeError:
        return None


def flagged_row_indexes(rows: Sequence[FeedRow]) -> frozenset[int]:
    """Return the indexes of every flagged row in *rows* (R-34)."""
    return frozenset(index for index, row in enumerate(rows) if row.flagged)


def edited_row_indexes(rows: Sequence[FeedRow]) -> frozenset[int]:
    """Return the indexes of every edited row in *rows* (E7.2.2).

    The feed's second bold channel: a crossing a correction touched
    (edit/void/add-at-time/reassign -- the ``FeedRow.edited`` flag set
    by ``EngineDataSource.feed_rows`` from the engine's event log)
    renders bold like a flagged (held-card) row does (R-34). Pure, so
    the wx-facing ``CrossingsFeedModel`` can delegate the decision
    here, exactly as it does for :func:`flagged_row_indexes`.
    """
    return frozenset(index for index, row in enumerate(rows) if row.edited)
