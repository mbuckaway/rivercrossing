# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``results_dlg``'s code-side behaviour (Part C/D).

Only what genuinely needs no window is pinned here, in the
``test_main_frame_riders_list.py`` / ``test_rider_list_columns.py``
style:

- :data:`STANDINGS_COLUMN_FLAGS` and the shared eight-column list, with
  Best lap directly after Total (Phase 5, Part 3);
- :meth:`StandingsListModel.Compare` -- the native header sort's
  per-column keys, its numeric Total/Best lap (seconds, not display
  text) and its non-negated row-position tie-break -- driven against a
  shell that owns only ``_rows``/``GetRow``;
- :meth:`ResultsWindow._build_columns`, for the Plate column the Team
  list drops under ``RIDER_POOLED`` (Part 2) and the two time columns
  ``show_times_chk`` gates (Part 3);
- :meth:`ResultsWindow.show_standings`, driven against a fake dialog
  and fake controls, for both the MIXED notebook and the SOLO
  standalone list;
- :meth:`ResultsWindow._on_standings_activated` -- the ⚠ badge's
  double-click explanation (Part 1) -- against a fake ``DataViewEvent``
  and a recording ``wx.MessageDialog`` double (the ``test_std_dialogs``
  pattern: importing wx is safe without a display, opening a modal is
  not);
- the Part D export-button gate, driven against a fake ride status and
  fake buttons (no real button is created).

The live layout -- real columns on real controls -- stays with the
(functional) suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.presenters.data_source import StandingsRow
from rivercrossing.ui.views import results_win
from rivercrossing.ui.views.results_win import (
    COL_BEST5,
    COL_BESTLAP,
    COL_ENTRY,
    COL_HAND,
    COL_LAPS,
    COL_PLACE,
    COL_PLATE,
    COL_TOTAL,
    COLUMN_LABELS,
    DRAW_EXPLANATION,
    DRAW_INFO_TITLE,
    STANDINGS_COLUMN_FLAGS,
    TIME_COLUMNS,
    ResultsWindow,
    StandingsListModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _row(  # noqa: PLR0913 -- a fixture builder mirroring StandingsRow's fields
    *,
    place: int = 1,
    plate: str = "1",
    entry: str = "A Racer",
    laps: int = 1,
    total: str = "0:01:00",
    total_seconds: float = 60.0,
    best_lap: str = "0:00:20",
    best_lap_seconds: float = 20.0,
    best5: Sequence[str] = ("2C",),
    hand: str = "High Card — Ace",
    draw_required: bool = False,
    tie_note: str | None = None,
) -> StandingsRow:
    """Build one standings row varying only what a test needs."""
    return StandingsRow(
        place=place,
        plate=plate,
        entry=entry,
        laps=laps,
        total=total,
        best5=tuple(best5),
        hand=hand,
        draw_required=draw_required,
        total_seconds=total_seconds,
        tie_note=tie_note,
        best_lap=best_lap,
        best_lap_seconds=best_lap_seconds,
    )


# ------------------------------------------------------ the columns


def test_standings_column_flags_given_the_shared_list_keep_it_resizable() -> None:
    """The explicit flags retain the resizable bit wx drops."""
    flags = STANDINGS_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_RESIZABLE) == wx.dataview.DATAVIEW_COL_RESIZABLE


def test_standings_column_flags_given_the_shared_list_keep_it_sortable() -> None:
    """Native header sorting is what the flags are for."""
    flags = STANDINGS_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_SORTABLE) == wx.dataview.DATAVIEW_COL_SORTABLE


