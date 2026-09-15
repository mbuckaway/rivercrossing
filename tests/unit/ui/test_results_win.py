# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``results_dlg``'s code-side behaviour (Part C/D).

Only what genuinely needs no window is pinned here, in the
``test_main_frame_riders_list.py`` / ``test_rider_list_columns.py``
style:

- :data:`STANDINGS_COLUMN_FLAGS` and the shared six-column list, every
  column pinned to its own width (G6);
- :meth:`StandingsListModel.Compare` -- the native header sort's
  per-column keys, its numeric Place/Laps and its non-negated
  row-position tie-break -- driven against a shell that owns only
  ``_rows``/``GetRow``;
- :meth:`ResultsWindow._build_columns`, for the Plate column the Team
  list drops under ``RIDER_POOLED`` (Part 2) and the Hand width each
  list's own column set pins (G6);
- :meth:`ResultsWindow.show_standings`, driven against a fake dialog
  and fake controls, for both the MIXED notebook and the SOLO
  standalone list;
- :meth:`ResultsWindow._on_standings_activated` -- the ⚠ badge's
  double-click explanation (Part 1) -- against a fake ``DataViewEvent``
  and a recording ``wx.MessageDialog`` double (the ``test_std_dialogs``
  pattern: importing wx is safe without a display, opening a modal is
  not);
- the Part D export-button gate, driven against a fake ride status and
  fake buttons (no real button is created);
- :meth:`ResultsWindow._apply_min_size`'s ten-row floor on the three
  standings lists (D16) -- the ``STANDINGS_*`` constants pinned, and
  the ``SetMinSize`` argument captured by a recording control double
  per list.

The live layout -- real columns on real controls -- stays with the
(functional) suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.presenters.data_source import StandingsRow
from rivercrossing.ui.views import results_win
from rivercrossing.ui.views.results_win import (
    COL_BEST5,
    COL_ENTRY,
    COL_HAND,
    COL_LAPS,
    COL_PLACE,
    COL_PLATE,
    COLUMN_LABELS,
    COLUMN_WIDTHS,
    DRAW_EXPLANATION,
    DRAW_INFO_TITLE,
    HAND_WIDTH,
    STANDINGS_COLUMN_FLAGS,
    TEAM_HAND_WIDTH,
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
        self.columns: list[tuple[str, int, int, int, _Column]] = []
        self.bindings: list[tuple[object, object]] = []

    def AppendTextColumn(  # noqa: N802, PLR0913 -- wx API name and shape
        self, label: str, col: int, *, width: int, flags: int
    ) -> _Column:
        """Record the column and return its double."""
        column = _Column()
        self.columns.append((label, col, width, flags, column))
        return column

    def Bind(self, event: object, handler: object) -> None:  # noqa: N802 -- wx API name
        """Record one (event, handler) binding."""
        self.bindings.append((event, handler))

    def widths_by_label(self) -> dict[str, int]:
        """Map each appended column's label to its pinned width."""
        return {label: width for label, _col, width, _flags, _column in self.columns}


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
        """Build the dialog and the three lists."""
        self.dialog = _BindingDialog()
        self.lists = (_ListControl(), _ListControl(), _ListControl())
        self.standings_list, self.teams_standings_list, self.solo_standings_list = self.lists

    def _on_standings_activated(self, event: object) -> None:
        """Delegate the activation to the real view handler."""
        ResultsWindow._on_standings_activated(self, event)


def test_build_columns_for_given_a_control_appends_the_six_canvas_columns() -> None:
    """One column per COLUMN_LABELS entry, in order."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [(label, col) for label, col, _width, _flags, _column in control.columns] == [
        (label, col) for col, label in enumerate(COLUMN_LABELS)
    ]


def test_build_columns_for_given_a_control_carries_the_shared_column_flags() -> None:
    """Every column is natively sortable and resizable."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [flags for _label, _col, _width, flags, _column in control.columns] == (
        [STANDINGS_COLUMN_FLAGS] * len(COLUMN_LABELS)
    )


def test_build_columns_for_given_a_control_pins_every_column_width() -> None:
    """G6: each column is appended with its own pinned width."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [width for _label, _col, width, _flags, _column in control.columns] == list(
        COLUMN_WIDTHS
    )


def test_column_widths_given_the_six_columns_are_the_g6_pins() -> None:
    """G6: Place 60, Plate 50, Entry 160, Laps 50, Best 5 160, Hand."""
    assert COLUMN_WIDTHS == (60, 50, 160, 50, 160, 210)
    assert COLUMN_WIDTHS[COL_HAND] == HAND_WIDTH


def test_team_hand_width_given_a_pooled_team_frees_the_plate_column() -> None:
    """G6: the hidden Plate column's 50px go to the team's Hand."""
    assert TEAM_HAND_WIDTH == 260
    assert HAND_WIDTH + COLUMN_WIDTHS[COL_PLATE] == TEAM_HAND_WIDTH


def test_min_size_given_the_solo_columns_fits_the_dialog_at_740() -> None:
    """G6: 740 = the solo columns 690 plus scrollbar and borders."""
    assert results_win.MIN_SIZE == (740, 442)


def test_build_columns_for_given_a_wide_hand_pins_only_the_hand_column() -> None:
    """G6: the team list's Hand width is its own parameter."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control, hand_width=TEAM_HAND_WIDTH)

    assert control.widths_by_label() == {
        **dict(zip(COLUMN_LABELS, COLUMN_WIDTHS, strict=True)),
        "Hand": TEAM_HAND_WIDTH,
    }


