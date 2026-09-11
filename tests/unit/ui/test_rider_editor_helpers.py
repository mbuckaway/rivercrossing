# SPDX-License-Identifier: GPL-3.0-only
"""Headless W7/Phase-3 pins for views/rider_editor's pure helpers.

The W7 rider-editor rework changes two pure facts before any window
opens: the canvas minimum becomes 1280x560 with both dimensions
applied, and a solo rider's Team cell reads the word "solo" instead
of the em dash (the user copy: 'the team in the list must be
"solo"'). Phase 3 adds the shared row model and the sort indicator:
both are pure Python over the objects they are handed (a
``RiderRow`` list, a ``wx.dataview.DataViewColumn``-shaped control),
so both are pinned here rather than through a real window. Real-window
geometry and click-through behaviour stay functional
(``test_rider_editor.py``, ``test_lists_demo.py``).
"""

import pytest

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.rider_columns import CONSOLE_RIDER_COLUMNS, EDITOR_RIDER_COLUMNS
from rivercrossing.ui.views import rider_editor
from rivercrossing.ui.views._support import RiderRowListModel, apply_sort_indicator


def _team_cell(row: RiderRow) -> str:
    """Return *row*'s Team cell through the shared column accessor."""
    return EDITOR_RIDER_COLUMNS[rider_editor.COL_TEAM].value(row)


def test_editor_team_column_given_a_solo_row_renders_the_word_solo() -> None:
    """A solo rider's Team cell is the literal word "solo" (W7)."""
    assert _team_cell(RiderRow(plate="123", name="Sam Ellis", team=None)) == "solo"


def test_editor_team_column_given_a_team_row_renders_the_team_display_name() -> None:
    """A team rider keeps their team's display name as the cell."""
    assert _team_cell(RiderRow(plate="77", name="A. Roy", team="Trail Blazers")) == "Trail Blazers"


def test_rider_editor_min_size_is_1280_by_560() -> None:
    """W7 rework: the canvas redraws the editor at 1280x560."""
    assert rider_editor.MIN_SIZE == (1280, 560)


# ------------------------------------------------- the editor's columns


def test_editor_column_labels_given_the_sex_column_are_plate_name_team_sex() -> None:
    """Phase 3: riders_list draws a Sex column after Team."""
    assert rider_editor.COLUMN_LABELS == ("Plate", "Name", "Team", "Sex")


def test_editor_column_labels_given_the_shared_definitions_cannot_drift() -> None:
    """The editor's headers are the shared labels, in order."""
    labels = tuple(column.label for column in EDITOR_RIDER_COLUMNS)

    assert labels == rider_editor.COLUMN_LABELS


def test_editor_column_indexes_given_the_sex_column_keep_team_second_last() -> None:
    """The frozen column indexes: Plate 0, Name 1, Team 2, Sex 3."""
    assert (rider_editor.COL_PLATE, rider_editor.COL_NAME, rider_editor.COL_TEAM) == (0, 1, 2)
    assert rider_editor.COL_SEX == 3


# ------------------------------------------------------- the row model


_ROW = RiderRow(plate="123", name="Sam Ellis", team=None, sex="F", cards=("AS", "KH"))


def test_rider_row_list_model_given_editor_columns_exposes_four() -> None:
    """rider_editor_dlg's list has one column per shared definition."""
    model = RiderRowListModel([], EDITOR_RIDER_COLUMNS)

    assert model.GetColumnCount() == 4


def test_rider_row_list_model_given_console_columns_exposes_five() -> None:
    """The console's list adds the Cards column."""
    model = RiderRowListModel([], CONSOLE_RIDER_COLUMNS)

    assert model.GetColumnCount() == 5


def test_rider_row_list_model_given_no_rows_reports_zero_rows() -> None:
    """T-4 boundary: an empty roster yields an empty model."""
    model = RiderRowListModel([], EDITOR_RIDER_COLUMNS)

    assert model.GetCount() == 0


def test_rider_row_list_model_given_one_row_reports_one_row() -> None:
    """T-4 boundary: the single-row case."""
    model = RiderRowListModel([_ROW], EDITOR_RIDER_COLUMNS)

    assert model.GetCount() == 1


def test_rider_row_list_model_given_many_rows_reports_them_all() -> None:
    """T-4 boundary: many rows all reach the control."""
    model = RiderRowListModel([_ROW, _ROW, _ROW], EDITOR_RIDER_COLUMNS)

    assert model.GetCount() == 3


def test_rider_row_list_model_given_any_column_reports_the_string_type() -> None:
    """Every rider-list column is plain text (no bitmaps here)."""
    model = RiderRowListModel([_ROW], EDITOR_RIDER_COLUMNS)

    assert model.GetColumnType(0) == "string"


