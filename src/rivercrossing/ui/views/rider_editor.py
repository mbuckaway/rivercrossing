# SPDX-License-Identifier: GPL-3.0-only
"""``RiderEditor``/``CsvPreviewDialog``: 1d/2b and 3e, on a real Roster.

E1.5.2 wired ``rider_editor_dlg`` to a display-only ``DataSource``
projection of the demo rows. E3.2.1/E3.2.2 replaced that with a real,
in-memory :class:`~rivercrossing.roster.Roster` that
``RidersPresenter`` (``ui.presenters.riders``) reads and writes
directly -- :class:`RiderEditor` takes ``roster=`` instead of
``data_source=``, constructs its own ``RidersPresenter`` (mirroring
``views/selftest.py``'s presenter-inside-the-view wiring), and binds
Add/Save/Delete/row selection to it. E5.4.2 retires the demo seam: the
app bootstrap's roster is empty until a store-backed ride is opened
(the library Open / resume flow replaces ``context.roster`` with the
store's), so the editor shows a correct empty state with no ride
open. E3.4 adds
:class:`CsvPreviewDialog` for ``csv_preview_dlg``: a *second* view,
pairing with a *second* ``RidersPresenter`` instance over the same
live roster, constructed with ``load=False`` (that class's own
``__init__`` docstring). Each view implements exactly one half of
``RidersView`` for real and raises ``NotImplementedError`` naming
the other -- :class:`RiderEditor` never renders
``summary_lbl``/``conflicts_list`` (it has none), and
:class:`CsvPreviewDialog` never renders
``riders_list``/``team_choice`` (it has none either); this is the
honest mirror image, not a gap.

1.0.12 makes ``rider_editor_dlg``'s form display-only (its text
fields are XRC ``wxTE_READONLY`` and its ``save_btn`` is gone): the
pane renders the selected record and the writing happens in
``add_rider_dlg``, which this module opens in either mode --
:class:`AddRiderDialog` takes the record to edit as its ``editing``
argument and pairs :class:`~rivercrossing.ui.presenters.riders.
EditRiderPresenter` with the same :class:`AddRiderDialog` view.
:class:`RiderEditor` binds ``edit_btn`` and a ``riders_list`` row
activation to that flow, and implements ``RidersView.confirm`` (the
delete confirm, :func:`~rivercrossing.ui.std_dialogs.show_confirm`)
for ``RidersPresenter.on_delete``.

xrc-windows.md section C's code-side footnote puts ``riders_list``'s
rows, its Team column's solo-only visibility, ``team_choice``'s
content and ``delete_btn``'s has-data gate in code -- ``riders.xrc``'s
own header explains why (``wxDataViewListCtrl`` would overwrite the
frozen name). A duplicate-plate or other refused operation renders as
a code-side ``wxInfoBar`` (:data:`ROSTER_INFOBAR` on ``rider_editor_
dlg``, :data:`CSV_INFOBAR` on ``csv_preview_dlg``) -- the same
measured pattern ``views/main_frame.py``'s ``_build_infobar`` uses,
except neither of these two dialogs' already-authored top sizer has a
reserved InfoBar slot (they predate this decision), so each InfoBar is
wrapped around its own dialog's existing sizer instead of inserted
into it -- and both disable ``wx.InfoBar``'s default slide effect
(``SetShowHideEffects``), measured to hang ``ShowMessage()``/
``Dismiss()`` on this build otherwise (first found wiring
``ROSTER_INFOBAR``, E3.2's follow-on sweep).

``_find`` is shared via ``ui.views._support.find_control`` -- see
that module's docstring for why it used to be duplicated here.

Phase 3 (Sex + sortable rider lists) changes four things here. The
editor's Team field becomes a read-only ``wxTextCtrl`` (it shows the
selected record's team and offers no way to change it -- assignment
is the Add/Edit dialog's job), so ``show_team_choices`` is gone from
both this view and ``RidersView``. The editor's list gains a Sex
column and spans the shared
:data:`~rivercrossing.ui.rider_columns.EDITOR_RIDER_COLUMNS`, rendered
by the shared :class:`~rivercrossing.ui.views._support.
RiderRowListModel`. ``add_rider_dlg`` gains a Sex dropdown
(``sex_choice``: blank, M, F) and opens no narrower than
:data:`ADD_RIDER_MIN_WIDTH` pixels.

**Native sorting.** Each column is appended with
:data:`RIDERS_LIST_COLUMN_FLAGS` (sortable *and* resizable, since an
explicit ``flags=`` argument replaces rather than extends wx's
default) and sorted through :meth:`RiderRowListModel.Compare` --
keyed by the shared column's own ``sort_key`` -- so the header arrow
the platform draws actually reorders the rows. The Name column opens
at :data:`COL_NAME_WIDTH` (double the platform's 80 DIP default).
:meth:`RiderEditor._apply_sort` re-applies the operator's current
sort after every ``show_riders`` rebuild (replacing the model drops
the control's sort key); no column sorts until a header is clicked,
so the list opens in the presenter's own order. Selection survives
the sort because the view forwards the selected row's *model* index
and the presenter's visible list is that same model order.

Phase 6 gives ``csv_preview_dlg`` its own default size:
:class:`CsvPreviewDialog` opens it twice as wide and twice as tall
as the size its own ``Fit()`` measured, in code, because XRC cannot
set a window minsize (``riders.xrc``'s header). Phase 3 narrows that
width from three times to two and adds the dialog's own "Map unknown
sex to Male" checkbox, forwarded to the presenter as
``on_toggle_map_unknown_sex``.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx
import wx.dataview
import wx.xrc

from rivercrossing import csvio
from rivercrossing.ui import ids, rider_columns, std_dialogs
from rivercrossing.ui.presenters.riders import (
    AddRiderPresenter,
    CsvConflict,
    EditRiderPresenter,
    RiderFormValues,
    RidersPresenter,
)
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import (
    RiderRowListModel,
    associate_model,
    clamp_to_display,
    find_control,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.roster import Entry, Rider, Roster
    from rivercrossing.ui.presenters.data_source import RiderRow
    from rivercrossing.ui.presenters.riders import CsvPreview

__all__ = [
    "ADD_RIDER_INFOBAR",
    "ADD_RIDER_MIN_WIDTH",
    "COLUMN_LABELS",
    "COLUMN_WIDTHS",
    "COL_DEFAULT_WIDTH",
    "COL_NAME",
    "COL_NAME_WIDTH",
    "COL_PLATE",
    "COL_PROBLEM",
    "COL_ROW",
    "COL_SEX",
    "COL_TEAM",
    "CONFLICT_COLUMN_LABELS",
    "CSV_INFOBAR",
    "CSV_PREVIEW_HEIGHT_SCALE",
    "CSV_PREVIEW_WIDTH_SCALE",
    "MIN_SIZE",
    "OK_BUTTON_SCALE",
    "RIDERS_LIST_COLUMN_FLAGS",
    "ROSTER_INFOBAR",
    "SEX_OPTIONS",
    "SOLO_TEAM_TEXT",
    "AddRiderDialog",
    "CsvConflictsListModel",
    "CsvPreviewDialog",
    "RiderEditor",
    "add_rider_width_floor",
    "ok_button_min_size",
    "run_add_rider_flow",
    "run_csv_export_flow",
    "run_csv_import_flow",
    "run_edit_rider_flow",
    "sex_from_choice",
]

# riders_list's columns come from the shared, wx-free
# ``ui.rider_columns`` (Phase 3), so this editor's list and the
# console's own ``console_riders_list`` cannot drift. The index names
# stay as this module's own public surface (the functional suite and
# this file read them).
COL_PLATE = 0
COL_NAME = 1
COL_TEAM = 2
COL_SEX = 3

COLUMN_LABELS: tuple[str, ...] = tuple(
    column.label for column in rider_columns.EDITOR_RIDER_COLUMNS
)

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the
# column NSTableColumnNoResizing -- so both bits must be spelled out
# (team_editor.py's measured note).
RIDERS_LIST_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE

# Both rider lists default every column to wxDVC_DEFAULT_WIDTH (80
# DIP). The Name column carries the rider the operator reads, so it
# opens at double that; the other columns keep the platform default
# -- the last one is what wx stretches to fill the control.
COL_DEFAULT_WIDTH = 80
COL_NAME_WIDTH = 160

# One width per COLUMN_LABELS entry, in that order.
COLUMN_WIDTHS: tuple[int, ...] = (
    COL_DEFAULT_WIDTH,
    COL_NAME_WIDTH,
    COL_DEFAULT_WIDTH,
    COL_DEFAULT_WIDTH,
)

COL_ROW = 0
COL_PROBLEM = 1

# xrc-windows.md C's csv_preview_dlg mock: "Row | Problem".
CONFLICT_COLUMN_LABELS: tuple[str, ...] = ("Row", "Problem")

# The canvas's own word for a solo rider's Team cell, from the shared
# module (Phase 3) -- ``views/main_frame.py`` reads the same one.
SOLO_TEAM_TEXT = rider_columns.SOLO_TEAM_TEXT

# ui/ids.py is generated from the .xrc files (R-05); these names
# never appear there since XRC cannot author a wxInfoBar at all
# (xrc-windows.md's own code-side footnote, main_frame.py's precedent).
ROSTER_INFOBAR = "roster_infobar"
CSV_INFOBAR = "csv_infobar"
ADD_RIDER_INFOBAR = "add_rider_infobar"

# W7 rework: the canvas redraws this dialog at 1280x560 with the
# riders_list pane dominant (riders.xrc's own 3:2 sizer options);
# XRC has no window-level minsize (riders.xrc's header notes this and
# defers to code), so _apply_min_size enforces the floor in code,
# width AND height.
MIN_SIZE = (1280, 560)

# 1.0.12 (B2): add_rider_dlg's primary button is the window's one
# affordance, and XRC cannot give a *window* a minsize (see above), so
# its width floor is applied in code. The floor is a multiple of
# whatever the platform measured for the label, never a fixed pixel
# width -- "Save" reads the same on Windows and macOS, the metrics do
# not.
OK_BUTTON_SCALE = 3

# Phase 3: add_rider_dlg's own default width is the sizer's narrow
# best width, which reads cramped beside its field labels; the view
# opens it no narrower than this many pixels (XRC has no window
# minsize, so _apply_min_size applies it in code). The height stays
# whatever the platform fitted. 700 is the operator's own request --
# the dialog used to open at ~1056 (its fitted ~352 scaled by 3),
# and 1056 / 1.5 rounds to 700.
ADD_RIDER_MIN_WIDTH = 700

# Phase 6 (3e): csv_preview_dlg's own default size is the conflicts
# list's narrow best size; CsvPreviewDialog._apply_min_size opens it
# this many times wider AND taller (XRC cannot give a window a
# minsize -- riders.xrc's header defers it to code). Both dimensions
# scale here, unlike add_rider_dlg's above: the preview is one list
# plus a summary line, and the width alone would only add empty
# margin.
CSV_PREVIEW_WIDTH_SCALE = 2
CSV_PREVIEW_HEIGHT_SCALE = 2

# Blank first: the Add mode's default, and the "unknown" value that
# ``sex_from_choice`` maps back to ``None``.
SEX_OPTIONS: tuple[str, ...] = ("", "M", "F")

# add_rider_dlg serves both modes (1.0.12 B2). XRC authors the Add
# title; the Edit mode retitles the same window rather than loading a
# second resource for one string.
_EDIT_TITLE = "Edit Rider"

# The two NotImplementedError messages each view class's own "wrong
# half" of RidersView raises (module docstring) -- each keeps "E3.4"
# as a substring so the already-pinned functional tests naming it
# stay valid unchanged.
_CSV_PREVIEW_NOT_IMPLEMENTED = (
    "csv_preview_dlg is decorated by CsvPreviewDialog, not RiderEditor (E3.4)"
)
_RIDER_EDITOR_NOT_IMPLEMENTED = (
    "rider_editor_dlg is decorated by RiderEditor, not CsvPreviewDialog (E3.4)"
)


def ok_button_min_size(best: tuple[int, int]) -> tuple[int, int]:
    """Return add_rider_dlg's primary-button floor for *best* (B2).

    *best* is the button's own measured best size as (width, height);
    the floor scales only the width -- a taller button would sit oddly
    in the std dialog button sizer it shares with Cancel.
    """
    width, height = best
    return (width * OK_BUTTON_SCALE, height)


def add_rider_width_floor(fitted: tuple[int, int]) -> tuple[int, int]:
    """Return add_rider_dlg's default size for a fitted *fitted*.

    *fitted* is the width/height ``Fit()`` measured for the built
    dialog. Only the width is floored (:data:`ADD_RIDER_MIN_WIDTH`):
    the form's rows are laid out vertically, so a wider window needs
    no extra height. A width already above the floor is left alone --
    the floor never narrows a window.
    """
    width, height = fitted
    return (max(ADD_RIDER_MIN_WIDTH, width), height)


def sex_from_choice(selection: str) -> str | None:
    """Return the Sex dropdown's value for *selection*.

    Phase 3's rule: the blank item means "unknown", which is ``None``
    on :class:`~rivercrossing.roster.Rider` -- never an empty string
    (the same normalization csvio's import applies).
    """
    return selection or None


def _build_infobar(dialog: wx.Dialog, name: str) -> wx.InfoBar:
    """Build the code-side InfoBar named *name*, wrapped on top.

    ``riders.xrc``'s dialogs carry no reserved InfoBar slot (unlike
    ``main.xrc``'s spacer placeholder) -- they predate the decision.
    Each dialog's existing sizer is kept alive and nested inside a
    new outer vertical one instead of edited in the frozen XRC.

    Measured (wxPython 4.3.1 / wxWidgets 3.3.3, macOS, a throwaway
    probe script per this repo's own convention): calling
    ``Dismiss()`` or ``ShowMessage()`` on a ``wx.InfoBar`` with its
    default slide effect never returns, on a dialog shown or not --
    disabling both effects here is what makes this module's own
    ``show_riders``/``show_validation`` calls safe.
    """
    bar = wx.InfoBar(dialog)
    bar.SetName(name)
    bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
    content = dialog.GetSizer()
    outer = wx.BoxSizer(wx.VERTICAL)
    outer.Add(bar, 0, wx.EXPAND)
    outer.Add(content, 1, wx.EXPAND)
    dialog.SetSizer(outer, deleteOld=False)
    return bar


def _set_team_choice_row_visible(control: wx.Window, *, visible: bool) -> None:
    """Show/hide *control* and its FlexGridSizer label sibling.

    The two Team labels ("Team" in rider_editor_dlg and add_rider_dlg)
    carry no frozen name to find them by (only the control they label
    does), so each label is located structurally: it is always the
    item immediately before its control in their shared
    ``wxFlexGridSizer`` row. The control is either dialog's own
    ``team_choice`` -- a read-only ``wx.TextCtrl`` in the editor and a
    ``wx.Choice`` in add_rider_dlg -- so the type is ``wx.Window``.
    """
    sizer = control.GetContainingSizer()
    items = list(sizer.GetChildren())
    index = next(i for i, item in enumerate(items) if item.GetWindow() is control)
    label = items[index - 1].GetWindow()
    sizer.Show(label, visible)
    sizer.Show(control, visible)


class RiderEditor:
    """Code-side behaviour for ``rider_editor_dlg`` (1d/2b, R-11/15/20).

    Implements ``RidersView`` (``ui.presenters.riders``) and
    constructs its own :class:`~rivercrossing.ui.presenters.riders.
    RidersPresenter` over *roster*, following ``views/selftest.py``'s
    presenter-inside-the-view wiring: the view stays dumb, forwarding
    every control event straight to the presenter and rendering
    whatever it is told, per module-skeletons.md's MVP split.
    """

    def __init__(self, dialog: wx.Dialog, *, roster: Roster) -> None:
        """Decorate an already-loaded ``rider_editor_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``riders.xrc``.
            roster: The in-memory :class:`~rivercrossing.roster.
                Roster` this editor reads and writes directly --
                unlike every other view in this package, never a
                ``DataSource`` projection of one.
        """
        self.dialog = dialog

        self.riders_list = self._find(ids.RIDERS_LIST, wx.dataview.DataViewCtrl)
        # The operator's current header sort, re-applied whenever the
        # presenter rebuilds the model (a new model drops the control's
        # sort key). No column sorts until a header is clicked.
        self._sort_column: int | None = None
        self._sort_ascending = True
        self._columns = self._build_columns()
        self._team_column = self._columns[COL_TEAM]
        # Replaced by the presenter's own show_riders() call below,
        # before any event can fire -- typed non-optional so
        # _on_row_selected never has to narrow it.
        self._model: RiderRowListModel = RiderRowListModel([], rider_columns.EDITOR_RIDER_COLUMNS)

        self.rider_search = self._find(ids.RIDER_SEARCH, wx.SearchCtrl)
        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.first_name_input = self._find(ids.FIRST_NAME_INPUT, wx.TextCtrl)
        self.last_name_input = self._find(ids.LAST_NAME_INPUT, wx.TextCtrl)
        # Phase 3: a read-only display of the selected record's team,
        # not a dropdown -- the Add/Edit dialog owns team assignment.
        self.team_choice = self._find(ids.TEAM_CHOICE, wx.TextCtrl)
        self.add_btn = self._find(ids.ADD_BTN, wx.Button)
        self.edit_btn = self._find(ids.EDIT_BTN, wx.Button)
        self.delete_btn = self._find(ids.DELETE_BTN, wx.Button)

        self.roster_infobar = self._build_infobar()

        self.presenter = RidersPresenter(self, roster)

        self._bind_events()
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

    def _build_columns(self) -> list[Any]:
        """Append ``riders_list``'s sortable columns in canvas order.

        The labels are the shared
        :data:`~rivercrossing.ui.rider_columns.EDITOR_RIDER_COLUMNS`
        ones (Plate | Name | Team | Sex) and the widths are
        :data:`COLUMN_WIDTHS`, so the console's own rider list draws
        the same headers at the same widths. Each column carries
        :data:`RIDERS_LIST_COLUMN_FLAGS`, so the platform draws a
        header arrow and sorts through
        :meth:`RiderRowListModel.Compare`.

        Returns:
            The appended columns in order -- the Team column (index
            :data:`COL_TEAM`) is what :meth:`set_team_ui_visible`
            hides.
        """
        return [
            self.riders_list.AppendTextColumn(
                label, col, width=COLUMN_WIDTHS[col], flags=RIDERS_LIST_COLUMN_FLAGS
            )
            for col, label in enumerate(COLUMN_LABELS)
        ]

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build :data:`ROSTER_INFOBAR`, wrapped on top of the sizer.

        See :func:`_build_infobar`'s docstring for the measured
        slide-effect hang and the shared wrapper this delegates to.
        """
        return _build_infobar(self.dialog, ROSTER_INFOBAR)

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter.

        1.0.12: the form is display-only, so the only bindings left are
        the two dialogs' openers (``add_btn``, ``edit_btn``), delete,
        the list's own search/selection/sort events -- and a row
        activation, which opens the Edit dialog exactly as ``edit_btn``
        does (the double-click/Enter shortcut the canvas asks for).
        """
        self.dialog.Bind(wx.EVT_BUTTON, self._on_add, self.add_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_edit, self.edit_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_delete, self.delete_btn)
        self.dialog.Bind(wx.EVT_TEXT, self._on_search_text, self.rider_search)
        self.dialog.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._on_search_text, self.rider_search)
        self.dialog.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_search_text, self.rider_search)
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_row_selected, self.riders_list
        )
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_row_activated, self.riders_list
        )
        # Remember the operator's header arrow, so the next show_riders
        # rebuild can put it back.
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_COLUMN_SORTED, self._on_column_sorted, self.riders_list
        )

    def _on_add(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``add_btn``: open the Add Rider dialog (W7).

        The editor's in-form add is retired (riders.xrc): the add
        dialog's own :class:`AddRiderPresenter` commits, and a real
        commit refreshes this editor's rows/form via
        :meth:`RidersPresenter.on_add_committed` -- nothing else
        would tell this open editor the roster changed underneath it.
        """
        event.Skip()
        if run_add_rider_flow(self.dialog, self.presenter.roster):
            self.presenter.on_add_committed()

    def _on_edit(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``edit_btn``: open the Edit Rider dialog (B3)."""
        event.Skip()
        self._open_edit_dialog()

    def _on_row_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``riders_list`` activation: select it, then edit it.

        Double-clicking (or pressing Enter on) a row is the shortcut to
        "Edit Rider…". The activation is resolved from the event's own
        item first -- a programmatic ``Select`` does not fire the
        selection event on MSW's native control (see
        ``select_rider_by_plate``), but an activation always names its
        row, so the selection ``_on_edit`` reads is that row's own.
        """
        event.Skip()
        item = event.GetItem()
        if item.IsOk():
            self.presenter.on_row_selected(self._model.GetRow(item))
        self._open_edit_dialog()

    def _open_edit_dialog(self) -> None:
        """Open add_rider_dlg in Edit mode over the selected record.

        A no-op when nothing is selected: there is no record to preload,
        and the dialog must never open blank. A real commit refreshes
        this editor's rows and re-shows the record through
        :meth:`RidersPresenter.on_edit_committed` -- nothing else would
        tell this open editor the roster changed underneath it.
        """
        record = self.presenter.selected
        if record is None:
            return
        entry, rider = record
        if run_edit_rider_flow(self.dialog, self.presenter.roster, editing=(entry, rider)):
            self.presenter.on_edit_committed(rider)

    def _on_delete(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``delete_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_delete()

    def _on_search_text(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a rider_search change; forward its current text.

        ``rider_search`` is a ``wxSearchCtrl``: text changes (typing,
        the harness's ``SetValue``, the native clear X) all re-run the
        filter, and the search button (Enter) does too -- every path
        reads the control's current value, so one handler serves all
        three events (the audit dialog's own precedent).
        """
        event.Skip()
        self.presenter.on_search_text(self.rider_search.GetValue())

    def _on_column_sorted(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Remember the header sort the operator just chose.

        wx's ``EVT_DATAVIEW_COLUMN_SORTED`` fires after the control has
        already reordered its rows through
        :meth:`RiderRowListModel.Compare`; this handler keeps the
        column and direction so :meth:`_apply_sort` can restore both
        after the next model rebuild.
        """
        event.Skip()
        column = self.riders_list.GetSortingColumn()
        if column is None:
            return
        self._sort_column = column.GetModelColumn()
        self._sort_ascending = column.IsSortOrderAscending()

    def _on_row_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``riders_list`` selection: forward its row index.

        Two guards, both measured-needed (B3): nothing selected is a
        stale event after the row it pointed to was deleted, and a row
        index past the model's current count is the same staleness one
        step later -- wx can deliver the event after ``show_riders``
        has already replaced the model (a search narrowing the list, a
        delete shrinking it), and a row index into the *new* model
        would then address the wrong record. Either way there is no row
        to forward.
        """
        event.Skip()
        item = self.riders_list.GetSelection()
        if not item.IsOk():
            return
        row = self._model.GetRow(item)
        if not 0 <= row < self._model.GetCount():
            return
        self.presenter.on_row_selected(row)

    def select_rider_by_plate(self, plate: str) -> None:
        """Select the riders_list row whose Plate column equals *plate*.

        The console Riders tab's preselect seam (ux-polish): the app
        bootstrap calls this after opening the editor so the operator
        lands on an existing rider's form instead of the add form. A
        no-op when no row's Plate column matches -- a stale plate from
        an earlier ride state, say.

        The form fills through the presenter's own ``on_row_selected``
        directly, never by relying on the selection event alone: a
        programmatic ``Select`` fires ``EVT_DATAVIEW_SELECTION_CHANGED``
        on macOS's generic control but not on MSW's native one
        (``harness.select_row``'s own measured note). Where macOS does
        fire the event, it re-runs the same idempotent handler.
        """
        for row in range(self._model.GetCount()):
            if self._model.GetValueByRow(row, COL_PLATE) == plate:
                self.riders_list.Select(self._model.GetItem(row))
                self.presenter.on_row_selected(row)
                return

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render ``riders_list`` (``RidersView``).

        Dismisses any prior :data:`ROSTER_INFOBAR` warning first: this
        is only ever called after a successful add/edit/delete
        refresh (``RidersPresenter``'s own call order), so the next
        successful action is exactly when a stale warning should
        clear. See ``ui.views._support.associate_model``'s docstring
        for why this also repaints explicitly (unverified remedy).
        """
        self.roster_infobar.Dismiss()
        self._model = RiderRowListModel(rows, rider_columns.EDITOR_RIDER_COLUMNS)
        associate_model(self.riders_list, self._model)
        self._apply_sort()

    def _apply_sort(self) -> None:
        """Re-apply the remembered header sort to the current model.

        ``show_riders`` replaces the model, which drops the sort key the
        control was holding; setting it on the column again and asking
        the model to resort restores exactly the order the operator
        left the list in. No column is remembered until a header is
        clicked, so the first render keeps the presenter's own order.
        The ``UnsetAsSortKey()`` is load-bearing on macOS:
        ``SetSortOrder`` is a no-op when the direction is unchanged.
        """
        if self._sort_column is None:
            return
        column = self.riders_list.GetColumn(self._sort_column)
        if column is None:
            return
        column.UnsetAsSortKey()
        column.SetSortOrder(self._sort_ascending)
        self._model.Resort()

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Toggle ``delete_btn``'s enabled state (R-15)."""
        self.delete_btn.Enable(enabled)

    def confirm(  # noqa: PLR0913 -- (title, message) + 2 button labels, mirroring std_dialogs.show_confirm
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
    ) -> bool:
        """Ask a destructive confirm; return whether OK was chosen (B3).

        The editor's own window parents the native dialog
        (:func:`~rivercrossing.ui.std_dialogs.show_confirm`, whose
        Cancel default keeps Enter off the destructive path); the
        presenter only ever reads the verdict. The same wiring
        ``MainFrame.confirm`` uses for the stop confirm.
        """
        result = std_dialogs.show_confirm(self.dialog, title, message, ok_label, cancel_label)
        return result == int(wx.ID_OK)

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Render ``csv_preview_dlg``; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``CsvPreviewDialog``
                implements this for real; ``rider_editor_dlg`` has
                no ``summary_lbl``/``conflicts_list`` of its own.
        """
        raise NotImplementedError(_CSV_PREVIEW_NOT_IMPLEMENTED)

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate ``csv_preview_dlg``'s Import; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``CsvPreviewDialog``
                implements this for real; ``rider_editor_dlg`` has
                no ``wxID_OK`` of its own.
        """
        raise NotImplementedError(_CSV_PREVIEW_NOT_IMPLEMENTED)

    def show_form(  # noqa: PLR0913 -- the passive view fills the four form fields verbatim
        self, *, plate: str, first_name: str, last_name: str, team: str
    ) -> None:
        """Fill plate_input and the two name inputs (``RidersView``).

        ``RidersView``, R-20: the passive view fills exactly what the
        presenter asks for. Phase 3: ``team_choice`` is a read-only
        text box now, so the Team field is set (``SetValue``) with the
        presenter's own cell text -- ``"— solo —"`` for a solo rider --
        rather than having a choice selected in it.
        """
        self.plate_input.SetValue(plate)
        self.first_name_input.SetValue(first_name)
        self.last_name_input.SetValue(last_name)
        self.team_choice.SetValue(team)

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide ``team_choice``, its label, and the Team column.

        ``RidersView``, R-11: a solo-only ride hides team assignment
        entirely. The label is located structurally -- see
        :func:`_set_team_choice_row_visible`'s docstring.
        """
        _set_team_choice_row_visible(self.team_choice, visible=visible)
        self._team_column.SetHidden(not visible)
        self.dialog.Layout()

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`ROSTER_INFOBAR` (``RidersView``).

        Non-modal, per the approved E3.2 decision: it stays up until
        :meth:`show_riders` dismisses it on the next successful
        action, never blocking the operator from correcting the form.
        """
        self.roster_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def _apply_min_size(self) -> None:
        """Force the W7 1280x560 floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors. W7 applies BOTH dimensions -- the
        old width-only ``-1`` height left the reworked two-pane
        layout free to collapse -- so the dialog opens at the
        canvas redraw's exact 1280x560.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], MIN_SIZE[1]))
        self.dialog.Fit()


class AddRiderDialog:
    """Code-side behaviour for ``add_rider_dlg`` (W7, R-20).

    Implements ``AddRiderView`` (``ui.presenters.riders``) over its
    own presenter -- :class:`~rivercrossing.ui.presenters.riders.
    AddRiderPresenter` when ``editing`` is ``None``, :class:`~river-
    crossing.ui.presenters.riders.EditRiderPresenter` (over the same
    view surface) when the caller passed the record to edit. The
    dialog pairs per-open like :class:`CsvPreviewDialog` (module
    docstring's mirror-image note): it never renders
    ``rider_editor_dlg``'s own rows, and a live ``RiderEditor`` sees
    the committed add/edit through the ``run_*_flow`` result, which
    refreshes the editor's own presenter.
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        *,
        roster: Roster,
        editing: tuple[Entry, Rider] | None = None,
    ) -> None:
        """Decorate an already-loaded ``add_rider_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``run_add_rider_flow`` (or
                ``run_edit_rider_flow``) loaded from ``riders.xrc``.
            roster: The in-memory roster this dialog writes into.
            editing: The ``(entry, rider)`` record to edit, or ``None``
                for the Add mode. The two modes share the whole window
                -- only the title and the presenter differ -- because
                ``riders.xrc`` carries one ``add_rider_dlg`` whose
                primary button already reads "Save".
        """
        self.dialog = dialog

        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.first_name_input = self._find(ids.FIRST_NAME_INPUT, wx.TextCtrl)
        self.last_name_input = self._find(ids.LAST_NAME_INPUT, wx.TextCtrl)
        self.team_choice = self._find(ids.TEAM_CHOICE, wx.Choice)
        # Phase 3's Sex dropdown: XRC authors an empty wxChoice (there
        # is no XRC content element in play), so its three items are
        # set here -- blank first, so an unset sex is the default.
        self.sex_choice = self._find(ids.SEX_CHOICE, wx.Choice)
        self.sex_choice.Set(list(SEX_OPTIONS))
        self.ok_btn = self._find("wxID_OK", wx.Button)
        self._widen_ok_button()

        self.add_infobar = _build_infobar(self.dialog, ADD_RIDER_INFOBAR)

        self.presenter: AddRiderPresenter | EditRiderPresenter
        if editing is None:
            self.presenter = AddRiderPresenter(self, roster)
        else:
            entry, rider = editing
            self.dialog.SetTitle(_EDIT_TITLE)
            self.presenter = EditRiderPresenter(self, roster, entry=entry, rider=rider)

        self._bind_events()
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

    def _widen_ok_button(self) -> None:
        """Give ``wxID_OK`` its 3x width floor (B2).

        XRC has no window minsize (riders.xrc's header), so the floor
        is applied here, before the sizer lays the button out. Only the
        width is floored -- see :func:`ok_button_min_size`.
        """
        best = self.ok_btn.GetBestSize()
        width, height = ok_button_min_size((best.width, best.height))
        self.ok_btn.SetMinSize(wx.Size(width, height))

    def _apply_min_size(self) -> None:
        """Open the dialog no narrower than ADD_RIDER_MIN_WIDTH.

        XRC gives a window no minsize and no default size, so the
        dialog would otherwise open at the flex grid's own narrow best
        width -- cramped beside its labels and too short for a real
        team name in ``team_choice``. ``Fit()`` first, so the width
        being floored is whatever this platform actually measured;
        the height is left alone (the form is laid out vertically).
        Only the *width* is floored: ``-1`` means "no minimum height"
        to wx, so a taller-than-fitted row can still grow the window.
        The floored width is clamped to the display's work area, so a
        window can never open wider than the screen.
        """
        self.dialog.Fit()
        fitted = self.dialog.GetSize()
        floored = add_rider_width_floor((fitted.width, fitted.height))
        width, height = clamp_to_display(*floored)
        self.dialog.SetMinSize(wx.Size(width, -1))
        self.dialog.SetSize(wx.Size(width, height))

    def _bind_events(self) -> None:
        """Forward ``wxID_OK`` ("Save") straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_submit, self.ok_btn)

    def _on_submit(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK`` ("Save"): commit, then close if it did.

        Measured: ``wxID_OK`` is a stock id wx auto-binds to
        ``EndModal(wx.ID_OK)`` on any ``EVT_BUTTON`` whose handler
        calls ``event.Skip()`` -- unlike ``add_btn``/``edit_btn``
        (plain custom ids), so *event* is never skipped here: this
        handler is the only thing allowed to decide whether the
        dialog closes (the same note CsvPreviewDialog's own
        ``_on_import`` carries). A refused save -- an add (blank name,
        duplicate plate, a full team, ...) or an edit (the ride has
        left DRAFT, ...) -- leaves the dialog open, showing why on
        :data:`ADD_RIDER_INFOBAR`, so the operator can correct or
        Cancel -- never a silent, unexplained non-close.
        """
        if self.presenter.on_submit(self._form_values()):
            self.dialog.EndModal(wx.ID_OK)

    def _form_values(self) -> RiderFormValues:
        """Return the dialog's current fields, read verbatim (R-20).

        The Sex dropdown's blank item is read as ``None``
        (:func:`sex_from_choice`) -- the value ``Rider.sex`` uses for
        an unknown sex, never an empty string.
        """
        return RiderFormValues(
            plate=self.plate_input.GetValue(),
            first_name=self.first_name_input.GetValue(),
            last_name=self.last_name_input.GetValue(),
            team=self.team_choice.GetStringSelection(),
            sex=sex_from_choice(self.sex_choice.GetStringSelection()),
        )

    # ---------------------------------------------------- AddRiderView

    def show_team_choices(self, names: list[str]) -> None:
        """Replace ``team_choice``'s content with *names* (R-20)."""
        self.team_choice.Set(names)

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide the Team row (R-11, solo-only rides have none)."""
        _set_team_choice_row_visible(self.team_choice, visible=visible)
        self.dialog.Layout()

    def set_plate_enabled(self, *, enabled: bool) -> None:
        """Toggle ``plate_input``'s editability (spec S3:46, B2)."""
        self.plate_input.Enable(enabled)

    def show_form(  # noqa: PLR0913 -- the passive view fills the five form fields verbatim
        self,
        *,
        plate: str,
        first_name: str,
        last_name: str,
        team: str,
        sex: str | None,
    ) -> None:
        """Fill all five fields (Add passes the names blank, B2).

        Phase 3: ``sex_choice``'s blank item is selected for an unset
        (*sex* ``None``) or unknown sex, its own ``"M"``/``"F"`` item
        otherwise.
        """
        self.plate_input.SetValue(plate)
        self.first_name_input.SetValue(first_name)
        self.last_name_input.SetValue(last_name)
        self.team_choice.SetStringSelection(team)
        self.sex_choice.SetStringSelection(sex if sex is not None else "")

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`ADD_RIDER_INFOBAR`.

        ``AddRiderView`` member: non-modal, mirroring the editor's
        own refusal surface -- it stays up until a real commit closes
        the dialog, never blocking the operator from correcting the
        form.
        """
        self.add_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()


class CsvConflictsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``CsvConflict`` rows, ``conflicts_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, conflicts: Sequence[CsvConflict]) -> None:
        """Wrap *conflicts* in ``preview()``'s own row order."""
        super().__init__(len(conflicts))
        self._conflicts = tuple(conflicts)

    def GetColumnCount(self) -> int:
        """Return the conflicts list's fixed two columns."""
        return len(CONFLICT_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every conflicts_list column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        conflict = self._conflicts[row]
        return str(conflict.row) if col == COL_ROW else conflict.problem


class CsvPreviewDialog:
    """Code-side behaviour for ``csv_preview_dlg`` (3e, R-21, E3.4).

    Implements ``RidersView``'s CSV trio for real (module docstring)
    over its own :class:`~rivercrossing.ui.presenters.riders.
    RidersPresenter` pairing, constructed with ``load=False`` since
    this dialog has no rider_editor_dlg controls to render.
    """

    def __init__(self, dialog: wx.Dialog, *, roster: Roster) -> None:
        """Decorate an already-loaded ``csv_preview_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``riders.xrc``.
            roster: The in-memory roster a picked file previews
                against and, on Import, commits into -- the same
                roster a live ``RiderEditor`` reads, if one happens
                to be open (module docstring's own mirror-image note).
        """
        self.dialog = dialog

        self.summary_lbl = self._find(ids.SUMMARY_LBL, wx.StaticText)
        self.conflicts_list = self._find(ids.CONFLICTS_LIST, wx.dataview.DataViewCtrl)
        self._build_columns()
        self._model: CsvConflictsListModel = CsvConflictsListModel(())

        self.ok_btn = self._find("wxID_OK", wx.Button)
        self.map_unknown_sex_chk = self._find(ids.MAP_UNKNOWN_SEX_CHK, wx.CheckBox)
        self.convert_teams_of_one_chk = self._find(ids.CONVERT_TEAMS_OF_ONE_CHK, wx.CheckBox)

        self.csv_infobar = self._build_infobar()

        self.presenter = RidersPresenter(self, roster, load=False)

        self._bind_events()
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

    def _build_columns(self) -> None:
        """Append ``conflicts_list``'s two columns in canvas order."""
        for col, label in enumerate(CONFLICT_COLUMN_LABELS):
            self.conflicts_list.AppendTextColumn(label, col)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build :data:`CSV_INFOBAR`, wrapped on top of the sizer.

        See :func:`_build_infobar`'s docstring for the measured
        slide-effect hang this mirrors.
        """
        return _build_infobar(self.dialog, CSV_INFOBAR)

    def _apply_min_size(self) -> None:
        """Open the preview twice as wide and twice as tall as fitted.

        XRC gives a window no minsize and no default size, so the
        dialog would otherwise open at the conflicts list's own narrow
        best size. ``Fit()`` first, so the size being scaled is
        whatever this platform actually measured; that same size is
        both the floor (``SetMinSize``) and the size the dialog opens
        at (``SetSize``) -- a floor alone would still let the loaded
        window keep the size XRC gave it. Phase 3 narrowed the width
        from Phase 6's three times to two; this is the both-dimensions
        mirror of :class:`AddRiderDialog`'s own width-only floor.
        """
        self.dialog.Fit()
        fitted = self.dialog.GetSize()
        width = fitted.width * CSV_PREVIEW_WIDTH_SCALE
        height = fitted.height * CSV_PREVIEW_HEIGHT_SCALE
        self.dialog.SetMinSize(wx.Size(width, height))
        self.dialog.SetSize(wx.Size(width, height))

    def _bind_events(self) -> None:
        """Bind ``wxID_OK`` and both import-option checkbox toggles."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_import, self.ok_btn)
        self.dialog.Bind(wx.EVT_CHECKBOX, self._on_map_unknown_sex_chk, self.map_unknown_sex_chk)
        self.dialog.Bind(
            wx.EVT_CHECKBOX, self._on_convert_teams_of_one_chk, self.convert_teams_of_one_chk
        )

    def _on_map_unknown_sex_chk(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Forward the sex checkbox's new state (Phase 3)."""
        event.Skip()
        self.presenter.on_toggle_map_unknown_sex(enabled=self.map_unknown_sex_chk.GetValue())

    def _on_convert_teams_of_one_chk(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Forward the teams-of-one checkbox's new state (Phase E)."""
        event.Skip()
        self.presenter.on_toggle_convert_teams_of_one(
            enabled=self.convert_teams_of_one_chk.GetValue()
        )

    def _on_import(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK`` ("Import"): commit, then close if it did.

        Measured: ``wxID_OK`` is a stock id wx auto-binds to
        ``EndModal(wx.ID_OK)`` on any ``EVT_BUTTON`` whose handler
        calls ``event.Skip()`` -- unlike ``add_btn``/``save_btn``
        (plain custom ids, ``MainFrame.wire_entry``'s own analogous
        note about ``record_btn``), so *event* is never skipped
        here: this handler is the only thing allowed to decide
        whether the dialog closes. A refused commit (module
        docstring: conflicts present after all) leaves the dialog
        open, showing why on :data:`CSV_INFOBAR`, so the operator can
        Cancel or re-pick a file -- never a silent, unexplained
        non-close.
        """
        if self.presenter.on_confirm_csv_import():
            self.dialog.EndModal(wx.ID_OK)

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Render *preview*'s summary, conflicts and warnings (R-21).

        ``conflicts`` block an import; ``warnings`` do not. Both are
        rendered in the one ``conflicts_list`` so the operator sees the
        whole pre-flight report, warnings prefixed to keep them apart
        from the blocking rows.
        """
        self.csv_infobar.Dismiss()
        self.summary_lbl.SetLabel(preview.summary)
        combined = [*preview.conflicts]
        combined.extend(
            CsvConflict(row=w.row, problem=f"⚠ warning: {w.problem}") for w in preview.warnings
        )
        self._model = CsvConflictsListModel(combined)
        associate_model(self.conflicts_list, self._model)

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate ``wxID_OK`` on *enabled* (``RidersView``, R-21)."""
        self.ok_btn.Enable(enabled)

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`CSV_INFOBAR` (``RidersView``)."""
        self.csv_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render ``riders_list``; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``RiderEditor`` implements
                this for real; ``csv_preview_dlg`` has no
                ``riders_list`` of its own.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Toggle ``delete_btn``; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` has no
                ``delete_btn`` of its own.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)

    def confirm(  # noqa: PLR0913 -- mirrors RidersView.confirm's own signature
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
    ) -> bool:
        """Ask a confirm; that dialog's own job.

        Raises:
            NotImplementedError: Always -- this dialog's wxID_OK only
                ever imports, and no RidersPresenter bound to it
                reaches the delete confirm.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)

    def show_form(  # noqa: PLR0913 -- mirrors RiderEditor.show_form's four-field signature
        self, *, plate: str, first_name: str, last_name: str, team: str
    ) -> None:
        """Fill the form fields; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` has no
                form fields of its own.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide team_choice + the Team column; that dialog's job.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` has
                neither.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)


# ---------------------------------------------------- shared csv flows
#
# The one place ``ui.app``'s own mi_import_csv/mi_export_csv route
# handlers run their picker -> preview/write flow through (E3.4's
# own follow-on "one source of truth" design constraint). Hosted
# here, not ``ui.app`` -- the obvious home, since the route handlers
# already lived there -- because a view importing ``ui.app`` back
# would create a views->app dependency cycle (the layering contract
# the import-linter's wx contract enforces); the original reason the
# comment recorded -- ``ui.app`` importing ``rivercrossing.demo``,
# which would leak through that back-import -- was retired with the
# seam itself in E5.4.2. W7 removes the editor's own import_btn/
# export_btn (riders.xrc), so these two flows now serve the File-menu
# routes alone. Not the presenter either: ``RidersPresenter`` may
# never import wx (R-71), and loading/showing ``csv_preview_dlg`` is
# unavoidably wx-touching. ``ui.app`` keeps calling these two
# functions with a deferred, function-scoped import -- the same way it
# already reaches every other view class in this package.


def _pick_import_path(parent: wx.Window) -> Path | None:
    """Ask the operator which CSV to import, or ``None`` if cancelled.

    A thin ``wx.FileDialog`` seam: tests monkeypatch this function
    itself (module-level) rather than ever driving the native picker,
    which no test in this suite can do (harness.py's own module
    docstring).
    """
    with wx.FileDialog(
        parent,
        message="Import Riders CSV",
        wildcard="CSV files (*.csv)|*.csv",
        style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
    ) as picker:
        if picker.ShowModal() != wx.ID_OK:
            return None
        return Path(picker.GetPath())


def _pick_export_path(parent: wx.Window) -> Path | None:
    """Ask the operator where to save the exported CSV, or ``None``.

    The save-mode sibling of :func:`_pick_import_path`; the same
    monkeypatch-able seam applies.
    """
    with wx.FileDialog(
        parent,
        message="Export Riders CSV",
        wildcard="CSV files (*.csv)|*.csv",
        style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
    ) as picker:
        if picker.ShowModal() != wx.ID_OK:
            return None
        return Path(picker.GetPath())


def run_csv_import_flow(parent: wx.Window, roster: Roster) -> bool:
    """Pick a CSV, preview it, let the operator Import or Cancel.

    A cancelled picker opens no window at all (task-briefs.md's own
    "cancelled picker = no dialog"). A picked path opens
    ``csv_preview_dlg`` decorated with :class:`CsvPreviewDialog`,
    already previewing it, so ``wxID_OK``'s enabled state is correct
    the moment the operator can see the dialog.

    Args:
        parent: The window to parent the native picker on, and to
            return focus to once ``csv_preview_dlg`` ends (module
            banner comment above).
        roster: The roster a clean Import commits into.

    Returns:
        Whether an import actually committed -- a caller with its
        own rows to refresh uses this to know whether to.
    """
    path = _pick_import_path(parent)
    if path is None:
        return False
    window = wx.xrc.XmlResource.Get().LoadDialog(None, ids.CSV_PREVIEW_DLG)
    if window is None:
        return False

    try:
        view = CsvPreviewDialog(window, roster=roster)
        view.presenter.on_pick_csv_import(path)
        default_button = dialogs.default_button_for(ids.CSV_PREVIEW_DLG)
        if default_button is not None:
            dialogs.set_default_button(window, default_button)
        result = dialogs.run_dialog(window, opener=parent)
    finally:
        # Fault A: construction/preview now run inside the close guard
        # -- a post-load raise (CsvPreviewDialog's _find can exhaust
        # its 25 retries under hosted-runner load) must not leave the
        # just-loaded dialog fully alive, rerun-masked until the reap
        # pin catches it.
        if not window.IsBeingDeleted():
            window.Destroy()
    ok_id: int = wx.ID_OK  # mypy: an int-typed local isolates wx's own Any
    return result == ok_id


def run_add_rider_flow(parent: wx.Window, roster: Roster) -> bool:
    """Open the Add Rider dialog; commit only if the operator Saves.

    W7's dedicated add path: ``rider_editor_dlg``'s own ``add_btn``
    handler calls this. The dialog pairs with its own
    :class:`AddRiderPresenter` instance over the same live roster
    (the module docstring's mirror-image split -- the editor's own
    ``RidersPresenter`` never writes); on a committed Add the caller
    refreshes its own rows/form through
    :meth:`~rivercrossing.ui.presenters.riders.RidersPresenter.
    on_add_committed`.

    Args:
        parent: The window to return focus to once ``add_rider_dlg``
            ends (module banner comment above).
        roster: The roster a clean Add commits into.

    Returns:
        Whether an add actually committed.
    """
    return _run_rider_form_flow(parent, roster, editing=None)


def run_edit_rider_flow(
    parent: wx.Window, roster: Roster, *, editing: tuple[Entry, Rider]
) -> bool:
    """Open the same dialog in Edit Rider… mode; commit on Save (B2).

    ``rider_editor_dlg``'s own ``edit_btn`` handler (and a
    ``riders_list`` row activation) calls this with the selected
    record. The dialog pairs with its own
    :class:`EditRiderPresenter` instance, which preloads *editing* and
    writes it back through the shared update mutators; a committed edit
    is reported to the caller through
    :meth:`~rivercrossing.ui.presenters.riders.RidersPresenter.
    on_edit_committed`.

    Args:
        parent: The window to return focus to once ``add_rider_dlg``
            ends (module banner comment above).
        roster: The roster the dialog writes into.
        editing: The ``(entry, rider)`` record to preload and edit.

    Returns:
        Whether the edit actually committed.
    """
    return _run_rider_form_flow(parent, roster, editing=editing)


def _run_rider_form_flow(
    parent: wx.Window, roster: Roster, *, editing: tuple[Entry, Rider] | None
) -> bool:
    """Load ``add_rider_dlg``, decorate it per mode and run it.

    The one body :func:`run_add_rider_flow` and
    :func:`run_edit_rider_flow` share: only ``editing`` (and so the
    title and the presenter class) differs between them.
    """
    window = wx.xrc.XmlResource.Get().LoadDialog(None, ids.ADD_RIDER_DLG)
    if window is None:
        return False
    try:
        AddRiderDialog(window, roster=roster, editing=editing)
        default_button = dialogs.default_button_for(ids.ADD_RIDER_DLG)
        if default_button is not None:
            dialogs.set_default_button(window, default_button)
        first_field = dialogs.first_field_for(ids.ADD_RIDER_DLG)
        if first_field is not None:
            dialogs.set_initial_focus(window, first_field)
        result = dialogs.run_dialog(window, opener=parent)
    finally:
        # Fault A: construction now runs inside the close guard -- a
        # post-load raise (AddRiderDialog's _find can exhaust its 25
        # retries under hosted-runner load) must not leave the
        # just-loaded dialog fully alive, rerun-masked until the reap
        # pin catches it.
        if not window.IsBeingDeleted():
            window.Destroy()
    ok_id: int = wx.ID_OK  # mypy: an int-typed local isolates wx's own Any
    return result == ok_id


def run_csv_export_flow(
    parent: wx.Window,
    roster: Roster,
    *,
    on_error: Callable[[str], None],
) -> Path | None:
    """Pick a save path, then write *roster* there as CSV (E3.4).

    The save-mode sibling of :func:`run_csv_import_flow`. A cancelled
    picker is a silent no-op. A failed write -- ``csvio.export``
    raising ``OSError`` on an unwritable target -- is caught here and
    reported through *on_error* as ``Export failed: {exc}``, never an
    unguarded raise into a wx handler that swallows it (the measured
    note ``docs/EPIC3-SESSION-SUMMARY.md`` records), which would leave
    the operator believing the export succeeded.

    Args:
        parent: The window to parent the native save picker on.
        roster: The roster to write.
        on_error: Where a failed write's ``Export failed: {exc}``
            notice goes -- the caller's own surface (the main
            frame's status bar from ``ui.app``).

    Returns:
        The path written, or ``None`` when the picker was cancelled
        or the write failed (reported through *on_error*).
    """
    path = _pick_export_path(parent)
    if path is None:
        return None
    try:
        csvio.export(roster, path)
    except OSError as exc:
        on_error(f"Export failed: {exc}")
        return None
    return path