def test_build_columns_for_given_no_hide_plate_leaves_every_column_visible() -> None:
    """Part 2's default: Table's Plate column is appended and shown."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control)

    assert [column.hidden for _label, _col, _width, _flags, column in control.columns] == (
        [None] * len(COLUMN_LABELS)
    )


def test_build_columns_for_given_hide_plate_hides_only_the_plate_column() -> None:
    """Part 2: the Team list's Plate column is the one that goes."""
    control = _ListControl()

    ResultsWindow._build_columns_for(control, hide_plate=True)

    assert [column.hidden for _label, _col, _width, _flags, column in control.columns] == [
        True if col == COL_PLATE else None for col in range(len(COLUMN_LABELS))
    ]


# ----------------------------------- the shared header (Part 2 + G6)


class _ColumnsShell:
    """A ResultsWindow shell owning the lists and a plate model."""

    def __init__(self, plate_model: PlateModel) -> None:
        """Build one column-recording list double per standings list."""
        self.plate_model = plate_model
        self.standings_list = _ListControl()
        self.teams_standings_list = _ListControl()
        self.solo_standings_list = _ListControl()

    def _build_columns_for(
        self, control: object, *, hide_plate: bool = False, hand_width: int = HAND_WIDTH
    ) -> None:
        """Delegate one list's columns to the real view method."""
        ResultsWindow._build_columns_for(control, hide_plate=hide_plate, hand_width=hand_width)


def test_column_labels_given_the_reworked_dialog_are_the_six_g6_columns() -> None:
    """G6: Total and Best lap leave the header entirely."""
    assert COLUMN_LABELS == ("Place", "Plate", "Entry", "Laps", "Best 5", "Hand")


def test_column_indexes_given_the_six_columns_are_zero_to_five() -> None:
    """G6: Best 5 and Hand reindex to 4 and 5."""
    assert (COL_PLACE, COL_PLATE, COL_ENTRY, COL_LAPS, COL_BEST5, COL_HAND) == (0, 1, 2, 3, 4, 5)


def test_build_columns_given_rider_pooled_hides_the_plate_column_on_the_team_list() -> None:
    """Part 2: a pooled team's plate repeats a member's -- it goes."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert shell.teams_standings_list.columns[COL_PLATE][4].hidden is True


def test_build_columns_given_rider_pooled_leaves_the_other_plate_columns_untouched() -> None:
    """Part 2: the standalone and Solo lists keep their Plate column."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert [
        shell.standings_list.columns[COL_PLATE][4].hidden,
        shell.solo_standings_list.columns[COL_PLATE][4].hidden,
    ] == [None, None]


def test_build_columns_given_team_relay_keeps_every_plate_column() -> None:
    """Part 2: relay plates identify the entry, so nothing is hidden."""
    shell = _ColumnsShell(PlateModel.TEAM_RELAY)

    ResultsWindow._build_columns(shell)

    assert [
        control.columns[COL_PLATE][4].hidden
        for control in (
            shell.standings_list,
            shell.teams_standings_list,
            shell.solo_standings_list,
        )
    ] == [None, None, None]


def test_build_columns_given_rider_pooled_pins_the_team_hand_column_at_260() -> None:
    """G6: the team Hand takes the hidden Plate column's width."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert shell.teams_standings_list.widths_by_label()["Hand"] == TEAM_HAND_WIDTH


def test_build_columns_given_rider_pooled_pins_the_solo_and_standalone_hand_at_210() -> None:
    """G6: only the team list's Hand width differs from the solo pin."""
    shell = _ColumnsShell(PlateModel.RIDER_POOLED)

    ResultsWindow._build_columns(shell)

    assert [
        shell.standings_list.widths_by_label()["Hand"],
        shell.solo_standings_list.widths_by_label()["Hand"],
    ] == [HAND_WIDTH, HAND_WIDTH]


def test_build_columns_given_team_relay_pins_every_hand_column_at_210() -> None:
    """G6: a shown Plate column keeps the team Hand at 210."""
    shell = _ColumnsShell(PlateModel.TEAM_RELAY)

    ResultsWindow._build_columns(shell)

    assert [
        control.widths_by_label()["Hand"]
        for control in (
            shell.standings_list,
            shell.teams_standings_list,
            shell.solo_standings_list,
        )
    ] == [HAND_WIDTH, HAND_WIDTH, HAND_WIDTH]


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


