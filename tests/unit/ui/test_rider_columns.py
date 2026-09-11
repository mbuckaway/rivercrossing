# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the shared rider-list columns (Phase 3), tests-first.

``ui/rider_columns.py`` is the one home for what
``rider_editor_dlg``'s ``riders_list`` and the console's own
``console_riders_list`` genuinely share: the column order, each cell's
text, each column's sort key, and the click-to-sort direction rule.
The module imports no ``wx`` (R-71: presenters import it too), so
everything here runs headless with no display.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.rider_columns import (
    CONSOLE_RIDER_COLUMNS,
    EDITOR_RIDER_COLUMNS,
    SOLO_TEAM_TEXT,
    RiderColumn,
    toggle_sort,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _row(  # noqa: PLR0913 -- one keyword per RiderRow field, with every field defaulted
    *,
    plate: str = "1",
    name: str = "Rider",
    team: str | None = None,
    sex: str | None = None,
    cards: tuple[str, ...] = (),
) -> RiderRow:
    """Build a minimal ``RiderRow`` varying only what a test needs."""
    return RiderRow(plate=plate, name=name, team=team, sex=sex, cards=cards)


def _column(label: str) -> RiderColumn:
    """Return the shared column labelled *label* (either list)."""
    return next(column for column in CONSOLE_RIDER_COLUMNS if column.label == label)


def _plates_in_sort_order(label: str, rows: Sequence[RiderRow]) -> list[str]:
    """Return *rows*' plates after sorting them by column *label*."""
    return [row.plate for row in sorted(rows, key=_column(label).sort_key)]


# ------------------------------------------------------ column lists


def test_editor_columns_given_the_rider_editor_are_plate_name_team_sex() -> None:
    """rider_editor_dlg's riders_list is Plate | Name | Team | Sex."""
    assert [column.label for column in EDITOR_RIDER_COLUMNS] == ["Plate", "Name", "Team", "Sex"]


def test_console_columns_given_the_console_list_append_cards() -> None:
    """The console list adds Cards after the editor's own four."""
    assert [column.label for column in CONSOLE_RIDER_COLUMNS] == [
        "Plate",
        "Name",
        "Team",
        "Sex",
        "Cards",
    ]


def test_console_columns_given_the_console_list_start_with_the_editors_own_four() -> None:
    """The lists cannot drift: the console's four are the editor's."""
    assert CONSOLE_RIDER_COLUMNS[: len(EDITOR_RIDER_COLUMNS)] == EDITOR_RIDER_COLUMNS


def test_column_given_every_shared_column_is_a_rider_column() -> None:
    """One description type backs both: label, cell, sort key."""
    assert [type(column) for column in CONSOLE_RIDER_COLUMNS] == [RiderColumn] * 5


# --------------------------------------------------------- cell text


CELL_CASES = (
    ("Plate", _row(plate="123"), "123"),
    ("Name", _row(name="Sam Ellis"), "Sam Ellis"),
    ("Team", _row(team=None), SOLO_TEAM_TEXT),  # T-4 nullable: missing
    ("Team", _row(team=""), ""),  # T-4 nullable: present-but-empty
    ("Team", _row(team="Trail Blazers"), "Trail Blazers"),  # T-4 nullable: present
    ("Sex", _row(sex=None), ""),  # T-4 nullable: missing
    ("Sex", _row(sex="M"), "M"),
    ("Sex", _row(sex="F"), "F"),
)


@pytest.mark.parametrize(("label", "row", "expected"), CELL_CASES)
def test_column_value_given_a_row_returns_its_canvas_cell_text(
    label: str,
    row: RiderRow,
    expected: str,
) -> None:
    """A solo row's Team cell reads "solo"; unknown sex is blank."""
    assert _column(label).value(row) == expected


CARD_CELL_CASES = (
    ((), ""),  # T-4 collection boundary: empty
    (("AS",), "A♠"),  # T-4 collection boundary: single
    (("AS", "KH", "TD"), "A♠ K♥ T♦"),  # T-4 collection boundary: many
    (("9H", "KD"), "9♥ K♦"),  # the goal's own example
    (("JK",), "JK★"),  # the joker marker
)


@pytest.mark.parametrize(("cards", "expected"), CARD_CELL_CASES)
def test_cards_column_value_given_a_hand_returns_the_space_joined_glyph_text(
    cards: tuple[str, ...],
    expected: str,
) -> None:
    """The console Cards cell is the dealt codes as glyphs."""
    assert _column("Cards").value(_row(cards=cards)) == expected


_VALID_RANKS = tuple("23456789TJQKA")
_VALID_SUITS = tuple("SHDC")


def _card_code(rank: str, suit: str) -> str:
    """Build one stored card code from a rank and a suit letter."""
    return f"{rank}{suit}"


_VALID_CARD_CODE = st.builds(
    _card_code, st.sampled_from(_VALID_RANKS), st.sampled_from(_VALID_SUITS)
)


@given(cards=st.lists(_VALID_CARD_CODE, min_size=1, max_size=6))
def test_cards_column_value_given_any_hand_preserves_its_card_count(cards: list[str]) -> None:
    """Property (T-7): one display token per dealt card."""
    text = _column("Cards").value(_row(cards=tuple(cards)))

    assert len(text.split(" ")) == len(cards)


# --------------------------------------------------------- sort keys


def test_plate_sort_key_given_mixed_plates_groups_digits_before_strings() -> None:
    """Digits order numerically and first; a relay code sorts after."""
    rows = [_row(plate=plate) for plate in ("10", "K1", "2", "9")]

    assert _plates_in_sort_order("Plate", rows) == ["2", "9", "10", "K1"]


def test_name_sort_key_given_mixed_case_names_orders_case_folded() -> None:
    """Name sort is case-insensitive: "sam" never lands after "Zoe"."""
    rows = [_row(plate="1", name="sam"), _row(plate="2", name="Zoe"), _row(plate="3", name="Alex")]

    assert _plates_in_sort_order("Name", rows) == ["3", "1", "2"]


def test_team_sort_key_given_solos_and_teams_groups_solos_first() -> None:
    """A solo row (team None) sorts before every named team."""
    rows = [
        _row(plate="1", team="Zebras"),
        _row(plate="2", team=None),
        _row(plate="3", team="alpha"),
    ]

    assert _plates_in_sort_order("Team", rows) == ["2", "3", "1"]


def test_team_sort_key_given_case_variant_names_orders_case_folded() -> None:
    """Team sort is case-insensitive, like Name."""
    rows = [_row(plate="1", team="zebras"), _row(plate="2", team="Alpha")]

    assert _plates_in_sort_order("Team", rows) == ["2", "1"]


def test_sex_sort_key_given_m_f_and_unknown_orders_m_then_f_then_blank() -> None:
    """The Sex rule: M, then F, then a blank (unknown) sex last."""
    rows = [_row(plate="1", sex=None), _row(plate="2", sex="F"), _row(plate="3", sex="M")]

    assert _plates_in_sort_order("Sex", rows) == ["3", "2", "1"]


def test_sex_sort_key_given_lower_case_letters_orders_them_like_the_upper_case() -> None:
    """A lower-case sex letter sorts with its canonical one."""
    rows = [_row(plate="1", sex="f"), _row(plate="2", sex="m")]

    assert _plates_in_sort_order("Sex", rows) == ["2", "1"]


def test_sex_sort_key_given_every_rider_unknown_keeps_the_roster_order() -> None:
    """All-unknown is a stable no-op: roster order survives (T-4)."""
    rows = [_row(plate="1"), _row(plate="2"), _row(plate="3")]

    assert _plates_in_sort_order("Sex", rows) == ["1", "2", "3"]


def test_cards_sort_key_given_hands_of_different_lengths_orders_by_joined_text() -> None:
    """Cards sort by their joined text: an empty hand sorts first."""
    rows = [
        _row(plate="1", cards=("KH",)),
        _row(plate="2", cards=()),
        _row(plate="3", cards=("AS",)),
    ]

    assert _plates_in_sort_order("Cards", rows) == ["2", "3", "1"]


def test_cards_sort_key_given_a_shared_first_card_orders_by_the_rest() -> None:
    """The joined text compares card by card, not by hand length."""
    rows = [
        _row(plate="1", cards=("AS", "KH")),
        _row(plate="2", cards=("AS", "AH")),
    ]

    assert _plates_in_sort_order("Cards", rows) == ["2", "1"]


# -------------------------------------------------- click-to-sort rule


def test_toggle_sort_given_a_new_column_sorts_it_ascending() -> None:
    """Clicking a column other than the active one always sorts up."""
    assert toggle_sort(2, column=0, ascending=False) == (2, True)


def test_toggle_sort_given_the_active_column_ascending_reverses_it() -> None:
    """Re-clicking the active column flips it to descending."""
    assert toggle_sort(0, column=0, ascending=True) == (0, False)


def test_toggle_sort_given_the_active_column_descending_restores_ascending() -> None:
    """A third click flips back up again."""
    assert toggle_sort(0, column=0, ascending=False) == (0, True)


def test_toggle_sort_given_no_active_column_sorts_the_clicked_one_ascending() -> None:
    """The first click after a clear (no active column) sorts up."""
    assert toggle_sort(3, column=None, ascending=True) == (3, True)


@given(column=st.integers(min_value=0, max_value=9), ascending=st.booleans())
def test_toggle_sort_given_two_clicks_on_one_column_restores_its_direction(
    column: int,
    ascending: bool,  # noqa: FBT001 -- a generated property value
) -> None:
    """Property: a re-click is an involution -- twice restores (T-7)."""
    once = toggle_sort(column, column=column, ascending=ascending)

    assert toggle_sort(column, column=once[0], ascending=once[1]) == (column, ascending)


@given(
    plates=st.lists(
        st.text(alphabet=string.digits, min_size=1, max_size=6),
        min_size=1,
        max_size=8,
        unique=True,
    )
)
def test_plate_sort_key_given_digit_plates_matches_integer_order(plates: list[str]) -> None:
    """Property: the numeric-aware key is monotonic in value (T-7)."""
    rows = [_row(plate=plate) for plate in plates]

    assert _plates_in_sort_order("Plate", rows) == sorted(plates, key=int)


@given(name=st.text(max_size=20))
def test_name_sort_key_given_any_name_is_already_case_folded(name: str) -> None:
    """Property: the Name key is idempotent under casefold (T-7)."""
    key = _column("Name").sort_key(_row(name=name))

    assert key == key.casefold()


_SAFE_CARD = st.text(alphabet=string.ascii_uppercase + string.digits, min_size=1, max_size=3)


@given(cards=st.lists(_SAFE_CARD, max_size=5))
def test_cards_sort_key_given_any_hand_preserves_its_card_count(cards: list[str]) -> None:
    """Property: one token per card, empty hand reading blank (T-7)."""
    key = _column("Cards").sort_key(_row(cards=tuple(cards)))

    assert key.split(" ") == (cards or [""])


def test_column_sort_key_given_every_shared_column_is_callable() -> None:
    """Every column carries a sort key, so every header is clickable."""
    assert all(callable(column.sort_key) for column in CONSOLE_RIDER_COLUMNS)


def test_column_value_given_every_shared_column_is_callable() -> None:
    """Every column carries an accessor the list model renders via."""
    accessors: list[Callable[[RiderRow], str]] = [column.value for column in CONSOLE_RIDER_COLUMNS]

    assert all(callable(accessor) for accessor in accessors)


def test_solo_team_text_given_the_shared_constant_is_the_word_solo() -> None:
    """The one shared spelling of a solo rider's Team cell."""
    assert SOLO_TEAM_TEXT == "solo"
