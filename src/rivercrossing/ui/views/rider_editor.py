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
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx
import wx.dataview
import wx.xrc

from rivercrossing import csvio
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.riders import RiderFormValues, RidersPresenter
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import RiderRow
    from rivercrossing.ui.presenters.riders import CsvConflict, CsvPreview

__all__ = [
    "COLUMN_LABELS",
    "COL_NAME",
    "COL_PLATE",
    "COL_PROBLEM",
    "COL_ROW",
    "COL_TEAM",
    "CONFLICT_COLUMN_LABELS",
    "CSV_INFOBAR",
    "MIN_SIZE",
    "ROSTER_INFOBAR",
    "SOLO_TEAM_TEXT",
    "CsvConflictsListModel",
    "CsvPreviewDialog",
    "RiderEditor",
    "RidersListModel",
    "format_team",
    "run_csv_export_flow",
    "run_csv_import_flow",
]

COL_PLATE = 0
COL_NAME = 1
COL_TEAM = 2

# xrc-windows.md C's exact column order: "Plate | Name | Team".
COLUMN_LABELS: tuple[str, ...] = ("Plate", "Name", "Team")

COL_ROW = 0
COL_PROBLEM = 1

# xrc-windows.md C's csv_preview_dlg mock: "Row | Problem".
CONFLICT_COLUMN_LABELS: tuple[str, ...] = ("Row", "Problem")

# The canvas's own dash for a solo rider's Team cell
# ("123 Sam Ellis —").
SOLO_TEAM_TEXT = "—"

# ui/ids.py is generated from the .xrc files (R-05); these two names
# never appear there since XRC cannot author a wxInfoBar at all
# (xrc-windows.md's own code-side footnote, main_frame.py's precedent).
ROSTER_INFOBAR = "roster_infobar"
CSV_INFOBAR = "csv_infobar"

# D16: the canvas draws this dialog at 640px; XRC has no window-level
# minsize (riders.xrc's own header notes this and defers to code).
# Height is Fit()'s own measurement of the real sizer content -- see
# this task's own report for how it was measured.
MIN_SIZE = (640, 281)

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


def format_team(row: RiderRow) -> str:
    """Return *row*'s ``riders_list`` Team cell text.

    ``RiderRow.team`` is ``None`` for a solo rider; the canvas draws
    an em dash rather than a blank cell.
    """
    return row.team if row.team is not None else SOLO_TEAM_TEXT


_TEXT_ACCESSORS: tuple[Callable[[RiderRow], str], ...] = (
    lambda rider: rider.plate,
    lambda rider: rider.name,
    format_team,
)


class RidersListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``RiderRow`` rows for ``riders_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[RiderRow]) -> None:
        """Wrap *rows* in the rider editor's canvas order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the roster's fixed three columns."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every ``riders_list`` column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


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
        self._team_column = self._build_columns()
        # Replaced by the presenter's own show_riders() call below,
        # before any event can fire -- typed non-optional so
        # _on_row_selected never has to narrow it.
        self._model: RidersListModel = RidersListModel([])

        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.first_name_input = self._find(ids.FIRST_NAME_INPUT, wx.TextCtrl)
        self.last_name_input = self._find(ids.LAST_NAME_INPUT, wx.TextCtrl)
        self.team_choice = self._find(ids.TEAM_CHOICE, wx.Choice)
        self.add_btn = self._find(ids.ADD_BTN, wx.Button)
        self.save_btn = self._find(ids.SAVE_BTN, wx.Button)
        self.delete_btn = self._find(ids.DELETE_BTN, wx.Button)
        self.import_btn = self._find(ids.IMPORT_BTN, wx.Button)
        self.export_btn = self._find(ids.EXPORT_BTN, wx.Button)

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

    def _build_columns(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Append ``riders_list``'s three columns in canvas order.

        Returns:
            The Team column object, for :meth:`set_team_ui_visible`
            to hide or show per the presenter's own R-11 decision.
        """
        columns = [
            self.riders_list.AppendTextColumn(label, col)
            for col, label in enumerate(COLUMN_LABELS)
        ]
        return columns[COL_TEAM]

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`ROSTER_INFOBAR`, wrapped on top.

        ``riders.xrc``'s already-authored top sizer is a plain
        ``wxBoxSizer`` with no reserved InfoBar slot (unlike
        ``main.xrc``'s spacer placeholder) -- it predates this
        decision. The existing sizer is kept alive and nested inside
        a new outer vertical one instead of edited in the frozen XRC.

        Measured (wxPython 4.3.1 / wxWidgets 3.3.3, macOS, a throwaway
        probe script per this repo's own convention): calling
        ``Dismiss()`` or ``ShowMessage()`` on a ``wx.InfoBar`` with its
        default slide effect never returns, on a dialog shown or not
        -- disabling both effects here is what makes this editor's own
        :meth:`show_riders`/:meth:`show_validation` calls safe.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(ROSTER_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_add, self.add_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_save, self.save_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_delete, self.delete_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_import_click, self.import_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_export_click, self.export_btn)
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_row_selected, self.riders_list
        )

    def _form_values(self) -> RiderFormValues:
        """Return the form's current fields, read verbatim (R-20).

        ``team_choice``'s selection is forwarded as-is, never
        translated here -- the passive-view contract
        ``RiderFormValues`` itself documents.
        """
        return RiderFormValues(
            plate=self.plate_input.GetValue(),
            first_name=self.first_name_input.GetValue(),
            last_name=self.last_name_input.GetValue(),
            team=self.team_choice.GetStringSelection(),
        )

    def _on_add(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``add_btn``: forward the form to the presenter."""
        event.Skip()
        self.presenter.on_add(self._form_values())

    def _on_save(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``save_btn``: forward the form to the presenter."""
        event.Skip()
        self.presenter.on_save(self._form_values())

    def _on_delete(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``delete_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_delete()

    def _on_import_click(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``import_btn``: the identical flow as mi_import_csv.

        On an actual commit, refreshes this still-open editor's own
        rows/team_choice via :meth:`RidersPresenter.refresh` --
        :func:`run_csv_import_flow` commits through ``csv_preview_
        dlg``'s own, *different* ``RidersPresenter`` instance
        (module docstring's mirror-image split), so nothing else
        would tell this open editor the roster changed underneath it.
        """
        event.Skip()
        if run_csv_import_flow(self.dialog, self.presenter.roster):
            self.presenter.refresh()

    def _on_export_click(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``export_btn``: the same flow as mi_export_csv."""
        event.Skip()
        run_csv_export_flow(self.dialog, self.presenter.roster)

    def _on_row_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``riders_list`` selection: forward its row index.

        No-op when nothing is selected (a stale event after a row it
        pointed to was deleted, say) -- there is no row index to
        forward the presenter could act on.
        """
        event.Skip()
        item = self.riders_list.GetSelection()
        if not item.IsOk():
            return
        row = self._model.GetRow(item)
        self.presenter.on_row_selected(row)

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render ``riders_list`` (``RidersView``).

        Dismisses any prior :data:`ROSTER_INFOBAR` warning first: this
        is only ever called after a successful add/save/delete
        refresh (``RidersPresenter``'s own call order), so the next
        successful action is exactly when a stale warning should
        clear. See ``ui.views._support.associate_model``'s docstring
        for why this also repaints explicitly (unverified remedy).
        """
        self.roster_infobar.Dismiss()
        self._model = RidersListModel(rows)
        associate_model(self.riders_list, self._model)

    def show_team_choices(self, names: list[str]) -> None:
        """Replace ``team_choice``'s content with *names* (R-20)."""
        self.team_choice.Set(names)

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Toggle ``delete_btn``'s enabled state (R-15)."""
        self.delete_btn.Enable(enabled)

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
        presenter asks for.
        """
        self.plate_input.SetValue(plate)
        self.first_name_input.SetValue(first_name)
        self.last_name_input.SetValue(last_name)
        self.team_choice.SetStringSelection(team)

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide ``team_choice``, its label, and the Team column.

        ``RidersView``, R-11: a solo-only ride hides team assignment
        entirely. ``riders.xrc``'s "Team" label carries no frozen
        name to find it by (only ``team_choice`` itself does), so its
        sizer item is located structurally instead: it is always the
        item immediately before ``team_choice`` in their shared
        ``wxFlexGridSizer`` row.
        """
        sizer = self.team_choice.GetContainingSizer()
        items = list(sizer.GetChildren())
        index = next(i for i, item in enumerate(items) if item.GetWindow() is self.team_choice)
        label = items[index - 1].GetWindow()
        sizer.Show(label, visible)
        sizer.Show(self.team_choice, visible)
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

    def prompt_new_team_name(self) -> str | None:
        """Ask for a new team's name via a native prompt (R-20).

        ``wx.TextEntryDialog``, per the approved E3.2 decision --
        ``riders.xrc`` authors no such dialog of its own. Returns
        ``None`` if the operator cancels, exactly the seam functional
        tests monkeypatch rather than drive.
        """
        with wx.TextEntryDialog(self.dialog, "Team name:", "New team…") as prompt:
            if prompt.ShowModal() != wx.ID_OK:
                return None
            return str(prompt.GetValue())

    def _apply_min_size(self) -> None:
        """Force the canvas's 640px floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()


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

        self.csv_infobar = self._build_infobar()

        self.presenter = RidersPresenter(self, roster, load=False)

        self._bind_events()

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
        """Build the code-side :data:`CSV_INFOBAR`, wrapped on top.

        See :meth:`RiderEditor._build_infobar`'s docstring for the
        measured slide-effect hang this mirrors -- the reason it
        disables both show/hide effects too.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(CSV_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _bind_events(self) -> None:
        """Forward ``wxID_OK`` straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_import, self.ok_btn)

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
        """Render *preview*'s summary and conflicts (``RidersView``)."""
        self.csv_infobar.Dismiss()
        self.summary_lbl.SetLabel(preview.summary)
        self._model = CsvConflictsListModel(preview.conflicts)
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

    def show_team_choices(self, names: list[str]) -> None:
        """Replace ``team_choice``'s content; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` has no
                ``team_choice`` of its own.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Toggle ``delete_btn``; that dialog's own job.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` has no
                ``delete_btn`` of its own.
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

    def prompt_new_team_name(self) -> str | None:
        """Ask for a new team's name; that dialog's own job.

        Raises:
            NotImplementedError: Always -- only ``on_add`` (never
                called on this pairing) would ever need it.
        """
        raise NotImplementedError(_RIDER_EDITOR_NOT_IMPLEMENTED)


# ---------------------------------------------------- shared csv flows
#
# The one place both ``ui.app``'s own mi_import_csv/mi_export_csv
# route handlers and RiderEditor's own import_btn/export_btn run
# their picker -> preview/write flow through (E3.4's own follow-on
# "one source of truth" design constraint). Hosted here, not
# ``ui.app`` -- the obvious home, since the route handlers already
# lived there -- because a view importing ``ui.app`` back would create
# a views->app dependency cycle (the layering contract the import-
# linter's wx contract enforces); the original reason the comment
# recorded -- ``ui.app`` importing ``rivercrossing.demo``, which would
# leak through that back-import -- was retired with the seam itself in
# E5.4.2. Not the presenter either: ``RidersPresenter`` may never
# import wx (R-71), and loading/showing ``csv_preview_dlg`` is
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
        own rows to refresh (:class:`RiderEditor`'s own
        ``import_btn``) uses this to know whether to.
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


def run_csv_export_flow(parent: wx.Window, roster: Roster) -> Path | None:
    """Pick a save path, then write *roster* there as CSV (E3.4).

    The save-mode sibling of :func:`run_csv_import_flow`. A cancelled
    picker is a silent no-op.

    Returns:
        The path written, or ``None`` if the picker was cancelled.
    """
    path = _pick_export_path(parent)
    if path is None:
        return None
    csvio.export(roster, path)
    return path