# (column, expected cell) pairs: one row through every console column.
CONSOLE_CELL_CASES = (
    (0, "123"),
    (1, "Sam Ellis"),
    (2, "solo"),
    (3, "F"),
    (4, "A♠ K♥"),
)


@pytest.mark.parametrize(("column", "expected"), CONSOLE_CELL_CASES)
def test_rider_row_list_model_given_a_row_renders_each_shared_cell(
    column: int,
    expected: str,
) -> None:
    """The model renders each cell through its column accessor."""
    model = RiderRowListModel([_ROW], CONSOLE_RIDER_COLUMNS)

    assert model.GetValueByRow(0, column) == expected


# ------------------------------------------------- the Sex dropdown


def test_sex_options_given_the_dialog_choice_are_blank_then_m_then_f() -> None:
    """The dropdown's items: blank (unset) first, then M, F."""
    assert rider_editor.SEX_OPTIONS == ("", "M", "F")


def test_sex_choice_given_the_frozen_name_matches_the_xrc_object() -> None:
    """The generated id the view resolves matches the .xrc name."""
    assert ids.SEX_CHOICE == "sex_choice"


SEX_CHOICE_CASES = (
    ("M", "M"),
    ("F", "F"),
    ("", None),  # T-4 nullable: present-but-empty (the blank item)
)


@pytest.mark.parametrize(("selection", "expected"), SEX_CHOICE_CASES)
def test_sex_from_choice_given_a_dropdown_value_returns_the_rider_sex(
    selection: str,
    expected: str | None,
) -> None:
    """The blank item means unknown (None), never an empty string."""
    assert rider_editor.sex_from_choice(selection) == expected


def test_sex_from_choice_given_every_dialog_option_maps_to_a_valid_rider_sex() -> None:
    """Every item the dialog installs is a legal ``Rider.sex`` value."""
    mapped = [rider_editor.sex_from_choice(option) for option in rider_editor.SEX_OPTIONS]

    assert mapped == [None, "M", "F"]


# ------------------------------------------------ the sort indicator


class _FakeColumn:
    """A ``wx.dataview.DataViewColumn`` stand-in without wx."""

    def __init__(self, title: str) -> None:
        """Start with *title* as the header text."""
        self.title = title

    def GetTitle(self) -> str:  # noqa: N802 -- wx API name the stub mirrors
        """Return the current header text."""
        return self.title

    def SetTitle(self, title: str) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """Replace the header text."""
        self.title = title


def _columns(*titles: str) -> list[_FakeColumn]:
    """Return one fake column per *titles* entry, in order."""
    return [_FakeColumn(title) for title in titles]


def test_apply_sort_indicator_given_an_ascending_column_marks_it_up() -> None:
    """The active column's header gains the ▲ marker."""
    columns = _columns("Plate", "Name", "Team", "Sex")

    apply_sort_indicator(columns, 1, ascending=True)

    assert [column.GetTitle() for column in columns] == ["Plate", "Name ▲", "Team", "Sex"]


def test_apply_sort_indicator_given_a_descending_column_marks_it_down() -> None:
    """A descending sort marks the active column ▼."""
    columns = _columns("Plate", "Name")

    apply_sort_indicator(columns, 1, ascending=False)

    assert [column.GetTitle() for column in columns] == ["Plate", "Name ▼"]


def test_apply_sort_indicator_given_no_active_column_leaves_plain_labels() -> None:
    """No active column means no marker anywhere."""
    columns = _columns("Plate", "Name")

    apply_sort_indicator(columns, None, ascending=True)

    assert [column.GetTitle() for column in columns] == ["Plate", "Name"]


def test_apply_sort_indicator_given_an_already_marked_column_replaces_the_marker() -> None:
    """T-3/T-8: re-marking replaces the marker, never stacking two."""
    columns = _columns("Plate ▲", "Name")

    apply_sort_indicator(columns, 0, ascending=False)

    assert [column.GetTitle() for column in columns] == ["Plate ▼", "Name"]


def test_apply_sort_indicator_given_previously_marked_columns_restores_their_labels() -> None:
    """Clearing the sort is idempotent: the markers come off both."""
    columns = _columns("Plate ▲", "Name ▼")

    apply_sort_indicator(columns, None, ascending=True)

    assert [column.GetTitle() for column in columns] == ["Plate", "Name"]


def test_apply_sort_indicator_given_a_moved_active_column_moves_the_marker() -> None:
    """Only the newly active column carries a marker."""
    columns = _columns("Plate ▲", "Name")

    apply_sort_indicator(columns, 1, ascending=True)

    assert [column.GetTitle() for column in columns] == ["Plate", "Name ▲"]


def test_apply_sort_indicator_given_no_columns_leaves_nothing_to_mark() -> None:
    """T-4 boundary: an empty column list is fine (no control yet)."""
    columns: list[_FakeColumn] = []

    apply_sort_indicator(columns, 0, ascending=True)

    assert columns == []
