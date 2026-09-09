# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``ride_library``'s column-width plan (W10).

The Ride-name column's 80 px default truncates the canvas's own names
at every window size: the dialog is resizable, but a fixed 80 px
column never uses the added width (xrc-windows.md D). Measured on
wxPython 4.3.1 osx-cocoa / wxWidgets 3.3.3: wxDataViewCtrl stretches
only its *last* column to fill the window, and the Ride column is
first, so the slack a widened dialog creates strands past Entries
while the name keeps clipping. The fix pins compact fixed widths for
Date/Status/Entries -- wide enough for the canvas content at 150%
text zoom, measured with ``GetFullTextExtent`` on the stock 13 px GUI
font: "2026-09-20" = 66 px, "REOPENED" = 59 px, "Entries" = 37 px --
and makes the Ride column elastic: ``name_column_width`` gives it
every pixel the compact columns leave, and the view re-applies it on
every size event, so a widened dialog widens the name column.

Importing ``ride_library`` pulls ``wx`` in transitively (its
``RidesListModel`` needs it at class-definition time, the same
``test_list_columns.py`` documents); nothing here constructs a wx
object, an App, or a window.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views.ride_library import (
    COL_DATE_WIDTH,
    COL_ENTRIES_WIDTH,
    COL_NAME_WIDTH,
    COL_STATUS_WIDTH,
    COLUMN_LABELS,
    name_column_width,
)

# The canvas's exact column order (xrc-windows.md D), one width per
# column in that order.
_COLUMN_WIDTHS_BY_CANVAS_ORDER = (
    COL_NAME_WIDTH,
    COL_DATE_WIDTH,
    COL_STATUS_WIDTH,
    COL_ENTRIES_WIDTH,
)


def test_column_width_constants_line_up_with_the_canvas_column_order() -> None:
    """Ride | Date | Status | Entries each carry one pinned width."""
    assert len(_COLUMN_WIDTHS_BY_CANVAS_ORDER) == len(COLUMN_LABELS)


def test_compact_column_widths_are_pinned_for_the_canvas_content() -> None:
    """Date/Status/Entries never clip; the name column takes the slack.

    The three compact columns are fixed so their short content (an ISO
    date, a status word, an entry count) never truncates at any window
    size; 110 px covers "2026-09-20" (66 px) and "REOPENED" (59 px)
    even at the in-app 150% text zoom, and 70 px covers the "Entries"
    header (37 px).
    """
    assert (COL_DATE_WIDTH, COL_STATUS_WIDTH, COL_ENTRIES_WIDTH) == (110, 110, 70)


def test_name_column_width_floor_is_pinned_for_the_canvas_minimum() -> None:
    """The name column never drops below its canvas-minimum fill.

    208 px is the fill at the dialog's 520 px floor (the list's client
    measures ~498 px there on 4.3.1 osx-cocoa; 498 - 110 - 110 - 70 =
    208) and fits the canvas's own longest name, "GORBA EPIC 2026
    (copy)" (135 px at the stock font).
    """
    assert COL_NAME_WIDTH == 208


_NAME_FILL_CASES = (
    (497, COL_NAME_WIDTH),  # clamp floor: one px below the exact-fill point
    (498, COL_NAME_WIDTH),  # exact fill at the 520 px dialog floor
    (499, 209),  # one px above: every extra px widens the name column
    (900, 610),
    (1344, 1054),  # the 1366 px field-laptop floor's list width
)


@pytest.mark.parametrize(("client_width", "expected"), _NAME_FILL_CASES)
def test_name_column_width_given_client_width_returns_leftover_or_floor(
    client_width: int, expected: int
) -> None:
    """Name width = client minus the compact columns, floored at 208."""
    assert name_column_width(client_width) == expected


@given(st.integers(min_value=498, max_value=4000))
def test_name_column_width_given_a_laid_out_list_takes_every_compact_leftover(
    client_width: int,
) -> None:
    """Property: above the floor, the name takes the full leftover.

    The exact-fill identity is what makes the column grow with the
    window: widening the dialog by N px widens the Ride column by N px.
    """
    compact_total = COL_DATE_WIDTH + COL_STATUS_WIDTH + COL_ENTRIES_WIDTH

    assert name_column_width(client_width) == client_width - compact_total
