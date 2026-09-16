# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the crossings feed's pure logic (E1.5.1).

Everything here runs without ``wx`` and without a display:
``ui/feed_model.py`` never imports it. The wx-facing half --
``CrossingsFeedModel``, a ``wx.dataview.DataViewIndexListModel``
subclass that delegates to the pure functions tested here -- lives in
``views/main_frame.py`` and needs the real toolkit, so it is not
exercised in this headless file.
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
    COL_TEAM,
    COL_TIME,
    COL_TOTAL,
    COLUMN_LABELS,
    COLUMN_SORT_KEYS,
    COLUMN_WIDTHS,
    LAP_TIME_COLUMN,
    TOTAL_COLUMN,
    card_status_text,
    card_text_or_blank,
    edited_row_indexes,
    entry_text,
    flagged_row_indexes,
    flash_crossing_label,
    lap_text,
    review_issue,
)
from rivercrossing.ui.presenters.data_source import FeedRow

# --- column layout (pure data, matches xrc-windows.md section A) ------

# W9 column order: the Entry header is renamed "Name" and the Card
# column moves ahead of Lap; the Team column sits right after Name --
# Time | Plate | Name | Team | Card | Lap | Lap time | Total.
CANVAS_COLUMN_ORDER = ("Time", "Plate", "Name", "Team", "Card", "Lap", "Lap time", "Total")
CANVAS_COLUMN_INDEXES = (
    COL_TIME,
    COL_PLATE,
    COL_NAME,
    COL_TEAM,
    COL_CARD,
    COL_LAP,
    COL_LAP_TIME,
    COL_TOTAL,
)


def test_column_labels_matches_the_canvas_exact_order() -> None:
    """W9: Time-Plate-Name-Team-Card-Lap-Lap time-Total."""
    assert COLUMN_LABELS == CANVAS_COLUMN_ORDER


def test_column_indexes_pin_team_between_name_and_card() -> None:
    """Team sits right after Name: Name 2, Team 3, Card 4, Lap 5."""
    assert (COL_NAME, COL_TEAM, COL_CARD, COL_LAP) == (2, 3, 4, 5)
    assert COLUMN_LABELS[COL_TEAM] == "Team"


def test_column_labels_rename_entry_to_name_throughout() -> None:
    """W9: no column still reads "Entry" -- the header is "Name"."""
    assert "Entry" not in COLUMN_LABELS
    assert "Name" in COLUMN_LABELS


# --- explicit column widths (W9: nothing truncates at the default size)

# One width per canvas column, in canvas order: enough for the widest
# demo value in each text column ("14:22:41", "9999", "Trail Blazers
# (T)", "Dirt Dynamos", "999", "3:02:11") and the 24x32 card face plus
# padding in the bitmap one (the rider editor's card columns use the
# same width).
CANVAS_COLUMN_WIDTHS = (80, 50, 150, 130, 60, 50, 80, 80)


def test_column_widths_length_matches_the_column_labels() -> None:
    """Every label has exactly one width -- the two stay in lockstep."""
    assert len(COLUMN_WIDTHS) == len(COLUMN_LABELS)


def test_column_widths_zipped_by_label_cover_each_canvas_column() -> None:
    """W9: per-label widths pin the content-fit numbers, in order."""
    assert dict(zip(CANVAS_COLUMN_ORDER, COLUMN_WIDTHS, strict=True)) == {
        "Time": 80,
        "Plate": 50,
        "Name": 150,
        "Team": 130,
        "Card": 60,
        "Lap": 50,
        "Lap time": 80,
        "Total": 80,
    }


def test_column_indexes_are_contiguous_from_zero_with_no_duplicate() -> None:
    """Every column index is used exactly once, 0..6."""
    assert sorted(CANVAS_COLUMN_INDEXES) == list(range(len(COLUMN_LABELS)))


def test_total_and_lap_time_columns_are_distinct_single_column_tuples() -> None:
    """R-37: each independent toggle owns exactly its own column."""
    assert (LAP_TIME_COLUMN, TOTAL_COLUMN) == ((COL_LAP_TIME,), (COL_TOTAL,))


# --- column sort keys (Phase 4: the list's native header sort) -------