class _Column:
    """A DataViewColumn double recording its hidden flag."""

    def __init__(self) -> None:
        """Start with no hidden flag applied."""
        self.hidden: bool | None = None

    def SetHidden(self, hidden: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record the hidden flag."""
        self.hidden = hidden


class _ListControl:
    """A DataViewCtrl double for the code-side columns and bindings."""

    def __init__(self) -> None:
        """Start with no columns and no bindings."""
        self.columns: list[tuple[str, int, int, _Column]] = []
        self.bindings: list[tuple[object, object]] = []

    def AppendTextColumn(  # noqa: N802 -- wx API name the double mirrors
        self, label: str, col: int, *, flags: int
    ) -> _Column:
        """Record the column and return its double."""
        column = _Column()
        self.columns.append((label, col, flags, column))
        return column

    def Bind(self, event: object, handler: object) -> None:  # noqa: N802 -- wx API name
        """Record one (event, handler) binding."""
        self.bindings.append((event, handler))


class _BindingDialog:
    """A ``wx.Dialog`` double recording each bind it is given."""

    def __init__(self) -> None:
        """Start with no bindings."""
        self.bindings: list[tuple[object, object, object]] = []

    def Bind(  # noqa: N802 -- wx API name the double mirrors
        self, event: object, handler: object, source: object = None
    ) -> None:
        """Record one binding, source included."""
        self.bindings.append((event, handler, source))


class _BindShell:
    """A ResultsWindow shell owning only what _bind_events reaches."""

    def __init__(self) -> None:
        """Build the dialog, the five checkboxes and the three lists."""
        self.dialog = _BindingDialog()
        self.show_times_chk = _CheckBox()
        self.laps_board_chk = _CheckBox()
        self.time_board_chk = _CheckBox()
        self.full_field_chk = _CheckBox()
        self.all_cards_chk = _CheckBox()
        self.lists = (_ListControl(), _ListControl(), _ListControl())
        self.standings_list, self.teams_standings_list, self.solo_standings_list = self.lists

    def _on_publish_toggle(self, event: object) -> None:
        """Delegate the toggle to the real view handler."""
        ResultsWindow._on_publish_toggle(self, event)

    def _on_standings_activated(self, event: object) -> None:
        """Delegate the activation to the real view handler."""
        ResultsWindow._on_standings_activated(self, event)


def test_build_columns_for_given_a_control_appends_the_eight_canvas_columns() -> None:
    """One column per COLUMN_LABELS entry, in order."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [(label, col) for label, col, _flags, _column in control.columns] == [
        (label, col) for col, label in enumerate(COLUMN_LABELS)
    ]


def test_build_columns_for_given_a_control_carries_the_shared_column_flags() -> None:
    """Every column is natively sortable and resizable."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [flags for _label, _col, flags, _column in control.columns] == (
        [STANDINGS_COLUMN_FLAGS] * len(COLUMN_LABELS)
    )


def test_build_columns_for_given_a_control_returns_its_total_and_best_lap_columns() -> None:
    """Return the two time columns show_times_chk gates (Part 3)."""
    control = _ListControl()

    columns = ResultsWindow._build_columns_for(control)

    assert columns == (control.columns[COL_TOTAL][3], control.columns[COL_BESTLAP][3])


def test_build_columns_for_given_no_hide_plate_leaves_every_column_visible() -> None:
    """Part 2's default: Table's Plate column is appended and shown."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [column.hidden for _label, _col, _flags, column in control.columns] == [None] * len(
        COLUMN_LABELS
    )


def test_build_columns_for_given_hide_plate_hides_only_the_plate_column() -> None:
    """Part 2: the Team list's Plate column is the one that goes."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control, hide_plate=True)

    assert [column.hidden for _label, _col, _flags, column in control.columns] == [
        True if col == COL_PLATE else None for col in range(len(COLUMN_LABELS))
    ]


# ----------------------------------- the shared header (Parts 2 and 3)


class _ColumnsShell:
    """A ResultsWindow shell owning the lists and a plate model."""

    def __init__(self, plate_model: PlateModel) -> None:
        """Build one column-recording list double per standings list."""
        self.plate_model = plate_model
        self.standings_list = _ListControl()
        self.teams_standings_list = _ListControl()
        self.solo_standings_list = _ListControl()

    def _build_columns_for(
        self, control: object, *, hide_plate: bool = False
    ) -> tuple[object, ...]:
        """Delegate one list's columns to the real view method."""
        return ResultsWindow._build_columns_for(control, hide_plate=hide_plate)


def test_column_labels_place_best_lap_directly_after_total() -> None:
    """Part 3: the header reads Place..Total, Best lap, Best 5, Hand."""
    assert COLUMN_LABELS == (
        "Place",
        "Plate",
        "Entry",
        "Laps",
        "Total",
        "Best lap",
        "Best 5",
        "Hand",
    )
    assert COL_BESTLAP == COL_TOTAL + 1


def test_time_columns_hold_the_total_and_best_lap_indexes() -> None:
    """Both time columns are gated together by show_times_chk (R-63)."""
    assert TIME_COLUMNS == (COL_TOTAL, COL_BESTLAP)


def test_build_columns_returns_the_two_time_columns_of_every_list() -> None:
    """Part 3: each list's Total and Best lap columns, list by list."""
    shell = _ColumnsShell(PlateModel.TEAM_RELAY)

    columns = ResultsWindow._build_columns(shell)

    assert columns == tuple(
        control.columns[col][3]
        for control in (
            shell.standings_list,
            shell.teams_standings_list,
            shell.solo_standings_list,
        )
        for col in TIME_COLUMNS
    )


