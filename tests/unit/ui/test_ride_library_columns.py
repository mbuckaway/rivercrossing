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

The dialog's own floor is now 2x the canvas width and 3x its height
(:data:`MIN_SIZE`), so the four columns fit at the minimum size too.
``RidesListModel.Compare`` backs the natively sortable headers: the
base ``DataViewIndexListModel`` already sorts text columns by their
displayed value, so the override only has to case-fold the Ride name
and sort Entries as a number (its displayed value is ``str(entries)``,
which would otherwise order "10" before "2").

Importing ``ride_library`` pulls ``wx`` in transitively (its
``RidesListModel`` needs it at class-definition time, the same
``test_list_columns.py`` documents); nothing here constructs an App or
a window, only bare ``DataViewIndexListModel`` instances -- the same
headless construction ``tests/unit/presenters/test_teams.py`` already
does for ``TeamsListModel``.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.views.ride_library import (
    COL_DATE,
    COL_DATE_WIDTH,
    COL_ENTRIES,
    COL_ENTRIES_WIDTH,
    COL_NAME,
    COL_NAME_WIDTH,
    COL_STATUS,
    COL_STATUS_WIDTH,
    COLUMN_LABELS,
    MIN_SIZE,
    RidesListModel,
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

    208 px is the fill the canvas's own 520 px dialog gave it (the
    list's client measured ~498 px there on 4.3.1 osx-cocoa;
    498 - 110 - 110 - 70 = 208) and fits the canvas's own longest name,
    "GORBA EPIC 2026 (copy)" (135 px at the stock font). It is now a
    floor seen only before the first layout: the dialog's own minimum
    is :data:`MIN_SIZE`'s 1040 px, so a laid-out list always hands the
    name column the leftover.
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


# --- MIN_SIZE (W10: 2x canvas width, 3x canvas height) ---------------


def test_min_size_given_the_canvas_dialog_floor_doubles_width_and_triples_height() -> None:
    """D16/W10: the 520x182 canvas floor grows to 1040x546.

    At 520 px the four columns did not fit (the last one clipped); the
    doubled width gives the elastic Ride column the slack, and the
    tripled height stops the list collapsing to the sizer's own tiny
    best height. Measured: ``SetMinSize`` + ``Fit()`` honours both
    dimensions at this size, so no ``SetSize`` fallback is needed.
    """
    assert MIN_SIZE == (1040, 546)


# --- RidesListModel.Compare (native header sorting) ------------------


def _rides_model(*rows: RideSummary) -> RidesListModel:
    """Build a model over *rows*, in the order given."""
    return RidesListModel(list(rows))


def _summary(  # noqa: PLR0913 -- the row's four fields, each with a default
    *,
    name: str = "Ride",
    date: str = "2026-09-20",
    status: RideStatus = RideStatus.DRAFT,
    entries: int = 1,
) -> RideSummary:
    """Build a minimal ``RideSummary`` with the given fields."""
    return RideSummary(name=name, date=date, status=status, entries=entries)


def test_rides_list_model_given_no_rows_is_empty_with_four_columns() -> None:
    """An empty rebuild is a valid state (T-4's empty collection)."""
    model = _rides_model()

    assert (model.GetCount(), model.GetColumnCount()) == (0, len(COLUMN_LABELS))


def test_rides_list_model_compare_given_one_row_compares_it_with_itself() -> None:
    """The single-row boundary: a lone row ties with itself (T-4)."""
    model = _rides_model(_summary(name="Only"))

    assert model.Compare(model.GetItem(0), model.GetItem(0), COL_ENTRIES, True) == 0  # noqa: FBT003


def test_rides_list_model_compare_name_sorts_case_folded() -> None:
    """Ride compares case-folded: "alpha" sorts before "Beta"."""
    model = _rides_model(_summary(name="alpha"), _summary(name="Beta"))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_NAME, True) < 0  # noqa: FBT003


def test_rides_list_model_compare_date_sorts_by_iso_string() -> None:
    """Date compares its ISO text, which is already chronological."""
    model = _rides_model(_summary(date="2026-01-31"), _summary(date="2026-02-01"))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_DATE, True) < 0  # noqa: FBT003


def test_rides_list_model_compare_status_sorts_by_display_text() -> None:
    """Status compares ``format_ride_status``'s upper-case text."""
    model = _rides_model(_summary(status=RideStatus.DRAFT), _summary(status=RideStatus.REOPENED))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_STATUS, True) < 0  # noqa: FBT003


def test_rides_list_model_compare_entries_sorts_numerically_not_as_text() -> None:
    """Entries sorts as a number: 2 before 10, never "10" before "2"."""
    model = _rides_model(_summary(name="Two", entries=2), _summary(name="Ten", entries=10))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_ENTRIES, True) < 0  # noqa: FBT003


def test_rides_list_model_compare_descending_reverses_the_entries_order() -> None:
    """Descending inverts the numeric Entries compare."""
    model = _rides_model(_summary(name="Two", entries=2), _summary(name="Ten", entries=10))

    assert model.Compare(model.GetItem(1), model.GetItem(0), COL_ENTRIES, False) < 0  # noqa: FBT003


def test_rides_list_model_compare_given_equal_keys_returns_zero() -> None:
    """Rows whose sort key is equal compare equal (a stable tie)."""
    model = _rides_model(_summary(name="Trail"), _summary(name="Trail"))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_NAME, True) == 0  # noqa: FBT003


# T-4 boundaries for the numeric sort key: the units->tens and
# tens->hundreds rolls, where a text compare first diverges from the
# integer one ("10" < "2", but 10 > 2).
_ENTRIES_ORDER_CASES = (
    (0, 1),  # the min end (0 = an empty ride)
    (1, 2),
    (2, 10),  # the units->tens roll where text order inverts
    (9, 10),  # max-1 / max of the units
    (10, 11),  # min / min+1 of the tens
    (99, 100),  # the tens->hundreds roll
)


@pytest.mark.parametrize(("smaller", "larger"), _ENTRIES_ORDER_CASES)
def test_rides_list_model_compare_entries_given_adjacent_counts_orders_numerically(
    smaller: int, larger: int
) -> None:
    """Every boundary pair orders the smaller count first, ascending."""
    model = _rides_model(
        _summary(name="Larger", entries=larger), _summary(name="Smaller", entries=smaller)
    )

    assert model.Compare(model.GetItem(1), model.GetItem(0), COL_ENTRIES, True) < 0  # noqa: FBT003


@given(a=st.integers(min_value=0, max_value=10_000), b=st.integers(min_value=0, max_value=10_000))
def test_rides_list_model_compare_entries_matches_the_integer_order(a: int, b: int) -> None:
    """Property: Entries returns exactly the sign of ``a - b``.

    The invariant the numeric override exists for: no entry count, at
    any magnitude, ever sorts by its decimal text instead of its value.
    """
    model = _rides_model(_summary(name="A", entries=a), _summary(name="B", entries=b))

    assert model.Compare(model.GetItem(0), model.GetItem(1), COL_ENTRIES, True) == (  # noqa: FBT003
        (a > b) - (a < b)
    )


@given(name_a=st.text(), name_b=st.text())
def test_rides_list_model_compare_name_is_antisymmetric(name_a: str, name_b: str) -> None:
    """Property: swapping the two items negates the name comparison."""
    model = _rides_model(_summary(name=name_a), _summary(name=name_b))

    forward = model.Compare(model.GetItem(0), model.GetItem(1), COL_NAME, True)  # noqa: FBT003
    backward = model.Compare(model.GetItem(1), model.GetItem(0), COL_NAME, True)  # noqa: FBT003

    assert forward == -backward
