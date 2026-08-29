# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the four list windows' pure format logic (E1.5.2).

Only the column/cell *formatting* functions each view module defines
are exercised here -- ``RidesListModel``/``RidersListModel``/
``CardsHeldModel``/``EntryLapsModel``/``StandingsListModel`` all
subclass ``wx.dataview.DataViewIndexListModel`` and are proven against
the real toolkit instead, in ``tests/functional/test_lists_demo.py``
(``cards_imagelist``'s own split between ``tests/unit/`` and
``tests/functional/`` is the precedent this mirrors).

Importing ``ride_library``/``rider_editor``/``results_win`` does pull
in ``wx`` transitively (their ``DataViewIndexListModel`` subclasses
need it at class-definition time, the same as ``views/main_frame.py``'s
``CrossingsFeedModel``) -- unlike ``feed_model.py``/
``cards_imagelist.py``'s stricter, wx-import-free split. This task's
file batch has no room for a fifth, wx-free sibling module to hold
these functions instead (see ``ride_library.py``'s own module
docstring); nothing below constructs a wx object, an App, or a
window, so no display or session is needed to run it. A follow-up
that touches ``views/__init__.py`` too could extract a genuinely
wx-free module mirroring ``feed_model.py``, closing this gap.
"""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters.data_source import RiderRow, StandingsRow
from rivercrossing.ui.views.results_win import (
    JOKER_DISPLAY,
    TIE_BADGE,
    format_best5,
    format_card,
    format_place,
)
from rivercrossing.ui.views.ride_library import format_ride_status
from rivercrossing.ui.views.rider_editor import SOLO_TEAM_TEXT, format_team

# --- ride_library.format_ride_status ----------------------------------

STATUS_CASES = (
    (RideStatus.DRAFT, "DRAFT"),
    (RideStatus.RUNNING, "RUNNING"),
    (RideStatus.FINISHED, "FINISHED"),
    (RideStatus.REOPENED, "REOPENED"),
)


@pytest.mark.parametrize(("status", "expected"), STATUS_CASES)
def test_format_ride_status_given_each_lifecycle_state_returns_its_canvas_text(
    status: RideStatus, expected: str
) -> None:
    """xrc-windows.md D: "RUNNING"/"FINISHED", upper-case each state."""
    assert format_ride_status(status) == expected


@given(st.sampled_from(RideStatus))
def test_format_ride_status_given_any_status_is_idempotent_uppercase(status: RideStatus) -> None:
    """Property: the result is already upper-case (T-7 idempotence)."""
    text = format_ride_status(status)

    assert text == text.upper()


# --- rider_editor.format_team ---------------------------------------


def _rider(*, plate: str = "1", name: str = "Rider", team: str | None = None) -> RiderRow:
    """Build a minimal ``RiderRow`` varying only what a test needs."""
    return RiderRow(plate=plate, name=name, team=team)


TEAM_CASES = (
    (_rider(team=None), SOLO_TEAM_TEXT),  # T-4 nullable: missing
    (_rider(team=""), ""),  # T-4 nullable: present-but-empty
    (_rider(team="Trail Blazers"), "Trail Blazers"),  # T-4 nullable: present
)


@pytest.mark.parametrize(("row", "expected"), TEAM_CASES)
def test_format_team_given_a_rider_row_returns_its_canvas_cell_text(
    row: RiderRow, expected: str
) -> None:
    """None -> em dash; a real team name passes through unchanged."""
    assert format_team(row) == expected


@given(st.text(max_size=20))
def test_format_team_given_any_non_none_team_returns_it_unchanged(team: str) -> None:
    """Property: type/value preservation for every real team string."""
    row = _rider(team=team)

    assert format_team(row) == team


# --- results_win.format_card ---------------------------------------

CARD_DISPLAY_CASES = (
    ("KS", "K♠"),
    ("KC", "K♣"),
    ("KD", "K♦"),
    ("9H", "9♥"),
    ("TH", "T♥"),
    ("AC", "A♣"),
    ("4D", "4♦"),
    ("4S", "4♠"),
    ("JK", JOKER_DISPLAY),
)


@pytest.mark.parametrize(("code", "expected"), CARD_DISPLAY_CASES)
def test_format_card_given_a_stored_code_returns_its_canvas_glyph_text(
    code: str, expected: str
) -> None:
    """Every suit glyph, the "T" ten boundary, and the joker marker."""
    assert format_card(code) == expected


def test_format_card_given_an_unknown_suit_letter_raises_key_error() -> None:
    """T-5: the implicit ``KeyError`` from ``_SUIT_SYMBOLS[suit]``."""
    with pytest.raises(KeyError, match=re.escape("'X'")):
        format_card("9X")


_VALID_RANKS = tuple("23456789TJQKA")
_VALID_SUITS = tuple("SHDC")


def _join_rank_suit(rank: str, suit: str) -> str:
    """Build one stored card code from a rank and a suit letter."""
    return f"{rank}{suit}"


_valid_card_codes = st.builds(
    _join_rank_suit, st.sampled_from(_VALID_RANKS), st.sampled_from(_VALID_SUITS)
)
_SUIT_GLYPHS = frozenset({"♠", "♥", "♦", "♣"})


@given(_valid_card_codes)
def test_format_card_given_any_valid_code_ends_in_a_known_suit_glyph_or_is_the_joker(
    code: str,
) -> None:
    """Property: every real card's display text carries a real glyph."""
    text = format_card(code)

    assert text[-1] in _SUIT_GLYPHS or text == JOKER_DISPLAY


