# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the crossings feed's pure logic (E1.5.1).

Everything here runs without ``wx`` and without a display:
``ui/feed_model.py`` never imports it. The wx-facing half --
``CrossingsFeedModel``, a ``wx.dataview.DataViewIndexListModel``
subclass that delegates to the pure functions tested here -- lives in
``views/main_frame.py`` and is proven by the real-toolkit functional
suite (``cards_imagelist``'s own split between ``tests/unit/`` and
``tests/functional/`` is the precedent this mirrors).
"""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.feed_model import (
    COL_CARD,
    COL_LAP,
    COL_LAP_TIME,
    COL_NAME,
    COL_PLATE,
    COL_TIME,
    COL_TOTAL,
    COLUMN_LABELS,
    COLUMN_WIDTHS,
    TIME_COLUMNS,
    card_text_or_blank,
    edited_row_indexes,
    flagged_row_indexes,
    flash_crossing_label,
    lap_text,
)
from rivercrossing.ui.presenters.data_source import FeedRow

# --- column layout (pure data, matches xrc-windows.md section A) ------

# W9 column order: the Entry header is renamed "Name" and the Card
# column moves ahead of Lap -- Time | Plate | Name | Card | Lap |
# Lap time | Total.
CANVAS_COLUMN_ORDER = ("Time", "Plate", "Name", "Card", "Lap", "Lap time", "Total")
CANVAS_COLUMN_INDEXES = (
    COL_TIME,
    COL_PLATE,
    COL_NAME,
    COL_CARD,
    COL_LAP,
    COL_LAP_TIME,
    COL_TOTAL,
)


def test_column_labels_matches_the_canvas_exact_order() -> None:
    """W9: Time-Plate-Name-Card-Lap-Lap time-Total."""
    assert COLUMN_LABELS == CANVAS_COLUMN_ORDER


def test_column_indexes_pin_card_before_lap_under_the_name_header() -> None:
    """W9: index 2 is Name, Card is 3, Lap is 4 -- in that order."""
    assert (COL_NAME, COL_CARD, COL_LAP) == (2, 3, 4)
    assert COLUMN_LABELS[COL_NAME] == "Name"


def test_column_labels_rename_entry_to_name_throughout() -> None:
    """W9: no column still reads "Entry" -- the header is "Name"."""
    assert "Entry" not in COLUMN_LABELS
    assert "Name" in COLUMN_LABELS


# --- explicit column widths (W9: nothing truncates at the default size)

# One width per canvas column, in canvas order: enough for the widest
# demo value in each text column ("14:22:41", "9999", "Trail Blazers
# (T)", "999", "3:02:11") and the 24x32 card face plus padding in the
# bitmap one (entry_detail's own D16 width precedent).
CANVAS_COLUMN_WIDTHS = (80, 50, 150, 60, 50, 80, 80)


def test_column_widths_length_matches_the_column_labels() -> None:
    """Every label has exactly one width -- the two stay in lockstep."""
    assert len(COLUMN_WIDTHS) == len(COLUMN_LABELS)


def test_column_widths_zipped_by_label_cover_each_canvas_column() -> None:
    """W9: per-label widths pin the content-fit numbers, in order."""
    assert dict(zip(CANVAS_COLUMN_ORDER, COLUMN_WIDTHS, strict=True)) == {
        "Time": 80,
        "Plate": 50,
        "Name": 150,
        "Card": 60,
        "Lap": 50,
        "Lap time": 80,
        "Total": 80,
    }


def test_column_indexes_are_contiguous_from_zero_with_no_duplicate() -> None:
    """Every column index is used exactly once, 0..6."""
    assert sorted(CANVAS_COLUMN_INDEXES) == list(range(len(COLUMN_LABELS)))


def test_time_columns_is_exactly_lap_time_and_total() -> None:
    """R-37 hides these two only; the clock is a separate control."""
    assert TIME_COLUMNS == (COL_LAP_TIME, COL_TOTAL)


# --- card_text_or_blank -------------------------------------------

DEALT_CARD_TEXT_CASES = (
    ("9H", "9♥"),
    ("6H", "6♥"),
    ("KS", "K♠"),
    ("TD", "T♦"),
    ("JK", "JK★"),
)

# W9: the feed never emits a literal "held" cell any more -- the card
# column always carries a real dealt code -- so the seam's old
# placeholder case is retired. Any other unmappable text still maps
# to "" (empty string boundary included, T-4); "A" and "ZZ" exercise
# the unknown-suit KeyError arm, "" the empty-code IndexError arm.
NON_CARD_TEXT_CASES = ("", "ZZ", "A")


@pytest.mark.parametrize(("card", "text"), DEALT_CARD_TEXT_CASES)
def test_card_text_or_blank_given_a_dealt_code_returns_its_canvas_text(
    card: str, text: str
) -> None:
    """A real dealt code resolves to ``format_card``'s canvas text."""
    assert card_text_or_blank(card) == text


@pytest.mark.parametrize("card", NON_CARD_TEXT_CASES)
def test_card_text_or_blank_given_a_non_card_string_returns_blank(card: str) -> None:
    """Any unmappable text is "" -- the blank cell seam (W9).

    ``""`` is the empty-string boundary case (T-4): a missing card
    value must not be mistaken for a dealt one either.
    """
    assert card_text_or_blank(card) == ""