def test_column_sort_keys_length_matches_the_column_labels() -> None:
    """Every column has one sort key -- the two stay in lockstep."""
    assert len(COLUMN_SORT_KEYS) == len(COLUMN_LABELS)


def test_time_sort_key_given_hours_orders_by_seconds_not_by_text() -> None:
    """9 h sorts before 10 h; as text '10:00:00' would come first."""
    nine_hours = _feed_row(elapsed_s=32_400.0)
    ten_hours = _feed_row(elapsed_s=36_000.0)

    ordered = sorted((ten_hours, nine_hours), key=COLUMN_SORT_KEYS[COL_TIME])

    assert ordered == [nine_hours, ten_hours]


def test_plate_sort_key_given_digit_plates_orders_numerically() -> None:
    """Plate 2 sorts before plate 10, not after it."""
    two = _feed_row(plate="2")
    ten = _feed_row(plate="10")

    ordered = sorted((ten, two), key=COLUMN_SORT_KEYS[COL_PLATE])

    assert ordered == [two, ten]


def test_plate_sort_key_given_a_non_digit_plate_orders_after_the_digits() -> None:
    """A relay plate is text: every numbered plate comes first."""
    numbered = _feed_row(plate="999")
    relay = _feed_row(plate="A1")

    ordered = sorted((relay, numbered), key=COLUMN_SORT_KEYS[COL_PLATE])

    assert ordered == [numbered, relay]


def test_name_sort_key_given_mixed_case_names_orders_case_insensitively() -> None:
    """The key casefolds, so 'amy' sorts with 'Amy'."""
    amy = _feed_row(entry="Amy")
    zoe = _feed_row(entry="zoe")

    ordered = sorted((zoe, amy), key=COLUMN_SORT_KEYS[COL_NAME])

    assert ordered == [amy, zoe]


def test_name_sort_key_given_a_dnf_row_ignores_the_rendered_marker() -> None:
    """The marker never reorders the list it was added to."""
    plain = _feed_row(entry="Amy")
    marked = _feed_row(entry="Amy", dnf=True)

    keys = [COLUMN_SORT_KEYS[COL_NAME](row) for row in (plain, marked)]

    assert keys == ["amy", "amy"]


def test_team_sort_key_given_mixed_case_teams_orders_case_insensitively() -> None:
    """The key casefolds, so "aces" sorts with "Aces"."""
    aces = _feed_row(team="Aces")
    zoe = _feed_row(team="Zoe")

    ordered = sorted((zoe, aces), key=COLUMN_SORT_KEYS[COL_TEAM])

    assert ordered == [aces, zoe]


def test_team_sort_key_given_a_miss_row_returns_the_blank_key() -> None:
    """A miss has no entry and no team: its key is empty."""
    assert COLUMN_SORT_KEYS[COL_TEAM](_feed_row(entry="missed", team="", missed=True)) == ""


@given(team=st.text(max_size=20))
def test_team_sort_key_given_any_team_returns_the_casefolded_text(team: str) -> None:
    """Property: the key is the casefolded team, idempotently."""
    key = COLUMN_SORT_KEYS[COL_TEAM](_feed_row(team=team))

    assert key == team.casefold()
    assert key.casefold() == key


def test_card_sort_key_given_codes_orders_by_the_stored_code() -> None:
    """Stored codes, not glyphs: the deck's own order."""
    king = _feed_row(card="KH")
    nine = _feed_row(card="9H")

    ordered = sorted((king, nine), key=COLUMN_SORT_KEYS[COL_CARD])

    assert ordered == [nine, king]


def test_lap_sort_key_given_laps_orders_numerically() -> None:
    """Lap 2 sorts before lap 10, not after it."""
    two = _feed_row(lap=2)
    ten = _feed_row(lap=10)

    ordered = sorted((ten, two), key=COLUMN_SORT_KEYS[COL_LAP])

    assert ordered == [two, ten]


def test_lap_time_sort_key_given_hours_orders_by_seconds_not_by_text() -> None:
    """A 9 h lap sorts before a 10 h one, numerically."""
    nine_hours = _feed_row(lap_time_s=32_400.0)
    ten_hours = _feed_row(lap_time_s=36_000.0)

    ordered = sorted((ten_hours, nine_hours), key=COLUMN_SORT_KEYS[COL_LAP_TIME])

    assert ordered == [nine_hours, ten_hours]


