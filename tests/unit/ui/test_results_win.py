# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``results_dlg``'s code-side behaviour (Part C/D).

Only what genuinely needs no window is pinned here, in the
``test_main_frame_riders_list.py`` / ``test_rider_list_columns.py``
style:

- :data:`STANDINGS_COLUMN_FLAGS` and the shared seven-column list;
- :meth:`StandingsListModel.Compare` -- the native header sort's
  per-column keys, its numeric Total (seconds, not display text) and
  its non-negated row-position tie-break -- driven against a shell
  that owns only ``_rows``/``GetRow``;
- :meth:`ResultsWindow.show_standings`, driven against a fake dialog
  and fake controls, for both the MIXED notebook and the SOLO
  standalone list;
- the Part D export-button gate, driven against a fake ride status and
  fake buttons (no real button is created).

The live layout -- real columns on real controls -- stays with the
(functional) suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode
from rivercrossing.ui.presenters.data_source import StandingsRow
from rivercrossing.ui.views import results_win
from rivercrossing.ui.views.results_win import (
    COL_BEST5,
    COL_ENTRY,
    COL_HAND,
    COL_LAPS,
    COL_PLACE,
    COL_PLATE,
    COL_TOTAL,
    COLUMN_LABELS,
    STANDINGS_COLUMN_FLAGS,
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
    best5: Sequence[str] = ("2C",),
    hand: str = "High Card — Ace",
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
        total_seconds=total_seconds,
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
    """A DataViewCtrl double for the code-side columns."""

    def __init__(self) -> None:
        """Start with no columns."""
        self.columns: list[tuple[str, int, int, _Column]] = []

    def AppendTextColumn(  # noqa: N802 -- wx API name the double mirrors
        self, label: str, col: int, *, flags: int
    ) -> _Column:
        """Record the column and return its double."""
        column = _Column()
        self.columns.append((label, col, flags, column))
        return column


def test_build_columns_for_given_a_control_appends_the_seven_canvas_columns() -> None:
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


def test_build_columns_for_given_a_control_returns_the_total_column() -> None:
    """Return the Total column, the one show_times_chk hides."""
    control = _ListControl()

    total = ResultsWindow._build_columns_for(control)

    assert total is control.columns[COL_TOTAL][3]


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


# Column, a lower row, a higher row: each pair differs only in the
# column under test, and the higher row must order after the lower one
# under that column's documented key (T-4 per-column coverage).
ORDER_CASES = (
    (COL_PLACE, _row(place=1), _row(place=2)),
    (COL_PLATE, _row(plate="2"), _row(plate="9")),
    (COL_ENTRY, _row(entry="A Racer"), _row(entry="B Racer")),
    (COL_LAPS, _row(laps=1), _row(laps=2)),
    (COL_TOTAL, _row(total_seconds=59.0), _row(total_seconds=3600.0)),
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
        """Build the five boxes, the columns and the presenter."""
        self.show_times_chk = _CheckBox(checked=show_times)
        self.laps_board_chk = _CheckBox(checked=True)
        self.time_board_chk = _CheckBox(checked=time_board)
        self.full_field_chk = _CheckBox(checked=True)
        self.all_cards_chk = _CheckBox(checked=True)
        self._total_columns = (_Column(),)
        self.presenter = _TogglePresenter()

    def _apply_show_times_state(self) -> None:
        """Delegate the gate to the real view method."""
        ResultsWindow._apply_show_times_state(self)


def test_apply_show_times_state_given_times_off_disables_and_clears_the_time_board() -> None:
    """R-63: times off clears and disables the Fastest-time box."""
    shell = _PublishShell(show_times=False, time_board=True)

    ResultsWindow._apply_show_times_state(shell)

    assert (shell.time_board_chk.enabled, shell.time_board_chk.GetValue()) == (False, False)


def test_apply_show_times_state_given_times_off_hides_every_total_column() -> None:
    """Times off still hides the Total column on every list."""
    shell = _PublishShell(show_times=False, time_board=False)

    ResultsWindow._apply_show_times_state(shell)

    assert shell._total_columns[0].hidden is True


def test_apply_show_times_state_given_times_off_hides_all_three_total_columns() -> None:
    """T-4: every list's Total column is hidden, not only the first."""
    shell = _PublishShell(show_times=False, time_board=False)
    shell._total_columns = (_Column(), _Column(), _Column())

    ResultsWindow._apply_show_times_state(shell)

    assert [column.hidden for column in shell._total_columns] == [True, True, True]


def test_apply_show_times_state_given_times_on_reenables_the_time_board() -> None:
    """Re-checking show_times restores the box and the column."""
    shell = _PublishShell(show_times=True, time_board=False)
    shell.time_board_chk.enabled = False  # the state while times were off
    shell._total_columns[0].SetHidden(True)  # noqa: FBT003 -- wx's positional bool

    ResultsWindow._apply_show_times_state(shell)

    assert (shell.time_board_chk.enabled, shell._total_columns[0].hidden) == (True, False)


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
