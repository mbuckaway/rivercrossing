# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``audit_dlg``'s columns, sort and size (Phase 6).

``audit.xrc`` declares no ``<size>`` and XRC has no window-level
minsize, so the audit dialog used to open at whatever a plain ``Fit()``
measured and its columns at the platform's 80 DIP default -- the Reason
cell clipped at every window size. ``AuditDialog`` now appends each
column at its own pinned width (When 90 | Action 180 | Entry 120 |
Reason 330), every column carries :data:`AUDIT_COLUMN_FLAGS` (sortable
and resizable), and ``_apply_min_size`` floors *and* opens the dialog at
:data:`audit.MIN_SIZE` (1000x600, ~2x the measured content), bounded by
:func:`~rivercrossing.ui.views._support.clamp_to_display` so a small
screen still shows the whole dialog.

The Who column is gone (scope 6a): the engine never records another
actor, so every cell read the same word. The four surviving columns
reindex to 0-3 and :meth:`AuditListModel.Compare` answers the native
header sort for all of them, keyed by the same text accessors the cells
render through.

A real ``wx.Dialog`` needs a desktop, so these tests drive recording
doubles and call the steps directly -- the same shape
``test_main_frame_feed_list.py`` and ``test_dialogs_positioning.py``
use. ``clamp_to_display`` reads the live display through
``wx.GetClientDisplayRect``, which raises without a ``wx.App``, so the
work area is stubbed: that read is the sizing step's GUI I/O boundary
(T-10), not its logic. Constructing a ``DataViewIndexListModel`` needs
no ``wx.App``, so the model's own accessors and ``Compare`` run for
real.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.presenters.audit import ACTION_CHOICES, ALL_ACTIONS
from rivercrossing.ui.presenters.data_source import AuditRow
from rivercrossing.ui.views.audit import (
    AUDIT_COLUMN_FLAGS,
    AUDIT_COLUMN_LABELS,
    AUDIT_COLUMN_WIDTHS,
    COL_ACTION,
    COL_ENTRY,
    COL_REASON,
    COL_WHEN,
    DEFAULT_SORT_ASCENDING,
    DEFAULT_SORT_COLUMN,
    MIN_SIZE,
    AuditDialog,
    AuditListModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# A work area roomy enough for the dialog's own 1000x600 floor.
_ROOMY_DISPLAY = (0, 34, 1920, 1080)

# A work area smaller than the floor: the clamp must win.
_CRAMPED_DISPLAY = (0, 34, 800, 480)


def _stub_display(monkeypatch: pytest.MonkeyPatch, rect: tuple[int, int, int, int]) -> None:
    """Point ``wx.GetClientDisplayRect`` at *rect* for one test."""
    monkeypatch.setattr(wx, "GetClientDisplayRect", lambda: rect)


def _row(  # noqa: PLR0913 -- (when, action, entry, reason): one fixed audit row
    *,
    when: str = "10:00:00",
    action: str = "record_crossing",
    entry: str = "12",
    reason: str = "",
) -> AuditRow:
    """Build one fixed audit row."""
    return AuditRow(when=when, action=action, entry=entry, reason=reason)


class _RecordingList:
    """An ``audit_list`` double recording each appended column."""

    def __init__(self) -> None:
        """Start with no columns."""
        self.columns: list[tuple[str, int, int, int]] = []

    def AppendTextColumn(  # noqa: N802, PLR0913 -- wx API name; its own four fields
        self, label: str, col: int, *, width: int, flags: int
    ) -> None:
        """Record one appended text column, its width and its flags."""
        self.columns.append((label, col, width, flags))


class _SizingDialog:
    """A dialog double recording every sizing call the SUT makes."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("Fit")

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = size


def _dialog_over(dialog: _SizingDialog) -> AuditDialog:
    """Return an ``AuditDialog`` over *dialog*, no window loaded."""
    view = object.__new__(AuditDialog)
    view.dialog = dialog
    return view


# ------------------------------------------------------------- columns


def test_audit_column_labels_given_the_retired_who_column_are_the_four() -> None:
    """Scope 6a: the Who column leaves the header entirely."""
    assert AUDIT_COLUMN_LABELS == ("When", "Action", "Entry", "Reason")


def test_audit_column_indexes_given_the_four_columns_are_zero_to_three() -> None:
    """Scope 6a: Action, Entry and Reason reindex to 1, 2 and 3."""
    assert (COL_WHEN, COL_ACTION, COL_ENTRY, COL_REASON) == (0, 1, 2, 3)


def test_audit_column_widths_given_the_four_labels_are_the_pinned_pixels() -> None:
    """When 90 | Action 180 | Entry 120 | Reason 330."""
    assert AUDIT_COLUMN_WIDTHS == (90, 180, 120, 330)


def test_audit_column_widths_given_the_four_labels_carry_one_width_each() -> None:
    """One width per label: the column index never misaligns."""
    assert len(AUDIT_COLUMN_WIDTHS) == len(AUDIT_COLUMN_LABELS)


def test_audit_column_flags_given_the_native_header_are_sortable_and_resizable() -> None:
    """Scope 6b: both bits are spelled out, never left to the default.

    An explicit ``flags=`` *replaces* ``AppendTextColumn``'s own default
    rather than OR-ing into it, so macOS would otherwise set
    ``NSTableColumnNoResizing``.
    """
    assert AUDIT_COLUMN_FLAGS == (
        wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE
    )


def test_audit_dialog_build_columns_given_the_labels_appends_each_width_and_flags() -> None:
    """Every column carries its label, index, width and flags."""
    listing = _RecordingList()
    view = object.__new__(AuditDialog)
    view.audit_list = listing

    view._build_columns()

    assert listing.columns == [
        (label, col, AUDIT_COLUMN_WIDTHS[col], AUDIT_COLUMN_FLAGS)
        for col, label in enumerate(AUDIT_COLUMN_LABELS)
    ]


def test_audit_column_widths_given_the_reason_column_leave_it_the_widest() -> None:
    """The Reason cell carries arbitrary text, so it takes the slack."""
    assert AUDIT_COLUMN_WIDTHS[COL_REASON] == max(AUDIT_COLUMN_WIDTHS)


def test_audit_list_model_column_count_given_the_four_labels_is_four() -> None:
    """The model answers one column per label -- no Who column."""
    model = AuditListModel([_row()])

    assert model.GetColumnCount() == 4


# ------------------------------------------------------ model accessors


def test_audit_list_model_value_by_row_given_a_row_returns_each_cell() -> None:
    """The four accessors follow the reindexed column order."""
    model = AuditListModel(
        [_row(when="10:02:00", action="edit_crossing", entry="77", reason="mis-keyed")]
    )

    cells = (
        model.GetValueByRow(0, COL_WHEN),
        model.GetValueByRow(0, COL_ACTION),
        model.GetValueByRow(0, COL_ENTRY),
        model.GetValueByRow(0, COL_REASON),
    )

    assert cells == ("10:02:00", "edit_crossing", "77", "mis-keyed")


# ------------------------------------------------------------- Compare


class _CompareShell:
    """An ``AuditListModel`` shell owning only ``_rows`` and GetRow.

    ``Compare`` reads ``self.GetRow(item)`` and ``self._rows``; driving
    it against this shell keeps the sort keys under test without
    building a wx model or a window.
    """

    def __init__(self, rows: Sequence[AuditRow]) -> None:
        """Store the rows the comparison reads."""
        self._rows = tuple(rows)

    def GetRow(self, item: int) -> int:  # noqa: N802 -- mirrors the wx base method
        """Return the item unchanged (items are row indexes here)."""
        return item


# Column, a lower row, a higher row: each pair differs only in the
# column under test, and the higher row must order after the lower one
# under that column's rendered text (T-4 per-column coverage).
ORDER_CASES = (
    (COL_WHEN, _row(when="10:00:00"), _row(when="10:00:01")),
    (COL_ACTION, _row(action="dnf"), _row(action="edit_crossing")),
    (COL_ENTRY, _row(entry="12"), _row(entry="34")),
    (COL_REASON, _row(reason="double entry"), _row(reason="mis-keyed time")),
)
ORDER_CASE_IDS = ("when", "action", "entry", "reason")

ASCENDING_CASES = ((True, -1), (False, 1))
ASCENDING_IDS = ("ascending", "descending")


@pytest.mark.parametrize(("ascending", "expected"), ASCENDING_CASES, ids=ASCENDING_IDS)
@pytest.mark.parametrize(("col", "lower", "higher"), ORDER_CASES, ids=ORDER_CASE_IDS)
def test_audit_compare_given_two_rows_orders_by_the_column_text(  # noqa: PLR0913 -- the two rows, the column and the arrow
    col: int, lower: AuditRow, higher: AuditRow, *, ascending: bool, expected: int
) -> None:
    """Every column sorts by its rendered text; the arrow flips it."""
    shell = _CompareShell([lower, higher])

    result = AuditListModel.Compare(shell, 0, 1, col, ascending)

    assert result == expected


@pytest.mark.parametrize("ascending", [True, False], ids=ASCENDING_IDS)
def test_audit_compare_given_equal_keys_keeps_the_row_position_unnegated(
    *, ascending: bool
) -> None:
    """T-3: equal keys fall back to the position, never negated."""
    shell = _CompareShell([_row(when="10:00:00"), _row(when="10:00:00")])

    result = AuditListModel.Compare(shell, 0, 1, COL_WHEN, ascending)

    assert result == -1


def test_audit_compare_given_reversed_equal_key_rows_orders_by_position() -> None:
    """T-3: the tie-break follows the row index, not the item."""
    shell = _CompareShell([_row(when="10:00:00"), _row(when="10:00:00")])

    result = AuditListModel.Compare(shell, 1, 0, COL_WHEN, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


def test_audit_compare_given_the_reason_column_sorts_by_its_rendered_text() -> None:
    """The cell's text is the key: what is drawn sorts the rows."""
    shell = _CompareShell([_row(reason="mis-keyed time"), _row(reason="")])

    result = AuditListModel.Compare(shell, 0, 1, COL_REASON, True)  # noqa: FBT003 -- wx's positional bool

    assert result == 1


# ----------------------------------------------------- the default sort


def test_audit_default_sort_given_the_canvas_order_is_when_descending() -> None:
    """Scope 6b: the opening order is newest event first."""
    assert (DEFAULT_SORT_COLUMN, DEFAULT_SORT_ASCENDING) == (COL_WHEN, False)


class _Column:
    """A ``wx.dataview.DataViewColumn`` double for the default sort."""

    def __init__(self, *, is_sort_key: bool = False) -> None:
        """Carry the sort-key state, which defaults to never-sorted.

        *is_sort_key* defaults to ``False``: a freshly built column has
        never sorted the control, which is exactly the state Windows'
        generic ``UnsetAsSortKey`` aborts on.
        """
        self.is_sort_key = is_sort_key
        self.operations: list[str] = []
        self.sort_orders: list[bool] = []

    def IsSortKey(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return whether the control currently sorts by this column."""
        return self.is_sort_key

    def UnsetAsSortKey(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the sort-key clear the macOS re-apply needs."""
        self.operations.append("unset")

    def SetSortOrder(self, ascending: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the direction the view re-applied."""
        self.sort_orders.append(ascending)
        self.operations.append("set")


class _ResortModel:
    """A ``DataViewIndexListModel`` double recording its resorts."""

    def __init__(self) -> None:
        """Start with no resort requested."""
        self.resorts = 0

    def Resort(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one resort request."""
        self.resorts += 1


class _SortControl:
    """An ``audit_list`` double owning its ``GetColumn`` columns."""

    def __init__(self, column: _Column | None = None) -> None:
        """Carry the column at :data:`DEFAULT_SORT_COLUMN`, if any."""
        self.columns: dict[int, _Column] = {}
        if column is not None:
            self.columns[DEFAULT_SORT_COLUMN] = column

    def GetColumn(self, index: int) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column at *index*, or ``None``."""
        return self.columns.get(index)


class _SortShell(AuditDialog):
    """A shell owning only what ``_apply_default_sort`` reads."""

    def __init__(self, control: _SortControl, model: Any = None) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Own *control* and the current *model*."""
        self.audit_list = control
        self._model: Any = model


def _sort_shell(*, column: _Column | None, model: Any) -> _SortShell:  # noqa: ANN401 -- wx ships no stubs
    """Return a shell over *column* and *model*."""
    return _SortShell(_SortControl(column), model)


def test_apply_default_sort_given_no_model_leaves_the_control_alone() -> None:
    """T-3 negative: rows never rendered means nothing to resort."""
    column = _Column()
    shell = _sort_shell(column=column, model=None)

    shell._apply_default_sort()

    assert column.sort_orders == []


def test_apply_default_sort_given_a_missing_column_leaves_the_model_alone() -> None:
    """T-3 negative: the control no longer has the When column."""
    model = _ResortModel()
    shell = _sort_shell(column=None, model=model)

    shell._apply_default_sort()

    assert model.resorts == 0


def test_apply_default_sort_given_a_never_sorted_column_leaves_the_key_alone() -> None:
    """First render: skip the clear for a never-sorted column.

    Windows' generic ``DataViewColumn.UnsetAsSortKey`` asserts ("column
    is not used for sorting") whenever the column is not the control's
    sort key -- the first render's exact state -- and aborts the
    process; skipping the clear keeps it alive.
    """
    column = _Column()
    model = _ResortModel()
    shell = _sort_shell(column=column, model=model)

    shell._apply_default_sort()

    assert (column.operations, column.sort_orders, model.resorts) == (["set"], [False], 1)


def test_apply_default_sort_given_a_live_sort_key_unsets_then_sets_then_resorts() -> None:
    """Measured macOS: clearing a live sort key is load-bearing."""
    column = _Column(is_sort_key=True)
    model = _ResortModel()
    shell = _sort_shell(column=column, model=model)

    shell._apply_default_sort()

    assert (column.operations, column.sort_orders, model.resorts) == (
        ["unset", "set"],
        [False],
        1,
    )


# ------------------------------------------------------ show_audit_rows


class _ShowControl(_RecordingList):
    """An ``audit_list`` double for ``show_audit_rows``."""

    def __init__(self) -> None:
        """Start with no model and a clean repaint log."""
        super().__init__()
        self.associated: object | None = None
        self.refreshes = 0
        self.updates = 0

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.associated = model

    def Refresh(self) -> None:  # noqa: N802 -- wx API name
        """Record the repaint request."""
        self.refreshes += 1

    def Update(self) -> None:  # noqa: N802 -- wx API name
        """Record the immediate repaint."""
        self.updates += 1


class _ShowShell(AuditDialog):
    """A shell owning only what ``show_audit_rows`` reads."""

    def __init__(self, control: _ShowControl) -> None:
        """Own *control*, with no model rendered yet."""
        self.audit_list = control
        self._model: AuditListModel | None = None
        self.default_sorts = 0

    def _apply_default_sort(self) -> None:
        """Record the default-sort call, never touching the control."""
        self.default_sorts += 1


def test_show_audit_rows_given_rows_associates_a_model_and_repaints() -> None:
    """The rebuilt model is handed to the control and repainted."""
    control = _ShowControl()
    shell = _ShowShell(control)

    shell.show_audit_rows([_row()])

    assert (control.associated is shell._model, control.refreshes, control.updates) == (
        True,
        1,
        1,
    )


def test_show_audit_rows_given_rows_renders_every_cell_of_the_first_row() -> None:
    """The association carries the rows, reindexed four-wide."""
    shell = _ShowShell(_ShowControl())

    shell.show_audit_rows(
        [_row(when="10:02:00", action="edit_crossing", entry="77", reason="mis-keyed")]
    )

    model = shell._model
    cells = (
        model.GetValueByRow(0, COL_WHEN),
        model.GetValueByRow(0, COL_ACTION),
        model.GetValueByRow(0, COL_ENTRY),
        model.GetValueByRow(0, COL_REASON),
    )
    assert cells == ("10:02:00", "edit_crossing", "77", "mis-keyed")


def test_show_audit_rows_given_rows_reapplies_the_default_sort() -> None:
    """Scope 6b: associating a new model drops the sort key it held."""
    shell = _ShowShell(_ShowControl())

    shell.show_audit_rows([_row()])

    assert shell.default_sorts == 1


def test_show_audit_rows_given_no_rows_associates_an_empty_model() -> None:
    """T-4 empty collection: no events renders a header and no rows."""
    control = _ShowControl()
    shell = _ShowShell(control)

    shell.show_audit_rows([])

    assert (shell._model.GetCount(), shell._model.GetColumnCount()) == (0, 4)


# ------------------------------------------ the action_choice mapping
# The dropdown draws labels; the audit trail records action strings.
# The view is the one place the two vocabularies meet, so the presenter
# only ever filters on an action.


class _Choice:
    """A ``wx.Choice`` double carrying its own string selection."""

    def __init__(self, selection: str) -> None:
        """Start on *selection*."""
        self._selection = selection

    def GetStringSelection(self) -> str:  # noqa: N802 -- wx API name the double mirrors
        """Return the dropdown's current label."""
        return self._selection


class _Event:
    """A ``wx.CommandEvent`` double recording the skip."""

    def __init__(self) -> None:
        """Start before the handler ran."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record that the handler let the event continue."""
        self.skipped = True


class _ActionPresenter:
    """An ``AuditPresenter`` double recording the forwarded action."""

    def __init__(self) -> None:
        """Start with no forwarded action."""
        self.actions: list[str] = []

    def on_action_selected(self, action: str) -> None:
        """Record one forwarded action."""
        self.actions.append(action)


class _ChoiceShell(AuditDialog):
    """A shell owning only what the choice handler reads."""

    def __init__(self, selection: str) -> None:
        """Own a choice on *selection* and a recording presenter."""
        self.action_choice = _Choice(selection)
        self.presenter = _ActionPresenter()


@pytest.mark.parametrize(
    ("label", "action"),
    ACTION_CHOICES,
    ids=[action for _label, action in ACTION_CHOICES],
)
def test_selected_action_given_a_dropdown_label_returns_its_action(
    label: str, action: str
) -> None:
    """Every label resolves to the action the trail records for it."""
    shell = _ChoiceShell(label)

    assert shell._selected_action() == action


def test_selected_action_given_the_all_actions_label_returns_it_unchanged() -> None:
    """The All actions label is the no-filter value, not an action."""
    shell = _ChoiceShell(ALL_ACTIONS)

    assert shell._selected_action() == ALL_ACTIONS


def test_selected_action_given_an_unknown_label_returns_all_actions() -> None:
    """T-4 negative: an unknown label never blanks the list."""
    shell = _ChoiceShell("Not a label")

    assert shell._selected_action() == ALL_ACTIONS


def test_on_action_selected_given_a_label_forwards_the_resolved_action() -> None:
    """The presenter filters on actions, so the view resolves first."""
    shell = _ChoiceShell("Record Crossing")
    event = _Event()

    shell._on_action_selected(event)

    assert (shell.presenter.actions, event.skipped) == (["record_crossing"], True)


# ------------------------------------------------------------- min size


def test_audit_dialog_min_size_given_the_fitted_content_is_1000_by_600() -> None:
    """Part 5's own numbers: 2x the dialog's measured content."""
    assert MIN_SIZE == (1000, 600)


def test_audit_dialog_apply_min_size_given_a_roomy_display_floors_and_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SetMinSize is what floors it; Fit() is what grows it now."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height, dialog.calls) == (
        1000,
        600,
        ["SetMinSize", "Fit"],
    )


def test_audit_dialog_apply_min_size_given_a_cramped_display_clamps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A display smaller than the floor still gets a whole dialog."""
    _stub_display(monkeypatch, _CRAMPED_DISPLAY)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (800, 480)


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        ((0, 34, 999, 599), (999, 599)),  # T-4: min - 1 on both axes
        ((0, 34, 1000, 600), (1000, 600)),  # T-4: exactly the floor
        ((0, 34, 1001, 601), (1000, 600)),  # T-4: min + 1, the floor holds
        ((0, 34, 3840, 2160), (1000, 600)),  # a 4K display
    ],
    ids=["min_minus_one", "min", "min_plus_one", "roomy"],
)
def test_audit_dialog_apply_min_size_given_any_display_fits_the_area(
    monkeypatch: pytest.MonkeyPatch,
    rect: tuple[int, int, int, int],
    expected: tuple[int, int],
) -> None:
    """T-4 boundaries: the floor is the smaller of it and the area."""
    _stub_display(monkeypatch, rect)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == expected


@given(size=st.tuples(st.integers(0, 4000), st.integers(0, 4000)))
def test_audit_dialog_apply_min_size_given_any_display_keeps_it_inside(
    size: tuple[int, int],
) -> None:
    """T-7 invariant: the applied floor never exceeds the work area."""
    # A context manager, not the monkeypatch fixture: Hypothesis does
    # not reset a function-scoped fixture between generated inputs.
    with patch.object(wx, "GetClientDisplayRect", lambda: (0, 34, *size)):
        dialog = _SizingDialog()

        _dialog_over(dialog)._apply_min_size()

    assert dialog.min_size.width <= size[0]
    assert dialog.min_size.height <= size[1]