def test_build_columns_given_rider_pooled_hides_the_plate_column_on_the_team_list() -> None:
    """Part 2: a pooled team's plate repeats a member's -- it goes."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert shell.teams_standings_list.columns[COL_PLATE][3].hidden is True


def test_build_columns_given_rider_pooled_leaves_the_other_plate_columns_untouched() -> None:
    """Part 2: the standalone and Solo lists keep their Plate column."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert [
        shell.standings_list.columns[COL_PLATE][3].hidden,
        shell.solo_standings_list.columns[COL_PLATE][3].hidden,
    ] == [None, None]


def test_build_columns_given_team_relay_keeps_every_plate_column() -> None:
    """Part 2: relay plates identify the entry, so nothing is hidden."""
    shell = _ColumnsShell(PlateModel.TEAM_RELAY)

    ResultsWindow._build_columns(shell)

    assert [
        control.columns[COL_PLATE][3].hidden
        for control in (
            shell.standings_list,
            shell.teams_standings_list,
            shell.solo_standings_list,
        )
    ] == [None, None, None]


# ------------------------------------------------------------- Compare


class _CompareShell:
    """A ``StandingsListModel`` shell owning only ``_rows`` and GetRow.

    ``Compare`` reads ``self.GetRow(item)`` and ``self._rows``; driving
    it against this shell keeps the sort keys under test without
    building a wx model or a window.
    """

    def __init__(self, rows: Sequence[StandingsRow]) -> None:
        """Store the rows the comparison reads."""
        self._rows = tuple(rows)

    def GetRow(self, item: int) -> int:  # noqa: N802 -- mirrors the wx base method
        """Return the item unchanged (items are row indexes here)."""
        return item


# wxNOT_FOUND, measured on this build: ``GetRow`` answers an item it
# cannot resolve with this unsigned -1, which no index comparison
# matches (``4294967295 == -1`` is False), so ``standing_at``'s range
# guard is what turns it into "no row".
NOT_FOUND_ROW = 0xFFFFFFFF

STANDING_AT_CASES = ((0, "1"), (1, "2"), (2, None), (-1, None), (NOT_FOUND_ROW, None))
STANDING_AT_IDS = ("first_row", "last_row", "past_last_row", "negative_row", "wx_not_found")


@pytest.mark.parametrize(("row", "expected_plate"), STANDING_AT_CASES, ids=STANDING_AT_IDS)
def test_standing_at_given_a_row_index_returns_that_row_or_none(
    row: int, expected_plate: str | None
) -> None:
    """T-4: only a real row index answers a row; every other is None."""
    model = StandingsListModel([_row(plate="1"), _row(plate="2", place=2)])

    standing = model.standing_at(row)

    assert (standing.plate if standing is not None else None) == expected_plate


# Column, a lower row, a higher row: each pair differs only in the
# column under test, and the higher row must order after the lower one
# under that column's documented key (T-4 per-column coverage).
ORDER_CASES = (
    (COL_PLACE, _row(place=1), _row(place=2)),
    (COL_PLATE, _row(plate="2"), _row(plate="9")),
    (COL_ENTRY, _row(entry="A Racer"), _row(entry="B Racer")),
    (COL_LAPS, _row(laps=1), _row(laps=2)),
    (COL_TOTAL, _row(total_seconds=59.0), _row(total_seconds=3600.0)),
    (COL_BESTLAP, _row(best_lap_seconds=59.0), _row(best_lap_seconds=3600.0)),
    (COL_BEST5, _row(best5=("2C",)), _row(best5=("AC",))),
    (COL_HAND, _row(hand="High Card — Ace"), _row(hand="Pair of twos")),
)
ORDER_CASE_IDS = [COLUMN_LABELS[col].replace(" ", "_") for col, _low, _high in ORDER_CASES]

ASCENDING_CASES = ((True, -1), (False, 1))
ASCENDING_IDS = ("ascending", "descending")


@pytest.mark.parametrize(("ascending", "expected"), ASCENDING_CASES, ids=ASCENDING_IDS)
@pytest.mark.parametrize(("col", "lower", "higher"), ORDER_CASES, ids=ORDER_CASE_IDS)
def test_standings_compare_given_two_rows_orders_by_the_column_key(  # noqa: PLR0913 -- the two rows, the column and the arrow
    col: int, lower: StandingsRow, higher: StandingsRow, *, ascending: bool, expected: int
) -> None:
    """Each column's key orders the rows; the arrow flips a result."""
    shell = _CompareShell([lower, higher])

    result = StandingsListModel.Compare(shell, 0, 1, col, ascending)

    assert result == expected


