# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the crossings feed's pure logic (E1.5.1).

Everything here runs without ``wx`` and without a display:
``ui/feed_model.py`` never imports it. The wx-facing half --
``CrossingsFeedModel``, a ``wx.dataview.DataViewIndexListModel``
subclass that delegates to the two functions tested here -- lives in
``views/main_frame.py`` and is proven by the real-toolkit suite,
``tests/functional/test_console_demo.py`` (``cards_imagelist``'s own
split between ``tests/unit/`` and ``tests/functional/`` is the
precedent this mirrors).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui.cards_imagelist import CARD_KEYS
from rivercrossing.ui.feed_model import (
    COL_CARD,
    COL_ENTRY,
    COL_LAP,
    COL_LAP_TIME,
    COL_PLATE,
    COL_TIME,
    COL_TOTAL,
    COLUMN_LABELS,
    TIME_COLUMNS,
    card_asset_key_or_none,
    edited_row_indexes,
    flagged_row_indexes,
)
from rivercrossing.ui.presenters.data_source import FeedRow

# --- column layout (pure data, matches xrc-windows.md section A) ------

CANVAS_COLUMN_ORDER = ("Time", "Plate", "Entry", "Lap", "Lap time", "Total", "Card")
CANVAS_COLUMN_INDEXES = (
    COL_TIME,
    COL_PLATE,
    COL_ENTRY,
    COL_LAP,
    COL_LAP_TIME,
    COL_TOTAL,
    COL_CARD,
)


def test_column_labels_matches_the_canvas_exact_order() -> None:
    """xrc-windows.md A: Time-Plate-Entry-Lap-Lap time-Total-Card."""
    assert COLUMN_LABELS == CANVAS_COLUMN_ORDER


def test_column_indexes_are_contiguous_from_zero_with_no_duplicate() -> None:
    """Every column index is used exactly once, 0..6."""
    assert sorted(CANVAS_COLUMN_INDEXES) == list(range(len(COLUMN_LABELS)))


def test_time_columns_is_exactly_lap_time_and_total() -> None:
    """R-37 hides these two only; the clock is a separate control."""
    assert TIME_COLUMNS == (COL_LAP_TIME, COL_TOTAL)


# --- card_asset_key_or_none ---------------------------------------

DEALT_CARD_CASES = (
    ("9H", "9h"),
    ("KS", "Ks"),
    ("4D", "4d"),
    ("JK", "joker"),
)

NON_CARD_CASES = ("held", "", "ZZ", "A")


@pytest.mark.parametrize(("card", "key"), DEALT_CARD_CASES)
def test_card_asset_key_or_none_given_a_dealt_code_returns_its_asset_key(
    card: str, key: str
) -> None:
    """A real dealt code resolves to the imagelist key it maps to."""
    assert card_asset_key_or_none(card) == key


@pytest.mark.parametrize("card", NON_CARD_CASES)
def test_card_asset_key_or_none_given_a_non_card_string_returns_none(card: str) -> None:
    """R-34's "held" placeholder and any other unmappable text is None.

    ``""`` is the empty-string boundary case (T-4): a missing card
    value must not be mistaken for a dealt one either.
    """
    assert card_asset_key_or_none(card) is None


@given(st.text(max_size=4))
def test_card_asset_key_or_none_given_arbitrary_text_never_raises_and_stays_in_the_deck(
    text: str,
) -> None:
    """Property: every input is either None or a real imagelist key."""
    key = card_asset_key_or_none(text)

    assert key is None or key in CARD_KEYS


# --- flagged_row_indexes -------------------------------------------


def _feed_row(*, plate: str = "1", flagged: bool = False, edited: bool = False) -> FeedRow:
    """Build a minimal ``FeedRow`` varying only what a test needs."""
    return FeedRow(
        time="14:00:00",
        plate=plate,
        entry="Rider",
        lap=1,
        lap_time="10:00",
        total="10:00",
        card="9H",
        flagged=flagged,
        edited=edited,
    )


FLAGGED_ROWS_CASES = (
    ((), frozenset()),
    ((_feed_row(flagged=False),), frozenset()),
    ((_feed_row(flagged=True),), frozenset({0})),
    (
        (
            _feed_row(plate="1", flagged=False),
            _feed_row(plate="2", flagged=False),
            _feed_row(plate="45", flagged=True),
            _feed_row(plate="4", flagged=False),
        ),
        frozenset({2}),
    ),
)


@pytest.mark.parametrize(("rows", "expected"), FLAGGED_ROWS_CASES)
def test_flagged_row_indexes_given_rows_returns_the_flagged_positions(
    rows: tuple[FeedRow, ...], expected: frozenset[int]
) -> None:
    """Boundary collection sizes (T-4): empty, single, many rows."""
    assert flagged_row_indexes(rows) == expected


def test_flagged_row_indexes_given_the_demo_feed_marks_only_the_plate_45_row() -> None:
    """Ties the flagged row to plate 45 -- never a bare row index."""
    rows = DemoDataSource().feed_rows()

    flagged = flagged_row_indexes(rows)

    assert {rows[index].plate for index in flagged} == {"45"}


@given(st.lists(st.booleans(), max_size=20))
def test_flagged_row_indexes_given_arbitrary_flags_agrees_with_each_rows_own_bit(
    flags: list[bool],
) -> None:
    """Property: membership matches each row's own flagged bit."""
    rows = [_feed_row(flagged=flag) for flag in flags]

    indexes = flagged_row_indexes(rows)

    agrees = all((index in indexes) == rows[index].flagged for index in range(len(rows)))
    assert agrees is True


# --- edited_row_indexes (E7.2.2: corrected crossings highlight) -----


EDITED_ROWS_CASES = (
    ((), frozenset()),
    ((_feed_row(edited=False),), frozenset()),
    ((_feed_row(edited=True),), frozenset({0})),
    (
        (
            _feed_row(plate="1", edited=True),
            _feed_row(plate="2", edited=False),
            _feed_row(plate="3", edited=True),
            _feed_row(plate="4", edited=False),
        ),
        frozenset({0, 2}),
    ),
)


@pytest.mark.parametrize(("rows", "expected"), EDITED_ROWS_CASES)
def test_edited_row_indexes_given_rows_returns_the_edited_positions(
    rows: tuple[FeedRow, ...], expected: frozenset[int]
) -> None:
    """Boundary collection sizes (T-4): empty, single, many rows."""
    assert edited_row_indexes(rows) == expected


@given(st.lists(st.booleans(), max_size=20))
def test_edited_row_indexes_given_arbitrary_edits_agrees_with_each_rows_own_bit(
    edits: list[bool],
) -> None:
    """T-7: membership matches each row's own edited bit."""
    rows = [_feed_row(edited=edited) for edited in edits]

    indexes = edited_row_indexes(rows)

    agrees = all((index in indexes) == rows[index].edited for index in range(len(rows)))
    assert agrees is True