@given(st.text(max_size=4))
def test_card_text_or_blank_given_arbitrary_text_never_raises_and_returns_display_or_blank(
    text: str,
) -> None:
    """Property: every input is either "" or a real glyph display."""
    display = card_text_or_blank(text)

    assert display == "" or display[-1] in {"♠", "♥", "♦", "♣"} or display == "JK★"


# --- flagged_row_indexes -------------------------------------------


def _feed_row(  # noqa: PLR0913 -- one keyword per feed field a test varies
    *,
    plate: str = "1",
    entry: str = "Rider",
    lap: int = 1,
    flagged: bool = False,
    edited: bool = False,
    missed: bool = False,
) -> FeedRow:
    """Build a minimal ``FeedRow`` varying only what a test needs."""
    return FeedRow(
        time="14:00:00",
        plate=plate,
        entry=entry,
        lap=lap,
        lap_time="10:00",
        total="10:00",
        card="9H",
        flagged=flagged,
        edited=edited,
        missed=missed,
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


def test_flagged_row_indexes_given_a_mixed_feed_marks_only_the_plate_45_row() -> None:
    """Ties the flagged row to plate 45 -- never a bare row index."""
    rows = (
        _feed_row(plate="123", flagged=False),
        _feed_row(plate="77", flagged=False),
        _feed_row(plate="45", flagged=True),
        _feed_row(plate="212", flagged=False),
    )

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


# --- flash_crossing_label (W9: dealt code glyphs + held suffix) -----


def _flash_row(*, card: str = "9H", flagged: bool = False) -> FeedRow:
    """Build the just-recorded crossing row ``flash_crossing`` shows."""
    return FeedRow(
        time="10:00:05",
        plate="12",
        entry="Rider 12",
        lap=3,
        lap_time="1:40",
        total="0:05:00",
        card=card,
        flagged=flagged,
    )


@pytest.mark.parametrize(
    ("card", "display"),
    [
        ("9H", "9♥"),
        ("KS", "K♠"),
        ("4D", "4♦"),
        ("7C", "7♣"),
        ("TD", "T♦"),
        ("JK", "JK★"),
    ],
    ids=["hearts", "spades", "diamonds", "clubs", "ten_keeps_t", "joker_star"],
)
def test_flash_crossing_label_given_a_dealt_code_spells_its_suit_glyph(
    card: str, display: str
) -> None:
    """W9 glyph polish: ``dealt 9H`` renders as ``dealt 9♥``."""
    label = flash_crossing_label(_flash_row(card=card))

    assert label == f"✓ 12 · Rider 12 · Lap 3 · 1:40 · dealt {display}"


@pytest.mark.parametrize(
    ("card", "display"),
    [("9H", "9♥"), ("QH", "Q♥"), ("JK", "JK★")],
    ids=["held_natural", "held_queen", "held_joker"],
)
def test_flash_crossing_label_given_a_flagged_row_appends_the_held_marker(
    card: str, display: str
) -> None:
    """W9: a held flash names the card and appends ``(held)``."""
    label = flash_crossing_label(_flash_row(card=card, flagged=True))

    assert label == f"✓ 12 · Rider 12 · Lap 3 · 1:40 · dealt {display} (held)"


def test_flash_crossing_label_given_an_unknown_suit_letter_raises_key_error() -> None:
    """Fail loud on a corrupt code, like results_win.format_card."""
    with pytest.raises(KeyError, match=re.escape("X")):
        flash_crossing_label(_flash_row(card="9X"))


@given(
    rank=st.text(alphabet="23456789TJQKA", min_size=1, max_size=1),
    suit=st.sampled_from("SHDC"),
    flagged=st.booleans(),
)
def test_flash_crossing_label_given_any_natural_code_renders_the_matching_glyph(
    rank: str, suit: str, *, flagged: bool
) -> None:
    """Property: rank+glyph pairing is exact for every natural card."""
    glyphs = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

    label = flash_crossing_label(_flash_row(card=f"{rank}{suit}", flagged=flagged))

    suffix = " (held)" if flagged else ""
    assert label.endswith(f"dealt {rank}{glyphs[suit]}{suffix}")
    assert label.startswith("✓ ")


# --- missed rows (K: a pass whose number the scorer missed) ----------


def test_lap_text_given_a_missed_row_returns_blank() -> None:
    """A miss shows no lap number -- the numeric row is blank."""
    row = _feed_row(plate="-", entry="missed", lap=0, missed=True)

    assert lap_text(row) == ""


def test_lap_text_given_a_crossing_row_returns_the_lap_number() -> None:
    """A normal crossing renders its 1-based lap number."""
    assert lap_text(_feed_row(lap=3)) == "3"


@given(lap=st.integers(min_value=0, max_value=10_000), missed=st.booleans())
def test_lap_text_given_any_row_is_blank_exactly_when_missed(lap: int, *, missed: bool) -> None:
    """Property: blank iff missed; otherwise the lap number as text."""
    row = _feed_row(lap=lap, missed=missed)

    assert lap_text(row) == ("" if missed else str(lap))


def test_flash_crossing_label_given_a_missed_row_names_the_plate_and_miss() -> None:
    """A miss flashes Plate "-" / Name "missed", with no card or lap."""
    row = _feed_row(plate="-", entry="missed", lap=0, missed=True)

    assert flash_crossing_label(row) == "- · missed"
