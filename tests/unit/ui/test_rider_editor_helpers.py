# SPDX-License-Identifier: GPL-3.0-only
"""Headless W7/Phase-3 pins for views/rider_editor's pure helpers.

The W7 rider-editor rework changes two pure facts before any window
opens: the canvas minimum becomes 1280x560 with both dimensions
applied, and a solo rider's Team cell reads the word "solo" instead
of the em dash (the user copy: 'the team in the list must be
"solo"'). Phase 3 adds the shared row model; the rider list now sorts
the way the team editor's does -- natively, through
:meth:`RiderRowListModel.Compare` -- so the model's comparator and the
view's two sort handlers are pinned here as pure objects (a
``RiderRow`` list, a ``wx.dataview.DataViewCtrl``-shaped double)
rather than through a real window. Real-window geometry and
click-through behaviour stay functional (``test_rider_editor.py``,
``test_lists_demo.py``).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.rider_columns import CONSOLE_RIDER_COLUMNS, EDITOR_RIDER_COLUMNS
from rivercrossing.ui.views import rider_editor
from rivercrossing.ui.views._support import RiderRowListModel


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


# ------------------------------------------------- the native sort


def _compare_model(rows: list[RiderRow]) -> RiderRowListModel:
    """Wrap *rows* in every shared column (Cards included)."""
    return RiderRowListModel(rows, CONSOLE_RIDER_COLUMNS)


def test_rider_row_list_model_compare_given_one_row_ties_with_itself() -> None:
    """The single-row boundary: a lone row ties with itself (T-4)."""
    model = _compare_model([RiderRow(plate="1", name="Only")])

    assert model.Compare(model.GetItem(0), model.GetItem(0), 1, True) == 0  # noqa: FBT003


def test_rider_row_list_model_compare_plate_sorts_numerically_not_as_text() -> None:
    """Plate compares its shared numeric-aware key: 2 before 10."""
    model = _compare_model(
        [
            RiderRow(plate="2", name="Two"),
            RiderRow(plate="10", name="Ten"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 0, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_plate_sorts_text_after_every_digit() -> None:
    """A relay code sorts after every digit plate (the shared key)."""
    model = _compare_model(
        [
            RiderRow(plate="10", name="Ten"),
            RiderRow(plate="K1", name="Relay"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 0, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_name_sorts_case_folded() -> None:
    """Name compares case-folded: "alpha" sorts before "Beta"."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="alpha"),
            RiderRow(plate="2", name="Beta"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 1, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_team_sorts_solos_first() -> None:
    """A solo rider (no team) sorts before every named team."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="Solo", team=None),
            RiderRow(plate="2", name="Team", team="Alpha"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 2, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_sex_sorts_m_before_f() -> None:
    """The shared Sex rule: M, then F, then blank last."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="Moe", sex="M"),
            RiderRow(plate="2", name="Fay", sex="F"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 3, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_cards_sorts_an_empty_hand_first() -> None:
    """Cards compare the joined card codes; no cards sorts first."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="No cards", cards=()),
            RiderRow(plate="2", name="One card", cards=("AS",)),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 4, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_descending_reverses_the_plate_order() -> None:
    """The header arrow's descending direction inverts the compare."""
    model = _compare_model(
        [
            RiderRow(plate="2", name="Two"),
            RiderRow(plate="10", name="Ten"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 0, False) > 0  # noqa: FBT003


def test_rider_row_list_model_compare_given_equal_keys_keeps_the_roster_order() -> None:
    """Tie-break (ascending): equal keys keep the roster's own order.

    wx's control-side sort is not stable (unlike the presenter's former
    ``sorted``), so without a tie-break two rows with the same Name
    would reorder freely between sorts.
    """
    model = _compare_model(
        [
            RiderRow(plate="1", name="Same"),
            RiderRow(plate="2", name="Same"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 1, True) < 0  # noqa: FBT003


def test_rider_row_list_model_compare_given_equal_keys_descending_keeps_the_roster_order() -> None:
    """Tie-break (descending): equal keys keep the roster order."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="Same"),
            RiderRow(plate="2", name="Same"),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 1, False) < 0  # noqa: FBT003


@given(name_a=st.text(), name_b=st.text())
def test_rider_row_list_model_compare_name_is_antisymmetric(name_a: str, name_b: str) -> None:
    """Property (T-7): swapping the two items negates the compare.

    The invariant wx's own sort relies on, whatever the two names --
    including the equal-name case, where the tie-break is the row
    index and is therefore antisymmetric too.
    """
    model = _compare_model(
        [
            RiderRow(plate="1", name=name_a),
            RiderRow(plate="2", name=name_b),
        ]
    )

    forward = model.Compare(model.GetItem(0), model.GetItem(1), 1, True)  # noqa: FBT003
    backward = model.Compare(model.GetItem(1), model.GetItem(0), 1, True)  # noqa: FBT003

    assert forward == -backward


@given(name=st.text(), ascending=st.booleans())
def test_rider_row_list_model_compare_given_equal_keys_ties_by_roster_order(
    name: str,
    ascending: bool,  # noqa: FBT001 -- a generated property value
) -> None:
    """Property (T-7): equal keys always tie-break to the roster order.

    Whatever the shared name and whichever way the arrow points, the
    earlier row compares first -- so wx's non-stable control sort
    cannot shuffle two rows whose cells are equal.
    """
    model = _compare_model(
        [
            RiderRow(plate="1", name=name),
            RiderRow(plate="2", name=name),
        ]
    )

    assert model.Compare(model.GetItem(0), model.GetItem(1), 1, ascending) < 0


@pytest.mark.parametrize("column", range(len(CONSOLE_RIDER_COLUMNS)))
def test_rider_row_list_model_compare_given_each_column_answers_an_int(column: int) -> None:
    """Every shared column compares, so every header is clickable."""
    model = _compare_model(
        [
            RiderRow(plate="1", name="A", team=None, sex=None, cards=()),
            RiderRow(plate="2", name="B", team="T", sex="M", cards=("AS",)),
        ]
    )

    assert isinstance(model.Compare(model.GetItem(0), model.GetItem(1), column, True), int)  # noqa: FBT003


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


# -------------------------------------------- the view sort handlers


class _SortColumn:
    """A ``wx.dataview.DataViewColumn`` double for the native sort."""

    def __init__(self, model_column: int, *, ascending: bool = True) -> None:
        """Carry *model_column* and the arrow's own direction."""
        self.model_column = model_column
        self.ascending = ascending
        self.sort_orders: list[bool] = []

    def GetModelColumn(self) -> int:  # noqa: N802 -- wx API name the double mirrors
        """Return the model column this header sorts."""
        return self.model_column

    def IsSortOrderAscending(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return the header arrow's direction."""
        return self.ascending

    def SetSortOrder(self, ascending: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the direction the view re-applied."""
        self.sort_orders.append(ascending)


class _SortEvent:
    """A ``wx.dataview.DataViewEvent`` double for the sorted event."""

    def __init__(self) -> None:
        """Start before the handler ran."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record that the handler let the event continue."""
        self.skipped = True


class _SortModel:
    """A ``RiderRowListModel`` double recording its resorts."""

    def __init__(self) -> None:
        """Start with no resort requested."""
        self.resorts = 0

    def Resort(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one resort request."""
        self.resorts += 1


class _SortControl:
    """A ``wx.dataview.DataViewCtrl`` double for the sort handlers."""

    def __init__(self, *, sorting: _SortColumn | None = None) -> None:
        """Carry the control's current sorting column, if any."""
        self.sorting = sorting
        self.columns: dict[int, _SortColumn] = {}
        self.model: object | None = None

    def GetSortingColumn(self) -> _SortColumn | None:  # noqa: N802 -- wx API name
        """Return the column the control currently sorts by."""
        return self.sorting

    def GetColumn(self, index: int) -> _SortColumn | None:  # noqa: N802 -- wx API name
        """Return the column at *index*, or ``None``."""
        return self.columns.get(index)

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model

    def Refresh(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""

    def Update(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""


class _Infobar:
    """A ``wx.InfoBar`` double the rows render dismisses."""

    def Dismiss(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op dismiss (nothing shown)."""


class _EditorShell:
    """A ``RiderEditor`` double owning only what the handlers read."""

    def __init__(
        self,
        *,
        control: _SortControl | None = None,
        sort_column: int | None = None,
        sort_ascending: bool = True,
    ) -> None:
        """Store the state the sort methods under test read."""
        self.riders_list = control if control is not None else _SortControl()
        self.roster_infobar = _Infobar()
        self._model: object = _SortModel()
        self._sort_column = sort_column
        self._sort_ascending = sort_ascending

    def _apply_sort(self) -> None:
        """Run the real handler so the show_riders wiring runs."""
        rider_editor.RiderEditor._apply_sort(self)


def test_rider_editor_on_column_sorted_given_a_column_remembers_its_state() -> None:
    """A header sort is remembered as its model column + direction."""
    control = _SortControl(sorting=_SortColumn(rider_editor.COL_SEX, ascending=False))
    shell = _EditorShell(control=control)
    event = _SortEvent()

    rider_editor.RiderEditor._on_column_sorted(shell, event)

    assert (shell._sort_column, shell._sort_ascending) == (rider_editor.COL_SEX, False)
    assert event.skipped is True


def test_rider_editor_on_column_sorted_given_no_sorting_column_keeps_the_state() -> None:
    """T-3 negative: wx's "nothing sorted" state changes nothing."""
    control = _SortControl(sorting=None)
    shell = _EditorShell(control=control, sort_column=rider_editor.COL_TEAM, sort_ascending=False)

    rider_editor.RiderEditor._on_column_sorted(shell, _SortEvent())

    assert (shell._sort_column, shell._sort_ascending) == (rider_editor.COL_TEAM, False)


def test_rider_editor_apply_sort_given_a_remembered_column_restores_it_and_resorts() -> None:
    """The remembered column's arrow and order are re-applied."""
    column = _SortColumn(rider_editor.COL_NAME)
    control = _SortControl()
    control.columns[rider_editor.COL_NAME] = column
    shell = _EditorShell(control=control, sort_column=rider_editor.COL_NAME, sort_ascending=False)
    shell._model = _SortModel()

    rider_editor.RiderEditor._apply_sort(shell)

    assert (column.sort_orders, shell._model.resorts) == ([False], 1)


def test_rider_editor_apply_sort_given_no_remembered_column_leaves_the_order_alone() -> None:
    """T-3 negative: nothing clicked means the roster's own order."""
    column = _SortColumn(rider_editor.COL_NAME)
    control = _SortControl()
    control.columns[rider_editor.COL_NAME] = column
    shell = _EditorShell(control=control)
    shell._model = _SortModel()

    rider_editor.RiderEditor._apply_sort(shell)

    assert (column.sort_orders, shell._model.resorts) == ([], 0)


def test_rider_editor_apply_sort_given_a_missing_column_leaves_the_model_alone() -> None:
    """T-3 negative: a remembered column the control no longer has."""
    shell = _EditorShell(sort_column=rider_editor.COL_NAME)
    shell._model = _SortModel()

    rider_editor.RiderEditor._apply_sort(shell)

    assert shell._model.resorts == 0


def test_rider_editor_show_riders_given_a_remembered_sort_re_applies_it() -> None:
    """A rebuilt model drops the sort key; the view restores it."""
    column = _SortColumn(rider_editor.COL_NAME)
    control = _SortControl()
    control.columns[rider_editor.COL_NAME] = column
    shell = _EditorShell(control=control, sort_column=rider_editor.COL_NAME)

    rider_editor.RiderEditor.show_riders(shell, [_ROW])

    assert column.sort_orders == [True]
    assert shell._model.GetCount() == 1


def test_rider_editor_show_riders_given_no_sort_leaves_every_column_alone() -> None:
    """T-3 negative: the list opens in the presenter's own order."""
    column = _SortColumn(rider_editor.COL_NAME)
    control = _SortControl()
    control.columns[rider_editor.COL_NAME] = column
    shell = _EditorShell(control=control)

    rider_editor.RiderEditor.show_riders(shell, [_ROW])

    assert column.sort_orders == []
    assert shell._model.GetCount() == 1
