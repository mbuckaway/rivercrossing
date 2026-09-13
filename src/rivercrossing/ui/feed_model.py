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

from typing import TYPE_CHECKING, Any

from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.rider_columns import plate_order_key

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import FeedRow

__all__ = [
    "COLUMN_LABELS",
    "COLUMN_SORT_KEYS",
    "COLUMN_WIDTHS",
    "COL_CARD",
    "COL_LAP",
    "COL_LAP_TIME",
    "COL_NAME",
    "COL_PLATE",
    "COL_TIME",
    "COL_TOTAL",
    "TIME_COLUMNS",
    "card_text_or_blank",
    "edited_row_indexes",
    "entry_text",
    "flagged_row_indexes",
    "flash_crossing_label",
    "lap_text",
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


def _time_sort_key(row: FeedRow) -> float:
    """Return the Time sort key: the elapsed reading in seconds.

    The numeric companion, not the ``h:mm:ss`` cell: as text "9:00:00"
    sorts *after* "10:00:00", so a string key would scramble a ride
    that runs past nine hours.
    """
    return row.elapsed_s


def _plate_sort_key(row: FeedRow) -> tuple[int, int] | tuple[int, str]:
    """Return the Plate sort key: numbered plates first, then text.

    The rider lists' own numeric-aware rule (``rider_columns.
    plate_order_key``): a relay ride's alphanumeric plate orders after
    every rider number instead of interleaving with it.
    """
    return plate_order_key(row.plate)


def _name_sort_key(row: FeedRow) -> str:
    """Return the Name sort key: the casefolded entry name.

    Deliberately the bare name, without :func:`entry_text`'s DNF
    marker: a rider marked out mid-ride must not jump position in a
    sorted list.
    """
    return row.entry.casefold()


def _card_sort_key(row: FeedRow) -> str:
    """Return the Card sort key: the stored code, not the glyph text.

    The rider lists' own rule: the column then orders by the deck's
    code order rather than by the suit-glyph block's code points.
    """
    return row.card


def _lap_sort_key(row: FeedRow) -> int:
    """Return the Lap sort key: the 1-based lap number."""
    return row.lap


def _lap_time_sort_key(row: FeedRow) -> float:
    """Return the Lap time sort key: the lap's own seconds."""
    return row.lap_time_s


def _total_sort_key(row: FeedRow) -> float:
    """Return the Total sort key: the entry's running seconds."""
    return row.total_s


# One sort key per :data:`COLUMN_LABELS` entry, in that order, for the
# list's native header arrows (``views/main_frame.CrossingsFeedModel.
# Compare``). A column's key is homogeneous *within* the column -- the
# Plate key is an ``(int, int)``/``(int, str)`` pair, three are
# ``float``, two ``str``, Lap an ``int`` -- but they differ *between*
# columns, so the shared annotation is the rider columns' own ``Any``.
COLUMN_SORT_KEYS: tuple[Callable[[FeedRow], Any], ...] = (
    _time_sort_key,
    _plate_sort_key,
    _name_sort_key,
    _card_sort_key,
    _lap_sort_key,
    _lap_time_sort_key,
    _total_sort_key,
)


def entry_text(row: FeedRow) -> str:
    """Return the feed's Name cell text for *row*.

    The DNF marker (Phase 4): a row whose rider -- or whose whole
    entry -- is out of the results renders a plain ``" DNF"`` suffix.
    Text, not a second bold channel: ``GetAttrByRow`` already carries
    the flagged/edited bold, and CODINGSTANDARDS-UX-DESKTOP.md §7
    forbids conveying meaning by colour or weight alone.
    """
    return f"{row.entry} DNF" if row.dnf else row.entry


def card_text_or_blank(card: str) -> str:
    """Return *card*'s display text, or ``""`` if it names no card.

    W9: the feed's Card column always carries a real dealt code -- a
    held crossing's row shows the held card's own code, not the
    retired literal placeholder -- so the blank cell is the seam for
    any unmappable text (``""``, corrupt strings) that does not need a
    ``CardImageList`` (or ``wx``) to be detected. Delegates the code
    to text mapping to the shared
    :func:`~rivercrossing.ui.card_text.format_card`, turning its
    ``KeyError`` (unknown suit) and ``IndexError`` (empty code) into
    the blank cell.
    """
    try:
        return format_card(card)
    except KeyError, IndexError:
        return ""


def flagged_row_indexes(rows: Sequence[FeedRow]) -> frozenset[int]:
    """Return the indexes of every flagged row in *rows* (R-34)."""
    return frozenset(index for index, row in enumerate(rows) if row.flagged)


def edited_row_indexes(rows: Sequence[FeedRow]) -> frozenset[int]:
    """Return the indexes of every edited row in *rows* (E7.2.2).

    The feed's second bold channel: a crossing a correction touched
    (edit/void/add-at-time/reassign -- the ``FeedRow.edited`` flag set
    by ``EngineDataSource.feed_rows`` from the engine's event log)
    renders bold like a flagged (short-lap) row does (R-34). Pure, so
    the wx-facing ``CrossingsFeedModel`` can delegate the decision
    here, exactly as it does for :func:`flagged_row_indexes`.
    """
    return frozenset(index for index, row in enumerate(rows) if row.edited)


def lap_text(row: FeedRow) -> str:
    """Return the feed's Lap cell text for *row*.

    A pending miss (``row.missed``, K) shows no lap number -- the whole
    numeric part of its row is blank -- so its Lap cell is ``""``, not
    the ``"0"`` its placeholder ``lap`` would otherwise render.
    """
    return "" if row.missed else str(row.lap)


def flash_crossing_label(row: FeedRow) -> str:
    """Return the last-crossing label ``flash_crossing`` renders.

    The console's just-recorded line: ``"✓ 12 · Rider 12 · Lap 3 ·
    1:40 · dealt 9♥"``. W9: the dealt card's code spells its suit as
    a glyph (the row's ``card`` is always a real code, held or not),
    and a held crossing -- the row's own ``held`` bit, R-34 --
    appends ``" (held)"``. The four-entry suit map is local to this
    helper: per-view maps already exist (``results_win``,
    ``dialogs``, ``pdfexport``) and this adds no shared structure.

    K: a miss row has no card or lap, so its label is just the blank
    plate ``-`` and the ``missed`` name.
    """
    if row.missed:
        return f"{row.plate} · {row.entry}"
    glyphs = {"H": "♥", "D": "♦", "C": "♣", "S": "♠"}
    code = row.card
    display = "JK★" if code == "JK" else f"{code[:-1]}{glyphs[code[-1]]}"
    held = " (held)" if row.held else ""
    return f"✓ {row.plate} · {row.entry} · Lap {row.lap} · {row.lap_time} · dealt {display}{held}"
