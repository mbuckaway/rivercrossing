# SPDX-License-Identifier: GPL-3.0-only
"""Pure logic behind the crossings feed DataView model (E1.5.1).

``crossings_list`` is authored in ``main.xrc`` as a bare
``wxDataViewCtrl`` shell; xrc-windows.md's own code-side footnote
puts its columns, rows and per-row attributes in code. The wx-facing
half of that -- ``CrossingsFeedModel``, a ``wx.dataview.
DataViewIndexListModel`` subclass -- lives in ``views/main_frame.py``
alongside its one consumer (SIMPLECODE Rule 7: no file split without
a second real consumer). *This* module is deliberately ``wx``-free:
it holds the column layout and the decisions ``CrossingsFeedModel``
delegates to -- plus the last-crossing label ``MainFrame.
flash_crossing`` renders -- so they are testable headlessly
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
    "COL_CARD",
    "COL_LAP",
    "COL_LAP_TIME",
    "COL_NAME",
    "COL_PLATE",
    "COL_TIME",
    "COL_TOTAL",
    "COLUMN_LABELS",
    "COLUMN_WIDTHS",
    "TIME_COLUMNS",
    "card_asset_key_or_none",
    "edited_row_indexes",
    "flash_crossing_label",
    "flagged_row_indexes",
]

COL_TIME = 0
COL_PLATE = 1
COL_NAME = 2  # the entry's display name (W9: header "Name", not "Entry")
COL_CARD = 3
COL_LAP = 4
COL_LAP_TIME = 5
COL_TOTAL = 6

# W9 feed order -- Time | Plate | Name | Card | Lap | Lap time |
# Total (the frozen canvas drawing still reads "Entry" and puts Card
# last; W15's canvas amendment records this change in xrc-windows.md).
COLUMN_LABELS: tuple[str, ...] = ("Time", "Plate", "Name", "Card", "Lap", "Lap time", "Total")

# R-37: the two columns hide-times removes; the clock stays untouched.
TIME_COLUMNS: tuple[int, ...] = (COL_LAP_TIME, COL_TOTAL)

# W9 explicit widths, one per :data:`COLUMN_LABELS` entry, so nothing
# truncates at the default window size: 80 fits "14:22:41"-shaped
# timestamps and "3:02:11" totals, 50 fits "9999" plates and "999"
# laps, 150 fits the longest demo name ("Trail Blazers (T)"), and 60
# fits the 24x32 card face plus padding (entry_detail's own D16 width
# for the same bitmaps). DataView columns have no autosize-to-content
# (xrc-windows.md's code-side list), so the widths are pinned data
# here and applied by ``views/main_frame._build_columns``.
COLUMN_WIDTHS: tuple[int, ...] = (80, 50, 150, 60, 50, 80, 80)


def card_asset_key_or_none(card: str) -> str | None:
    """Return *card*'s imagelist key, or ``None`` if it names no card.

    W9: the feed's Card column always carries a real dealt code -- a
    held crossing's row shows the held card's own code, not the
    retired literal placeholder -- so ``None`` is the blank-cell seam
    for any unmappable text (``""``, corrupt strings) that does not
    need a ``CardImageList`` (or ``wx``) to be detected.
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


def flash_crossing_label(row: FeedRow) -> str:
    """Return the last-crossing label ``flash_crossing`` renders.

    The console's just-recorded line: ``"✓ 12 · Rider 12 · Lap 3 ·
    1:40 · dealt 9♥"``. W9: the dealt card's code spells its suit as
    a glyph (the row's ``card`` is always a real code, held or not),
    and a held crossing -- the row's own ``flagged`` bit, R-34 --
    appends ``" (held)"``. The four-entry suit map is local to this
    helper: per-view maps already exist (``results_win``,
    ``dialogs``, ``pdfexport``) and this adds no shared structure.
    """
    glyphs = {"H": "♥", "D": "♦", "C": "♣", "S": "♠"}
    code = row.card
    display = "JK★" if code == "JK" else f"{code[:-1]}{glyphs[code[-1]]}"
    held = " (held)" if row.flagged else ""
    return (
        f"✓ {row.plate} · {row.entry} · Lap {row.lap} · "
        f"{row.lap_time} · dealt {display}{held}"
    )