# --- results_win.format_best5 ---------------------------------------

BEST5_CASES = (
    (("KS", "KC", "KD", "JK", "9H"), "K♠ K♣ K♦ JK★ 9♥"),
    (("QH", "JH", "TH", "9H", "8H"), "Q♥ J♥ T♥ 9♥ 8♥"),
    (("AC", "AD", "AH", "4D", "4S"), "A♣ A♦ A♥ 4♦ 4♠"),
    ((), ""),  # T-4 collection boundary: empty
    (("JK",), JOKER_DISPLAY),  # T-4 collection boundary: single
)


@pytest.mark.parametrize(("cards", "expected"), BEST5_CASES)
def test_format_best5_given_a_hand_returns_the_canvas_exact_best5_cell(
    cards: tuple[str, ...], expected: str
) -> None:
    """The three demo standings rows, plus the empty/single boundary."""
    assert format_best5(cards) == expected


@given(st.lists(_valid_card_codes, min_size=1, max_size=8))
def test_format_best5_given_any_non_empty_hand_preserves_its_card_count(
    cards: list[str],
) -> None:
    """Property: length preservation -- one display token per card."""
    text = format_best5(cards)

    assert len(text.split(" ")) == len(cards)


# --- results_win.format_place (E6.4.1 ⚠ badge) ---------------------


def _standing(*, place: int, draw_required: bool = False) -> StandingsRow:
    """Build a minimal standings row varying only place/draw state."""
    return StandingsRow(
        place=place,
        plate="7",
        entry="Rider",
        laps=1,
        total="0:01:00",
        best5=(),
        hand="High Card — Ace",
        draw_required=draw_required,
    )


PLACE_CASES = (
    (_standing(place=1), "1"),
    (_standing(place=12), "12"),
    (_standing(place=1, draw_required=True), f"{TIE_BADGE} 1"),
    (_standing(place=2, draw_required=True), f"{TIE_BADGE} 2"),
)


@pytest.mark.parametrize(("row", "expected"), PLACE_CASES)
def test_format_place_given_a_standings_row_returns_its_canvas_cell_text(
    row: StandingsRow, expected: str
) -> None:
    """Draw rows carry the warning badge; ordinary rows just the place.

    E6.4.1's decision beyond the brief: the ⚠ badge renders as a
    leading glyph in the Place cell (the canvas pins exactly seven
    columns and shows no tie rows), rather than a new eighth column.
    """
    assert format_place(row) == expected


@given(
    place=st.integers(min_value=1, max_value=999),
    draw_required=st.booleans(),
)
def test_format_place_preserves_the_place_number_and_badges_only_draw_rows(
    place: int,
    draw_required: bool,  # noqa: FBT001 -- a generated property value
) -> None:
    """Invariant: the cell ends in the place number; ⚠ iff draw."""
    text = format_place(_standing(place=place, draw_required=draw_required))

    assert text.endswith(str(place))
    assert (TIE_BADGE in text) is draw_required
