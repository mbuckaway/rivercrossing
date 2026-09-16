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
    "COL_TEAM",
    "COL_TIME",
    "COL_TOTAL",
    "LAP_TIME_COLUMN",
    "TOTAL_COLUMN",
    "card_status_text",
    "card_text_or_blank",
    "edited_row_indexes",
    "entry_text",
    "flagged_row_indexes",
    "flash_crossing_label",
    "lap_text",
    "review_issue",
]

COL_TIME = 0
COL_PLATE = 1
COL_NAME = 2  # the entry's display name (W9: header "Name", not "Entry")
COL_TEAM = 3  # the team's display name, `solo` for a solo entry
COL_CARD = 4
COL_LAP = 5
COL_LAP_TIME = 6
COL_TOTAL = 7

# W9 feed order -- Time | Plate | Name | Team | Card | Lap |
# Lap time | Total (the frozen canvas drawing still reads "Entry" and
# puts Card last; W15's canvas amendment records this change in
# xrc-windows.md).
COLUMN_LABELS: tuple[str, ...] = (
    "Time",
    "Plate",
    "Name",
    "Team",
    "Card",
    "Lap",
    "Lap time",
    "Total",
)

# R-37: the feed's two time columns, one per independent show setting
# (``show_total_times`` / ``show_lap_time``). Each is kept as a
# one-element column tuple so it matches the view's per-column handle;
# the clock stays untouched -- R-37 keeps it visible regardless.
TOTAL_COLUMN: tuple[int, ...] = (COL_TOTAL,)
LAP_TIME_COLUMN: tuple[int, ...] = (COL_LAP_TIME,)

# W9 explicit widths, one per :data:`COLUMN_LABELS` entry, so nothing
# truncates at the default window size: 80 fits "14:22:41"-shaped
# timestamps and "3:02:11" totals, 50 fits "9999" plates and "999"
# laps, 150 fits the longest demo name ("Trail Blazers (T)"), 130 fits
# a 15-character team name ("Blazing Saddles"), and 60 fits the 24x32
# card face plus padding (the width the rider editor's card columns
# use). DataView columns have no autosize-to-content
# (xrc-windows.md's code-side list), so the widths are pinned data
# here and applied by ``views/main_frame._build_columns``.
COLUMN_WIDTHS: tuple[int, ...] = (80, 50, 150, 130, 60, 50, 80, 80)


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
    every rider plate instead of interleaving with it.
    """
    return plate_order_key(row.plate)


def _name_sort_key(row: FeedRow) -> str:
    """Return the Name sort key: the casefolded entry name.

    Deliberately the bare name, without :func:`entry_text`'s DNF
    marker: a rider marked out mid-ride must not jump position in a
    sorted list.
    """
    return row.entry.casefold()


def _team_sort_key(row: FeedRow) -> str:
    """Return the Team sort key: the casefolded team display name.

    The Name column's own rule; a miss row -- the only blank Team
    cell -- sorts with the empty string ahead of every other row.
    """
    return row.team.casefold()


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
# ``float``, three ``str``, Lap an ``int`` -- but they differ *between*
# columns, so the shared annotation is the rider columns' own ``Any``.
COLUMN_SORT_KEYS: tuple[Callable[[FeedRow], Any], ...] = (
    _time_sort_key,
    _plate_sort_key,
    _name_sort_key,
    _team_sort_key,
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


# One display word per card disposition (``FeedRow.card_status``): the
# three states the feed derives by elimination, plus the blank default
# an undealt row (a miss) carries.
_CARD_STATUS_TEXTS: dict[str, str] = {
    "": "",
    "held": "Held",
    "credited": "Credited",
    "voided": "Void",
}


def card_status_text(row: FeedRow) -> str:
    """Return *row*'s card disposition as the Card column's status text.

    ``"Held"``, ``"Credited"`` or ``"Void"`` for the three states
    ``data_source._card_status_for`` derives -- and ``""`` for a row
    that dealt no card at all (``FeedRow.card_status``'s own default),
    so an undealt row renders the blank cell ``card_text_or_blank``
    already gives it. The words are title-cased for the cell; the
    stored field stays the lowercase token.
    """
    return _CARD_STATUS_TEXTS[row.card_status]


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


def review_issue(row: FeedRow) -> str:
    """Return the Needs Review tab's Issue cell text for *row*.

    Why the row entered review. A live duplicate pair (``row.
    duplicate``, Phase 3) is listed by its own bit and takes the
    wording first: the earlier twin's derived lap time is real, so it
    can carry no ``flagged`` bit of its own. Then the flagged row's
    *kind*: a short lap on a TEAM entry (``row.team_overlap``, the
    team-overlap report) is that team's riders' laps overlapping, so
    it reads "Team overlap"; any other short lap (``row.flagged``,
    R-34) reads as a plain short lap, whoever holds its card -- the
    Card column carries the held/credited/voided state
    (``FeedRow.card_status``, :func:`card_status_text`), so the Issue
    cell never repeats it. A row that is none of the three has no
    issue to show, so the cell is blank.

    The design docs name only the duplicate and short-lap wordings
    (xrc-windows.md's ``flagged_list`` Issue column); this middle
    reading is pinned here.
    """
    if row.duplicate:
        return "Duplicate crossing"
    if row.team_overlap:
        return "Team overlap"
    if row.flagged:
        return "Short lap"
    return ""


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