def test_standings_compare_given_the_laps_column_orders_numerically_not_as_text() -> None:
    """T-5: the int key sorts 10 laps after 9, which the text would not.

    The rendered cells are the strings "10" and "9", so this pins the
    numeric ``laps`` key -- the dialog's remaining numeric columns.
    """
    ten = _row(laps=10)
    nine = _row(laps=9)
    shell = _CompareShell([ten, nine])

    result = StandingsListModel.Compare(shell, 0, 1, COL_LAPS, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


def test_standings_compare_given_the_place_column_orders_numerically_not_as_text() -> None:
    """T-5: the int key sorts place 10 after place 9, not before it."""
    tenth = _row(place=10)
    ninth = _row(place=9)
    shell = _CompareShell([tenth, ninth])

    result = StandingsListModel.Compare(shell, 0, 1, COL_PLACE, True)  # noqa: FBT003 -- wx's positional bool

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


def test_bind_events_given_the_reworked_dialog_binds_no_dialog_control() -> None:
    """G6: the publish checkboxes left, so nothing is bound."""
    shell = _BindShell()

    ResultsWindow._bind_events(shell)

    assert shell.dialog.bindings == []


# ------------------------------- the standings lists' ten-row floor


class _FlooredControl:
    """A standings-list double recording its floor and the order."""

    def __init__(self, calls: list[str], name: str) -> None:
        """Join *calls* and start with no floor recorded."""
        self.calls = calls
        self.name = name
        self.min_size: wx.Size | None = None

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one floor and when it was applied."""
        self.calls.append(f"{self.name}.SetMinSize")
        self.min_size = size


class _MinSizeDialog:
    """A ``wx.Dialog`` double recording its own floor and its Fit()."""

    def __init__(self, calls: list[str]) -> None:
        """Join *calls* and start with no floor recorded."""
        self.calls = calls
        self.min_size: wx.Size | None = None

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the dialog's own floor."""
        self.calls.append("dialog.SetMinSize")
        self.min_size = size

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("dialog.Fit")


class _MinSizeShell:
    """A ResultsWindow shell owning only the min-size slots."""

    def __init__(self) -> None:
        """Build the dialog double and the three list doubles."""
        self.calls: list[str] = []
        self.dialog = _MinSizeDialog(self.calls)
        self.standings_list = _FlooredControl(self.calls, "standings_list")
        self.teams_standings_list = _FlooredControl(self.calls, "teams_standings_list")
        self.solo_standings_list = _FlooredControl(self.calls, "solo_standings_list")
        self.lists = (
            self.standings_list,
            self.teams_standings_list,
            self.solo_standings_list,
        )

    def _apply_min_size(self) -> None:
        """Delegate the floor to the real view method."""
        ResultsWindow._apply_min_size(self)


def test_standings_min_rows_given_the_results_lists_is_ten() -> None:
    """The scorer's working set: ten rows visible, no scrollbar."""
    assert results_win.STANDINGS_MIN_ROWS == 10


def test_standings_row_height_given_the_measured_wx_metric_is_seventeen() -> None:
    """The measured DataView row height on wxPython 4.3.1."""
    assert results_win.STANDINGS_ROW_HEIGHT == 17


def test_standings_header_height_given_the_measured_wx_metric_is_twenty_eight() -> None:
    """The measured DataView header height on wxPython 4.3.1."""
    assert results_win.STANDINGS_HEADER_HEIGHT == 28


def test_standings_list_min_height_given_ten_rows_is_198() -> None:
    """28 px header + 10 x 17 px rows: the floor each list is given."""
    assert results_win.STANDINGS_LIST_MIN_HEIGHT == 198


def test_standings_list_min_height_given_the_parts_is_header_plus_ten_rows() -> None:
    """The floor is derived from its parts, never a bare 198."""
    assert results_win.STANDINGS_LIST_MIN_HEIGHT == (
        results_win.STANDINGS_HEADER_HEIGHT
        + results_win.STANDINGS_MIN_ROWS * results_win.STANDINGS_ROW_HEIGHT
    )


def test_apply_min_size_given_the_three_lists_floors_each_at_198() -> None:
    """A MIXED notebook page and the SOLO list all hold ten rows."""
    shell = _MinSizeShell()

    ResultsWindow._apply_min_size(shell)

    assert [(control.min_size.width, control.min_size.height) for control in shell.lists] == [
        (-1, results_win.STANDINGS_LIST_MIN_HEIGHT)
    ] * len(shell.lists)


def test_apply_min_size_given_the_three_lists_floors_them_before_fitting_the_dialog() -> None:
    """Fit() must measure floored children, so floors come first."""
    shell = _MinSizeShell()

    ResultsWindow._apply_min_size(shell)

    assert shell.calls == [
        "standings_list.SetMinSize",
        "teams_standings_list.SetMinSize",
        "solo_standings_list.SetMinSize",
        "dialog.SetMinSize",
        "dialog.Fit",
    ]


def test_apply_min_size_given_the_dialog_keeps_the_measured_width_floor() -> None:
    """D16's width floor and the Fit()-measured height are unchanged."""
    shell = _MinSizeShell()

    ResultsWindow._apply_min_size(shell)

    assert (shell.dialog.min_size.width, shell.dialog.min_size.height) == (
        results_win.MIN_SIZE[0],
        -1,
    )
