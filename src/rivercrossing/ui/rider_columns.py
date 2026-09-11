# SPDX-License-Identifier: GPL-3.0-only
"""The rider lists' shared columns, cell text and sort keys (Phase 3).

``rider_editor_dlg``'s ``riders_list`` and the console's own
``console_riders_list`` draw the same rider rows -- Plate | Name |
Team | Sex, with a Cards column on the console's list only -- and
both order them by clicking a column header. This module is that one
description: each column's header label, the ``RiderRow`` field it
renders and the key it sorts by, plus :func:`toggle_sort` for the
click-to-sort direction rule the two presenters share.

Pure Python -- no ``wx`` import may ever land here (R-71). Both the
presenters (``ui.presenters.riders``, ordering rows) and the views
(``ui.views._support``, building columns and cells) import it, so it
must stay importable headless. The Cards cell's text comes from
``ui.card_text``, the one formatter all three call sites share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rivercrossing.ui.card_text import format_card

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ui.presenters.data_source import RiderRow

__all__ = [
    "CONSOLE_RIDER_COLUMNS",
    "EDITOR_RIDER_COLUMNS",
    "SOLO_TEAM_TEXT",
    "RiderColumn",
    "plate_order_key",
    "toggle_sort",
]

# The canvas's own word for a solo rider's Team cell
# ("123 Sam Ellis solo" -- W7 rework; the user copy is the literal
# word "solo", never the old em dash). Both rider lists render it.
SOLO_TEAM_TEXT = "solo"

# The Sex sort rule: M before F, an unknown sex (blank) after both.
# ``Rider.sex`` is normalized to "M"/"F"/None on the way in (csvio),
# but a caller may still hand this module a lower-case letter.
_SEX_ORDER: dict[str, int] = {"M": 0, "F": 1}
_UNKNOWN_SEX_ORDER = 2


@dataclass(frozen=True, slots=True)
class RiderColumn:
    """One rider-list column: header, cell accessor and sort key.

    ``sort_key`` values are homogeneous *within* one column (the
    Plate key is an ``(int, int)``/``(int, str)`` pair, the others are
    ``str`` or ``int``) but differ *between* columns, so the shared
    annotation is ``Any`` -- the only spelling mypy can accept for a
    per-column union inside a list of columns.
    """

    label: str
    value: Callable[[RiderRow], str]
    sort_key: Callable[[RiderRow], Any]


def plate_order_key(plate: str) -> tuple[int, int] | tuple[int, str]:
    """Return the numeric-aware sort key of one Plate cell.

    Digit plates order by value (``2`` before ``10``); every non-digit
    relay plate orders after all of them, alphabetically.
    """
    if plate.isdigit():
        return (0, int(plate))
    return (1, plate)


def _plate_cell(row: RiderRow) -> str:
    """Return the Plate cell: the rider's (or entry's) plate string."""
    return row.plate


def _name_cell(row: RiderRow) -> str:
    """Return the Name cell: the rider's full display name."""
    return row.name


def _team_cell(row: RiderRow) -> str:
    """Return the Team cell: the team's name, or the word "solo"."""
    return row.team if row.team is not None else SOLO_TEAM_TEXT


def _sex_cell(row: RiderRow) -> str:
    """Return the Sex cell: ``"M"``/``"F"``, or blank when unknown."""
    return row.sex if row.sex is not None else ""


def _cards_cell(row: RiderRow) -> str:
    """Return the Cards cell: the dealt codes as canvas suit glyphs."""
    return " ".join(format_card(code) for code in row.cards)


def _plate_sort_key(row: RiderRow) -> tuple[int, int] | tuple[int, str]:
    """Return the Plate sort key: numeric first, then text."""
    return plate_order_key(row.plate)


def _name_sort_key(row: RiderRow) -> str:
    """Return the Name sort key: the casefolded display name."""
    return row.name.casefold()


def _team_sort_key(row: RiderRow) -> str:
    """Return the Team sort key: the casefolded team name.

    A solo rider has no team at all, so it sorts before every named
    team -- the empty string is smaller than any real name.
    """
    return row.team.casefold() if row.team is not None else ""


def _sex_sort_key(row: RiderRow) -> int:
    """Return the Sex sort key: M, then F, then blank (unknown) last."""
    return _SEX_ORDER.get((row.sex or "").upper(), _UNKNOWN_SEX_ORDER)


def _cards_sort_key(row: RiderRow) -> str:
    """Return the Cards sort key: the space-joined raw card codes.

    Deliberately the stored codes, not the displayed glyphs: the
    column then orders by the deck's own code order -- a rank's card
    by rank's card -- instead of by the suit-glyph block's code
    points.
    """
    return " ".join(row.cards)


# rider_editor_dlg's riders_list: Plate | Name | Team | Sex
# (xrc-windows.md C's column order, extended by Phase 3's Sex column).
EDITOR_RIDER_COLUMNS: tuple[RiderColumn, ...] = (
    RiderColumn(label="Plate", value=_plate_cell, sort_key=_plate_sort_key),
    RiderColumn(label="Name", value=_name_cell, sort_key=_name_sort_key),
    RiderColumn(label="Team", value=_team_cell, sort_key=_team_sort_key),
    RiderColumn(label="Sex", value=_sex_cell, sort_key=_sex_sort_key),
)

# The console's console_riders_list (WS-H): the editor's own four,
# plus the Cards column -- one shared prefix, never a second copy.
CONSOLE_RIDER_COLUMNS: tuple[RiderColumn, ...] = (
    *EDITOR_RIDER_COLUMNS,
    RiderColumn(label="Cards", value=_cards_cell, sort_key=_cards_sort_key),
)


def toggle_sort(clicked: int, *, column: int | None, ascending: bool) -> tuple[int, bool]:
    """Return the (column, ascending) sort state for a header click.

    Args:
        clicked: The column index the operator just clicked.
        column: The currently sorted column index, or ``None``.
        ascending: The current sort direction.

    Returns:
        Clicking a new column sorts it ascending; re-clicking the
        active column reverses the current direction.
    """
    if column == clicked:
        return clicked, not ascending
    return clicked, True