@pytest.mark.parametrize("ascending", [True, False], ids=ASCENDING_IDS)
def test_standings_compare_given_equal_keys_keeps_the_row_position_unnegated(
    *, ascending: bool
) -> None:
    """T-3: equal keys fall back to the position, never negated."""
    shell = _CompareShell([_row(place=1), _row(place=1)])

    result = StandingsListModel.Compare(shell, 0, 1, COL_PLACE, ascending)

    assert result == -1


def test_standings_compare_given_reversed_equal_key_rows_orders_by_position() -> None:
    """T-3: the tie-break follows the row index, not the item."""
    shell = _CompareShell([_row(place=1), _row(place=1)])

    result = StandingsListModel.Compare(shell, 1, 0, COL_PLACE, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


def test_standings_compare_given_total_column_orders_by_seconds_not_display_text() -> None:
    """T-5: the numeric key sorts 36000s after 32400s, not the text.

    A string sort of the rendered text would order "10:00:00" before
    "9:00:00", so this pins the numeric ``total_seconds`` key.
    """
    ten_hours = _row(total="10:00:00", total_seconds=36000.0)
    nine_hours = _row(total="9:00:00", total_seconds=32400.0)
    shell = _CompareShell([ten_hours, nine_hours])

    result = StandingsListModel.Compare(shell, 0, 1, COL_TOTAL, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


def test_standings_compare_given_best_lap_column_orders_by_seconds_not_display_text() -> None:
    """Part 3: Best lap sorts on seconds, never its rendered text.

    The rendered "1:40:00" sorts before "59:00" as text but after it as
    a duration, so this pins the numeric ``best_lap_seconds`` key.
    """
    long_lap = _row(best_lap="1:40:00", best_lap_seconds=6000.0)
    short_lap = _row(best_lap="59:00", best_lap_seconds=3540.0)
    shell = _CompareShell([long_lap, short_lap])

    result = StandingsListModel.Compare(shell, 0, 1, COL_BESTLAP, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


# ------------------------------------------------- show_standings


class _Notebook:
    """A ``wx.Notebook`` double recording its visibility."""

    def __init__(self) -> None:
        """Start hidden (the view shows it only for MIXED)."""
        self.visible = False

    def Show(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the show."""
        self.visible = True

    def Hide(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the hide."""
        self.visible = False


class _StandingsControl:
    """A ``wx.dataview.DataViewCtrl`` double for the standings lists."""

    def __init__(self) -> None:
        """Start visible with no associated model."""
        self.visible = True
        self.model: StandingsListModel | None = None

    def AssociateModel(self, model: StandingsListModel) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model

    def Refresh(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""

    def Update(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""

    def Show(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the show."""
        self.visible = True

    def Hide(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the hide."""
        self.visible = False


class _Dialog:
    """A ``wx.Dialog`` double counting its Layout calls."""

    def __init__(self) -> None:
        """Start with no layout requested."""
        self.layouts = 0

    def Layout(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one layout request."""
        self.layouts += 1


class _StandingsShell:
    """A ResultsWindow shell owning only what show_standings reads."""

    def __init__(self, *, entry_mode: EntryMode) -> None:
        """Build the four controls and the standalone model slots."""
        self.entry_mode = entry_mode
        self.standings_list = _StandingsControl()
        self.teams_standings_list = _StandingsControl()
        self.solo_standings_list = _StandingsControl()
        self.results_notebook = _Notebook()
        self.dialog = _Dialog()
        self._model: StandingsListModel | None = None
        self._teams_model: StandingsListModel | None = None
        self._solo_model: StandingsListModel | None = None


def test_show_standings_given_mixed_mode_shows_the_notebook_and_both_models() -> None:
    """MIXED: notebook shown, standalone hidden, one model per page."""
    shell = _StandingsShell(entry_mode=EntryMode.MIXED)
    teams = [_row(plate="77")]
    solo = [_row(plate="123")]

    ResultsWindow.show_standings(shell, teams, solo)

    assert (shell.results_notebook.visible, shell.standings_list.visible) == (True, False)
    assert shell.teams_standings_list.model.GetValueByRow(0, COL_PLATE) == "77"
    assert shell.solo_standings_list.model.GetValueByRow(0, COL_PLATE) == "123"


def test_show_standings_given_mixed_mode_leaves_the_standalone_model_unset() -> None:
    """MIXED never associates a model with the standalone list."""
    shell = _StandingsShell(entry_mode=EntryMode.MIXED)

    ResultsWindow.show_standings(shell, [_row(plate="77")], [_row(plate="123")])

    assert (shell.standings_list.model, shell._model) == (None, None)


def test_show_standings_given_solo_mode_shows_the_standalone_list() -> None:
    """SOLO: notebook hidden, standalone shown with the solo rows."""
    shell = _StandingsShell(entry_mode=EntryMode.SOLO)
    solo = [_row(plate="123"), _row(plate="8", place=2)]

    ResultsWindow.show_standings(shell, [], solo)

    assert (shell.results_notebook.visible, shell.standings_list.visible) == (False, True)
    assert shell.standings_list.model.GetValueByRow(1, COL_PLATE) == "8"


def test_show_standings_given_solo_mode_leaves_the_notebook_models_unset() -> None:
    """SOLO never associates a model with a hidden notebook page."""
    shell = _StandingsShell(entry_mode=EntryMode.SOLO)

    ResultsWindow.show_standings(shell, [_row(plate="77")], [_row(plate="123")])

    assert (shell.teams_standings_list.model, shell.solo_standings_list.model) == (None, None)


@pytest.mark.parametrize("entry_mode", [EntryMode.MIXED, EntryMode.SOLO], ids=["mixed", "solo"])
def test_show_standings_given_each_mode_lays_out_the_dialog(entry_mode: EntryMode) -> None:
    """Both modes re-layout the dialog once after associating rows."""
    shell = _StandingsShell(entry_mode=entry_mode)

    ResultsWindow.show_standings(shell, [_row()], [_row()])

    assert shell.dialog.layouts == 1


def test_show_standings_given_empty_sections_renders_empty_models() -> None:
    """T-4 collection boundary: empty teams and solo are valid rows."""
    shell = _StandingsShell(entry_mode=EntryMode.MIXED)

    ResultsWindow.show_standings(shell, [], [])

    assert shell.teams_standings_list.model.GetCount() == 0
    assert shell.solo_standings_list.model.GetCount() == 0


# -------------------------------------- publish-checkbox gating (R-63)


class _CheckBox:
    """A ``wx.CheckBox`` double recording its tick and enablement."""

    def __init__(self, *, checked: bool = False, enabled: bool = True) -> None:
        """Start at *checked*/*enabled* (the XRC defaults)."""
        self.checked = checked
        self.enabled = enabled

    def GetValue(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return the recorded tick state."""
        return self.checked

    def SetValue(self, value: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record a programmatic tick."""
        self.checked = value

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record the enablement."""
        self.enabled = enabled


class _CheckEvent:
    """A checkbox event double naming the control it fired from."""

    def __init__(self, source: _CheckBox) -> None:
        """Store the control the event fired from."""
        self.source = source
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the event.Skip()."""
        self.skipped = True

    def GetEventObject(self) -> _CheckBox:  # noqa: N802 -- wx API name
        """Return the control the event fired from."""
        return self.source


class _TogglePresenter:
    """A ``ResultsPresenter`` double counting toggle forwards."""

    def __init__(self) -> None:
        """Start with no forwards."""
        self.toggles = 0

    def on_publish_toggled(self) -> None:
        """Record one forward from the view."""
        self.toggles += 1


class _PublishShell:
    """A ResultsWindow shell owning only the publish checkboxes."""

    def __init__(self, *, show_times: bool, time_board: bool) -> None:
        """Build the five boxes, the time columns and the presenter."""
        self.show_times_chk = _CheckBox(checked=show_times)
        self.laps_board_chk = _CheckBox(checked=True)
        self.time_board_chk = _CheckBox(checked=time_board)
        self.full_field_chk = _CheckBox(checked=True)
        self.all_cards_chk = _CheckBox(checked=True)
        self._time_columns = (_Column(), _Column())
        self.presenter = _TogglePresenter()

    def _apply_show_times_state(self) -> None:
        """Delegate the gate to the real view method."""
        ResultsWindow._apply_show_times_state(self)


def test_apply_show_times_state_given_times_off_disables_and_clears_the_time_board() -> None:
    """R-63: times off clears and disables the Fastest-time box."""
    shell = _PublishShell(show_times=False, time_board=True)

    ResultsWindow._apply_show_times_state(shell)

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (False, False)


def test_apply_show_times_state_given_times_off_hides_the_total_column() -> None:
    """Times off still hides the Total column on every list."""
    shell = _PublishShell(show_times=False, time_board=False)

    ResultsWindow._apply_show_times_state(shell)

    assert shell._time_columns[0].hidden is True


def test_apply_show_times_state_given_times_off_hides_the_best_lap_column_too() -> None:
    """Part 3: Best lap is time data, so times off hides it as well."""
    shell = _PublishShell(show_times=False, time_board=False)

    ResultsWindow._apply_show_times_state(shell)

    assert shell._time_columns[1].hidden is True


def test_apply_show_times_state_given_times_off_hides_all_three_lists_time_columns() -> None:
    """T-4: every list's two time columns, not only the first one."""
    shell = _PublishShell(show_times=False, time_board=False)
    shell._time_columns = tuple(_Column() for _ in range(6))

    ResultsWindow._apply_show_times_state(shell)

    assert [column.hidden for column in shell._time_columns] == [True] * 6


def test_apply_show_times_state_given_times_on_reenables_the_time_board() -> None:
    """Re-checking show_times restores the box and the columns."""
    shell = _PublishShell(show_times=True, time_board=False)
    shell.time_board_chk.enabled = False  # the state while times were off
    shell._time_columns[0].SetHidden(True)  # noqa: FBT003 -- wx's positional bool

    ResultsWindow._apply_show_times_state(shell)

    assert (shell.time_board_chk.enabled, shell._time_columns[0].hidden) == (True, False)


def test_apply_show_times_state_given_times_on_keeps_a_checked_time_board() -> None:
    """R-63(d): with times on a checked box is left alone."""
    shell = _PublishShell(show_times=True, time_board=True)

    ResultsWindow._apply_show_times_state(shell)

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (True, True)


def test_publish_options_given_times_on_reports_a_checked_time_board() -> None:
    """R-63(d): with times on the mapping keeps time_board."""
    shell = _PublishShell(show_times=True, time_board=True)

    options = ResultsWindow.publish_options(shell)

    assert options == ExportOptions(show_times=True, time_board=True)


def test_show_publish_options_given_times_on_restores_the_time_board_tick() -> None:
    """Re-enabling times re-enables the box and its tick (R-63)."""
    shell = _PublishShell(show_times=False, time_board=False)
    shell.time_board_chk.enabled = False  # the state while times were off

    ResultsWindow.show_publish_options(shell, ExportOptions(show_times=True, time_board=True))

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (True, True)


def test_on_publish_toggle_given_times_off_clears_the_time_board_option() -> None:
    """Unchecking times clears the box before the read."""
    shell = _PublishShell(show_times=False, time_board=True)
    event = _CheckEvent(shell.show_times_chk)

    ResultsWindow._on_publish_toggle(shell, event)

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (False, False)
    assert ResultsWindow.publish_options(shell).time_board is False
    assert (shell.presenter.toggles, event.skipped) == (1, True)


def test_on_publish_toggle_given_another_checkbox_still_forwards_and_skips() -> None:
    """Only show_times_chk runs the gate; other toggles forward."""
    shell = _PublishShell(show_times=True, time_board=True)
    event = _CheckEvent(shell.laps_board_chk)

    ResultsWindow._on_publish_toggle(shell, event)

    assert (shell.presenter.toggles, event.skipped) == (1, True)
    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (True, True)


def test_show_publish_options_given_times_off_clears_a_reflected_time_board_tick() -> None:
    """Reflected options can never show a tick while times are off."""
    shell = _PublishShell(show_times=True, time_board=True)

    ResultsWindow.show_publish_options(shell, ExportOptions(show_times=False, time_board=True))

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (False, False)


# ------------------------------------------------- export-button gate


class _Button:
    """A wx.Button double recording enablement and its bound handler."""

    def __init__(self) -> None:
        """Start with no enablement and no handler."""
        self.enabled: bool | None = None
        self.handler: object | None = None

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record the enablement."""
        self.enabled = enabled

    def Bind(self, _event: object, handler: object) -> None:  # noqa: N802 -- wx API name
        """Record the bound handler."""
        self.handler = handler


class _StatusSource:
    """A ``DataSource`` double exposing only ``ride_status``."""

    def __init__(self, status: RideStatus) -> None:
        """Store the status the gate reads."""
        self._status = status

    def ride_status(self) -> RideStatus:
        """Return the stored status."""
        return self._status


class _ExportShell:
    """A ResultsWindow shell owning only what the export gate reads."""

    def __init__(self, *, status: RideStatus, on_export: object = None) -> None:
        """Build one button double per export button."""
        self.data_source = _StatusSource(status)
        self.on_export = on_export
        self.buttons = {
            button_name: _Button() for button_name, _target in results_win._EXPORT_BUTTONS
        }

    def _find(self, name: str, _expected_type: type = object) -> _Button:
        """Return the button double named *name*."""
        return self.buttons[name]


STATUS_GATE_CASES = (
    (RideStatus.DRAFT, False),
    (RideStatus.RUNNING, False),
    (RideStatus.FINISHED, True),
    (RideStatus.REOPENED, False),
)
STATUS_GATE_IDS = [status.value for status, _expected in STATUS_GATE_CASES]


@pytest.mark.parametrize(("status", "expected"), STATUS_GATE_CASES, ids=STATUS_GATE_IDS)
def test_bind_export_buttons_given_ride_status_enables_only_when_finished(
    status: RideStatus, *, expected: bool
) -> None:
    """Part D: every export button is FINISHED-gated."""
    shell = _ExportShell(status=status)

    ResultsWindow._bind_export_buttons(shell)

    assert [shell.buttons[name].enabled for name, _target in results_win._EXPORT_BUTTONS] == (
        [expected] * len(results_win._EXPORT_BUTTONS)
    )


@pytest.mark.parametrize(
    ("button_name", "target"),
    results_win._EXPORT_BUTTONS,
    ids=[name for name, _target in results_win._EXPORT_BUTTONS],
)
def test_bind_export_buttons_given_a_callback_binds_each_button_to_its_target(
    button_name: str, target: str
) -> None:
    """W11/Part D: a callback fires with the button's route target."""
    calls: list[str] = []
    shell = _ExportShell(status=RideStatus.FINISHED, on_export=calls.append)

    ResultsWindow._bind_export_buttons(shell)
    shell.buttons[button_name].handler(object())

    assert calls == [target]


def test_bind_export_buttons_given_no_callback_leaves_every_button_unbound() -> None:
    """T-3: no live ride leaves the buttons inert, still gated."""
    shell = _ExportShell(status=RideStatus.FINISHED)

    ResultsWindow._bind_export_buttons(shell)

    assert [shell.buttons[name].handler for name, _target in results_win._EXPORT_BUTTONS] == (
        [None] * len(results_win._EXPORT_BUTTONS)
    )


# ------------------------------------------- the ⚠ explanation (Part 1)
#
# A wxDataViewCtrl has no per-row hover tooltip, so the R-43 badge's
# explanation rides the activation gesture (double-click / Enter)
# instead: a draw row opens an OK-only information alert carrying the
# row's own tie note plus the one plain sentence (Part 1). The alert is
# driven through a recording wx.MessageDialog double, exactly as
# test_std_dialogs does -- opening a real modal would need a desktop and
# would block.


class _FakeMessageDialog:
    """Recording stand-in for ``wx.MessageDialog``; opens no window."""

    def __init__(  # noqa: PLR0913, PLR0917 -- mirrors wx.MessageDialog's 4-arg API
        self, parent: object, message: str, caption: str, style: int
    ) -> None:
        """Record the four arguments ``show_info`` passes."""
        self.parent = parent
        self.message = message
        self.caption = caption
        self.style = style
        _CREATED_DIALOGS.append(self)

    def ShowModal(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Report OK without opening anything."""
        return wx.ID_OK

    def Destroy(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the destroy (nothing was built to destroy)."""


_CREATED_DIALOGS: list[_FakeMessageDialog] = []


@pytest.fixture
def created_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[_FakeMessageDialog]:
    """Patch ``wx.MessageDialog``; return the constructions recorded."""
    _CREATED_DIALOGS.clear()
    monkeypatch.setattr(std_dialogs.wx, "MessageDialog", _FakeMessageDialog)
    return _CREATED_DIALOGS


class _ActivateEvent:
    """A ``DataViewEvent`` double naming its model and its item."""

    def __init__(self, model: object, item: object) -> None:
        """Store the model and item the activation resolves through."""
        self._model = model
        self._item = item

    def GetModel(self) -> object:  # noqa: N802 -- wx API name the double mirrors
        """Return the model the event was raised with."""
        return self._model

    def GetItem(self) -> object:  # noqa: N802 -- wx API name the double mirrors
        """Return the activated item."""
        return self._item


class _ActivateShell:
    """A ResultsWindow shell owning only the dialog the alert uses."""

    def __init__(self) -> None:
        """Build the dialog double the alert parents."""
        self.dialog = _Dialog()


def _draw_model(*, tie_note: str | None = "draw required") -> StandingsListModel:
    """Build a one-row model holding a draw-flagged standings row."""
    return StandingsListModel([_row(place=1, draw_required=True, tie_note=tie_note)])


# The exact body Part 1 specifies: the row's own tie note, then the one
# plain sentence saying what the flag means and who decides.
DRAW_MESSAGE = (
    "draw required\n\n"
    "Identical best hands were not resolved by the tie-break — the venue draw arbitrates."
)


def test_draw_info_message_given_a_draw_row_leads_with_its_tie_note() -> None:
    """The body is the row's tie note plus the explanation sentence."""
    row = _row(draw_required=True, tie_note="draw required")

    message = results_win.draw_info_message(row)

    assert message == DRAW_MESSAGE


def test_draw_info_message_given_an_unset_tie_note_still_reads_draw_required() -> None:
    """T-3: a hand-built draw row never renders the word "None"."""
    row = _row(draw_required=True, tie_note=None)

    message = results_win.draw_info_message(row)

    assert message == DRAW_MESSAGE


def test_draw_info_message_given_a_custom_tie_note_keeps_its_own_wording() -> None:
    """An engine note of its own leads the body verbatim."""
    row = _row(draw_required=True, tie_note="copy held identical")

    message = results_win.draw_info_message(row)

    assert message == f"copy held identical\n\n{DRAW_EXPLANATION}"


@given(note=st.text(alphabet="abcdefgh -", max_size=24))
def test_draw_info_message_keeps_the_note_and_closes_with_the_explanation(note: str) -> None:
    """Invariant (T-7): the note leads; the explanation closes."""
    message = results_win.draw_info_message(_row(draw_required=True, tie_note=note))

    assert message.startswith(note or results_win.DRAW_TIE_NOTE)
    assert message.endswith(f"\n\n{DRAW_EXPLANATION}")


def test_on_standings_activated_given_a_draw_row_shows_the_explanation(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """Part 1: activating a ⚠ row opens the OK-only info alert."""
    model = _draw_model()
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(model, model.GetItem(0)))

    assert len(created_dialogs) == 1
    assert (created_dialogs[0].parent, created_dialogs[0].caption) == (
        shell.dialog,
        DRAW_INFO_TITLE,
    )
    assert created_dialogs[0].message == DRAW_MESSAGE


def test_on_standings_activated_given_a_draw_row_uses_the_rows_own_note(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """The row's tie_note leads the body -- the engine's own wording."""
    model = _draw_model(tie_note="copy held identical")
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(model, model.GetItem(0)))

    assert created_dialogs[0].message.startswith("copy held identical\n\n")


def test_on_standings_activated_given_an_ordinary_row_shows_nothing(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """Part 1: a non-draw row's activation does nothing at all."""
    model = StandingsListModel([_row(place=1)])
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(model, model.GetItem(0)))

    assert created_dialogs == []


def test_on_standings_activated_given_an_item_naming_no_row_shows_nothing(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """T-4 boundary: an invalid item resolves to no row, so no alert."""
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(
        shell, _ActivateEvent(_draw_model(), wx.dataview.DataViewItem())
    )

    assert created_dialogs == []


def test_on_standings_activated_given_an_empty_list_shows_nothing(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """T-4 collection boundary: an empty list has no row to explain."""
    model = StandingsListModel([])
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(model, wx.dataview.DataViewItem()))

    assert created_dialogs == []


def test_on_standings_activated_given_a_foreign_model_shows_nothing(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """T-3: an event with no standings model is not ours to explain."""
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(None, wx.dataview.DataViewItem()))

    assert created_dialogs == []


def test_on_standings_activated_given_the_team_model_resolves_that_lists_row(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """The activated list's own model answers, not a fixed one."""
    model = StandingsListModel([_row(place=2, draw_required=True, tie_note="draw required")])
    shell = _ActivateShell()

    ResultsWindow._on_standings_activated(shell, _ActivateEvent(model, model.GetItem(0)))

    assert created_dialogs[0].message.startswith("draw required\n")


def test_bind_events_binds_the_activation_handler_on_every_standings_list() -> None:
    """Part 1: all three lists explain their own ⚠ rows."""
    shell = _BindShell()

    ResultsWindow._bind_events(shell)

    assert [control.bindings for control in shell.lists] == [
        [(wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, shell._on_standings_activated)]
    ] * len(shell.lists)


def test_bind_events_still_binds_every_publish_checkbox_to_the_dialog() -> None:
    """T-3: the new activation binding displaces no checkbox forward."""
    shell = _BindShell()

    ResultsWindow._bind_events(shell)

    assert [(event, source) for event, _handler, source in shell.dialog.bindings] == [
        (wx.EVT_CHECKBOX, checkbox)
        for checkbox in (
            shell.show_times_chk,
            shell.laps_board_chk,
            shell.time_board_chk,
            shell.full_field_chk,
            shell.all_cards_chk,
        )
    ]