def test_total_sort_key_given_hours_orders_by_seconds_not_by_text() -> None:
    """The Total column sorts by time, like the standings' own."""
    nine_hours = _feed_row(total_s=32_400.0)
    ten_hours = _feed_row(total_s=36_000.0)

    ordered = sorted((ten_hours, nine_hours), key=COLUMN_SORT_KEYS[COL_TOTAL])

    assert ordered == [nine_hours, ten_hours]


# --- entry_text (Phase 4: the DNF marker on the Name cell) -----------


def test_entry_text_given_a_healthy_row_returns_the_entry_name() -> None:
    """T-3 negative: nothing marked, nothing appended."""
    assert entry_text(_feed_row(entry="Rider 12")) == "Rider 12"


def test_entry_text_given_a_dnf_row_appends_the_marker() -> None:
    """A DNF rider's row carries a text marker, not a colour alone."""
    assert entry_text(_feed_row(entry="Rider 12", dnf=True)) == "Rider 12 DNF"


def test_entry_text_given_a_missed_row_keeps_the_miss_name() -> None:
    """A miss has no entry to be DNF: its own name renders unchanged."""
    assert entry_text(_feed_row(entry="missed", missed=True)) == "missed"


@given(entry=st.text(max_size=20), dnf=st.booleans())
def test_entry_text_given_any_row_appends_the_marker_exactly_when_dnf(
    entry: str, *, dnf: bool
) -> None:
    """Property: the cell is the name, suffixed iff DNF."""
    cell = entry_text(_feed_row(entry=entry, dnf=dnf))

    assert cell == (f"{entry} DNF" if dnf else entry)


# --- card_text_or_blank -------------------------------------------

DEALT_CARD_TEXT_CASES = (
    ("9H", "9♥"),
    ("6H", "6♥"),
    ("KS", "K♠"),
    ("10D", "10♦"),
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
    team: str = "",
    lap: int = 1,
    flagged: bool = False,
    held: bool = False,
    edited: bool = False,
    missed: bool = False,
    duplicate: bool = False,
    team_overlap: bool = False,
    dnf: bool = False,
    card: str = "9H",
    card_status: str = "",
    elapsed_s: float = 0.0,
    lap_time_s: float = 0.0,
    total_s: float = 0.0,
) -> FeedRow:
    """Build a minimal ``FeedRow`` varying only what a test needs."""
    return FeedRow(
        time="0:10:00",
        plate=plate,
        entry=entry,
        team=team,
        lap=lap,
        lap_time="10:00",
        total="10:00",
        card=card,
        flagged=flagged,
        held=held,
        edited=edited,
        missed=missed,
        duplicate=duplicate,
        team_overlap=team_overlap,
        dnf=dnf,
        card_status=card_status,
        elapsed_s=elapsed_s,
        lap_time_s=lap_time_s,
        total_s=total_s,
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


def _flash_row(*, card: str = "9H", flagged: bool = False, held: bool = False) -> FeedRow:
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
        held=held,
    )


