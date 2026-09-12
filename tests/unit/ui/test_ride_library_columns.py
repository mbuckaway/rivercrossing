# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``ride_library``'s column defaults (plan 1b).

A ``wxDataViewCtrl`` column never sizes itself to its content, so an
unpinned column keeps the platform's 80 DIP default (measured on
4.3.1 osx-cocoa / wxWidgets 3.3.3: the control stretches only its
*last* column, and the Ride name is first) and the name clipped at
every window size. Plan 1b retires W10's elastic Ride column: every
column now opens at its own pinned width -- Ride 240 px, then compact
fixed widths for Date/Status/Entries, wide enough for the canvas
content at 150% text zoom (measured with ``GetFullTextExtent`` on the
stock 13 px GUI font: "2026-09-20" = 66 px, "REOPENED" = 59 px,
"Entries" = 37 px) -- and stays resizable, so the operator tunes the
widths by hand instead of the view re-filling the Ride column on
every size event.

The dialog's own floor (:data:`MIN_SIZE`) returns to a canvas-close
560x220: the four pinned widths total 530 px, so nothing needs W10's
doubled 1040 px width. ``RidesListModel.Compare`` backs the
still-sortable headers: the base ``DataViewIndexListModel`` already
sorts text columns by their displayed value, so the override only has
to case-fold the Ride name and sort Entries as a number (its
displayed value is ``str(entries)``, which would otherwise order "10"
before "2").

Importing ``ride_library`` pulls ``wx`` in transitively (its
``RidesListModel`` needs it at class-definition time, the same
``test_list_columns.py`` documents); nothing here constructs an App or
a window, only bare ``DataViewIndexListModel`` instances -- the same
headless construction ``tests/unit/presenters/test_teams.py`` already
does for ``TeamsListModel``.
"""

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.views import ride_library
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
    RIDES_LIST_COLUMN_FLAGS,
    RidesListModel,
)

# --- column defaults (plan 1b) ---------------------------------------


def test_column_widths_given_the_canvas_column_order_are_the_plan_defaults() -> None:
    """Ride | Date | Status | Entries open at 240 | 110 | 110 | 70.

    ``_COLUMN_WIDTHS`` is what ``_build_columns`` applies to each
    column, one entry per ``COLUMN_LABELS`` entry in canvas order.
    """
    assert ride_library._COLUMN_WIDTHS == (240, 110, 110, 70)


def test_column_widths_given_the_canvas_labels_carry_one_width_each() -> None:
    """One width per label: the column index never misaligns."""
    assert len(ride_library._COLUMN_WIDTHS) == len(COLUMN_LABELS)


def test_column_widths_given_the_ride_column_position_use_the_name_width() -> None:
    """Ride is first in canvas order, so its width is COL_NAME_WIDTH."""
    assert ride_library._COLUMN_WIDTHS[COL_NAME] == COL_NAME_WIDTH


def test_name_column_width_given_the_plan_default_is_240() -> None:
    """240 px is 3x wx's 80 DIP default and clears the longest name.

    "GORBA EPIC 2026 (copy)" measured 135 px at the stock 13 px GUI
    font, so the pinned width leaves the operator's long names room.
    """
    assert COL_NAME_WIDTH == 240


def test_compact_column_widths_given_the_canvas_content_never_clip() -> None:
    """Date/Status/Entries keep compact widths that fit their content.

    The three compact columns are fixed so their short content (an ISO
    date, a status word, an entry count) never truncates; 110 px covers
    "2026-09-20" (66 px) and "REOPENED" (59 px) even at the in-app 150%
    text zoom, and 70 px covers the "Entries" header (37 px).
    """
    assert (COL_DATE_WIDTH, COL_STATUS_WIDTH, COL_ENTRIES_WIDTH) == (110, 110, 70)


# --- RIDES_LIST_COLUMN_FLAGS (sortable + resizable, plan 1b) ---------


def test_rides_list_column_flags_given_the_pinned_list_keep_it_resizable() -> None:
    """The explicit flags retain the resizable bit wx drops."""
    flags = RIDES_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_RESIZABLE) == wx.dataview.DATAVIEW_COL_RESIZABLE


def test_rides_list_column_flags_given_the_pinned_list_keep_it_sortable() -> None:
    """Native header sorting is what the flags are for."""
    flags = RIDES_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_SORTABLE) == wx.dataview.DATAVIEW_COL_SORTABLE


# --- MIN_SIZE (plan 1b: back to a canvas-close floor) ----------------


def test_min_size_given_the_plan_floor_is_small_enough_to_sit_beside_the_console() -> None:
    """Plan 1b: the floor returns from W10's 1040x546 to 560x220.

    The four pinned widths total 530 px, inside the 560 px floor, so
    the dialog opens small and the operator widens a column by hand.
    """
    assert MIN_SIZE == (560, 220)


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