@pytest.mark.parametrize(
    ("card", "display"),
    [
        ("9H", "9♥"),
        ("KS", "K♠"),
        ("4D", "4♦"),
        ("7C", "7♣"),
        ("10D", "10♦"),
        ("JK", "JK★"),
    ],
    ids=["hearts", "spades", "diamonds", "clubs", "ten_spells_10", "joker_star"],
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
def test_flash_crossing_label_given_a_held_row_appends_the_held_marker(
    card: str, display: str
) -> None:
    """W9: a held flash names the card and appends ``(held)``."""
    label = flash_crossing_label(_flash_row(card=card, held=True))

    assert label == f"✓ 12 · Rider 12 · Lap 3 · 1:40 · dealt {display} (held)"


@pytest.mark.parametrize(
    ("flagged", "held", "suffix"),
    [
        (False, False, ""),
        (True, False, ""),
        (False, True, " (held)"),
        (True, True, " (held)"),
    ],
    ids=["normal", "short_credited", "held_only", "short_held"],
)
def test_flash_crossing_label_given_a_row_appends_held_exactly_when_held(
    flagged: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    held: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    suffix: str,
) -> None:
    """T-13: the suffix follows ``held``, not ``flagged``."""
    label = flash_crossing_label(_flash_row(flagged=flagged, held=held))

    assert label == f"✓ 12 · Rider 12 · Lap 3 · 1:40 · dealt 9♥{suffix}"


def test_flash_crossing_label_given_an_unknown_suit_letter_raises_key_error() -> None:
    """Fail loud on a corrupt code, like results_win.format_card."""
    with pytest.raises(KeyError, match=re.escape("X")):
        flash_crossing_label(_flash_row(card="9X"))


@given(
    rank=st.sampled_from(("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")),
    suit=st.sampled_from("SHDC"),
    held=st.booleans(),
)
def test_flash_crossing_label_given_any_natural_code_renders_the_matching_glyph(
    rank: str, suit: str, *, held: bool
) -> None:
    """Property: rank+glyph pairing is exact for every natural card."""
    glyphs = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

    label = flash_crossing_label(_flash_row(card=f"{rank}{suit}", held=held))

    suffix = " (held)" if held else ""
    assert label.endswith(f"dealt {rank}{glyphs[suit]}{suffix}")
    assert label.startswith("✓ ")


def test_flagged_row_indexes_given_a_credited_short_lap_still_marks_the_row() -> None:
    """Always-deal: a short lap is bolded for review though not held."""
    rows = (_feed_row(plate="12", flagged=True, held=False),)

    assert flagged_row_indexes(rows) == frozenset({0})


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


# --- card_status_text (Scope 1b: the Card column's disposition) ------
# ``FeedRow.card_status`` is the feed's card state, derived by
# elimination in ``data_source._crossing_feed_row``: "held" (R-34,
# awaiting confirm/void), "credited" (in the entry's hand), "voided"
# (in neither). This helper is its display word, so the console's Card
# column can carry the state the Needs Review Issue cell no longer
# repeats (``review_issue``).

CARD_STATUS_CASES = (
    ("", ""),
    ("held", "Held"),
    ("credited", "Credited"),
    ("voided", "Void"),
)


@pytest.mark.parametrize(("status", "text"), CARD_STATUS_CASES)
def test_card_status_text_given_a_card_status_returns_its_display_word(
    status: str, text: str
) -> None:
    """Each of the three dispositions has its own Card-cell word.

    ``""`` is the blank-cell case: a miss row (and every other row that
    dealt no card) carries the field's default, not a disposition.
    """
    assert card_status_text(_feed_row(card_status=status)) == text


@given(status=st.sampled_from(("", "held", "credited", "voided")))
def test_card_status_text_given_any_known_status_is_blank_exactly_when_that_status_is(
    status: str,
) -> None:
    """Property: only the undealt default renders the blank cell."""
    text = card_status_text(_feed_row(card_status=status))

    assert (text == "") is (status == "")


# --- review_issue (Needs Review tab: why this row is here) -----------


# The four texts the Needs Review tab can show, and the full decision
# table (T-13): four independent booleans, 2^4 rows -- the limit the
# rule allows without asking first. A duplicate outranks everything
# (the earlier twin's derived lap time is real, so it can carry no
# flagged bit of its own); a team overlap outranks the plain short lap
# (a flagged crossing on a TEAM entry, ``FeedRow.team_overlap``);
# ``held`` never refines the wording at all -- the Card column carries
# the held/credited/voided state (``FeedRow.card_status``,
# ``card_status_text``), so a held short lap reads exactly like the
# credited one and the held rows pin that.
REVIEW_ISSUE_TEXT = "Short lap"
REVIEW_ISSUE_HELD_TEXT = REVIEW_ISSUE_TEXT
REVIEW_ISSUE_TEAM_OVERLAP_TEXT = "Team overlap"
REVIEW_ISSUE_DUPLICATE_TEXT = "Duplicate crossing"
REVIEW_ISSUE_TEXTS = frozenset(
    {
        "",
        REVIEW_ISSUE_TEXT,
        REVIEW_ISSUE_HELD_TEXT,
        REVIEW_ISSUE_TEAM_OVERLAP_TEXT,
        REVIEW_ISSUE_DUPLICATE_TEXT,
    }
)

REVIEW_ISSUE_CASES = (
    (False, False, False, False, ""),
    (False, False, False, True, ""),
    (False, False, True, False, REVIEW_ISSUE_TEAM_OVERLAP_TEXT),
    (False, False, True, True, REVIEW_ISSUE_TEAM_OVERLAP_TEXT),
    (False, True, False, False, REVIEW_ISSUE_TEXT),
    (False, True, False, True, REVIEW_ISSUE_HELD_TEXT),
    (False, True, True, False, REVIEW_ISSUE_TEAM_OVERLAP_TEXT),
    (False, True, True, True, REVIEW_ISSUE_TEAM_OVERLAP_TEXT),
    (True, False, False, False, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, False, False, True, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, False, True, False, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, False, True, True, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, True, False, False, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, True, False, True, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, True, True, False, REVIEW_ISSUE_DUPLICATE_TEXT),
    (True, True, True, True, REVIEW_ISSUE_DUPLICATE_TEXT),
)


@pytest.mark.parametrize(
    ("duplicate", "flagged", "team_overlap", "held", "expected"),
    REVIEW_ISSUE_CASES,
    ids=[
        "clean",
        "clean_held",
        "team_overlap",
        "team_overlap_held",
        "credited_short_lap",
        "held_short_lap",
        "flagged_team_overlap",
        "held_team_overlap",
        "duplicate",
        "duplicate_held",
        "duplicate_team_overlap",
        "duplicate_team_overlap_held",
        "duplicate_short_lap",
        "duplicate_held_short_lap",
        "duplicate_flagged_team_overlap",
        "duplicate_held_flagged_team_overlap",
    ],
)
def test_review_issue_given_a_rows_flags_returns_its_review_reason(  # noqa: PLR0913, PLR0917 -- the four flags plus the reason
    duplicate: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    flagged: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    team_overlap: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    held: bool,  # noqa: FBT001 -- parametrize passes the flags positionally
    expected: str,
) -> None:
    """T-13: duplicate outranks overlap, which outranks short lap."""
    row = _feed_row(duplicate=duplicate, flagged=flagged, team_overlap=team_overlap, held=held)

    assert review_issue(row) == expected


def test_review_issue_given_a_flagged_team_crossing_reads_as_a_team_overlap() -> None:
    """A TEAM entry's flagged crossing is the overlap, not a lap."""
    row = _feed_row(flagged=True, team="Trail Blazers", team_overlap=True)

    assert review_issue(row) == "Team overlap"


def test_review_issue_given_a_flagged_solo_crossing_reads_as_a_short_lap() -> None:
    """T-3 negative: a solo short lap is no overlap."""
    row = _feed_row(flagged=True, team="solo", team_overlap=False)

    assert review_issue(row) == "Short lap"


def test_review_issue_given_a_duplicated_team_crossing_reads_as_a_duplicate() -> None:
    """A duplicate stays the first wording, overlap beside it or not."""
    row = _feed_row(duplicate=True, flagged=True, team_overlap=True)

    assert review_issue(row) == "Duplicate crossing"


def test_review_issue_given_a_held_short_lap_reads_as_a_plain_short_lap() -> None:
    """Scope 1b: the Card column carries the hold, not the Issue."""
    row = _feed_row(flagged=True, held=True, card="9H", card_status="held")

    assert review_issue(row) == "Short lap"


@given(
    duplicate=st.booleans(),
    flagged=st.booleans(),
    team_overlap=st.booleans(),
    held=st.booleans(),
)
def test_review_issue_given_any_flags_is_blank_exactly_when_clean(  # noqa: PLR0913 -- one argument per review flag
    *, duplicate: bool, flagged: bool, team_overlap: bool, held: bool
) -> None:
    """T-7: an issue shows iff one of the three review bits is set."""
    row = _feed_row(duplicate=duplicate, flagged=flagged, team_overlap=team_overlap, held=held)

    issue = review_issue(row)

    assert (issue == "") is not (duplicate or flagged or team_overlap)
    assert issue in REVIEW_ISSUE_TEXTS
