# SPDX-License-Identifier: GPL-3.0-only
"""``RiderEditor``: ``rider_editor_dlg`` (1d/2b), live on a real Roster.

E1.5.2 wired this dialog to a display-only ``DataSource`` projection
of the demo rows. E3.2.1/E3.2.2 replaced that with a real, in-memory
:class:`~rivercrossing.roster.Roster` that ``RidersPresenter`` (``ui.
presenters.riders``) reads and writes directly -- this module is the
matching wx half: it now takes ``roster=`` instead of ``data_source=``,
constructs ``RidersPresenter`` itself (mirroring ``views/selftest.py``'s
own presenter-inside-the-view wiring), and binds Add/Save/Delete/row
selection to it. ``show_csv_preview``/``set_import_enabled`` exist only
to satisfy the ``RidersView`` Protocol -- ``csv_preview_dlg`` itself is
wired in E3.4.

xrc-windows.md section C's code-side footnote puts ``riders_list``'s
rows, its Team column's solo-only visibility, ``team_choice``'s
content and ``delete_btn``'s has-data gate in code -- ``riders.xrc``'s
own header explains why (``wxDataViewListCtrl`` would overwrite the
frozen name). A duplicate-plate or other refused operation renders as
a code-side ``wxInfoBar`` named :data:`ROSTER_INFOBAR` -- the same
measured pattern ``views/main_frame.py``'s ``_build_infobar`` uses,
except ``riders.xrc``'s already-authored top sizer has no reserved
InfoBar slot (it predates this decision), so the InfoBar is wrapped
around the existing sizer instead of inserted into it.

``_find`` is shared via ``ui.views._support.find_control`` -- see
that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.riders import RiderFormValues, RidersPresenter
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import RiderRow
    from rivercrossing.ui.presenters.riders import CsvPreview

__all__ = [
    "COLUMN_LABELS",
    "COL_NAME",
    "COL_PLATE",
    "COL_TEAM",
    "MIN_SIZE",
    "ROSTER_INFOBAR",
    "SOLO_TEAM_TEXT",
    "RiderEditor",
    "RidersListModel",
    "format_team",
]

COL_PLATE = 0
COL_NAME = 1
COL_TEAM = 2

# xrc-windows.md C's exact column order: "Plate | Name | Team".
COLUMN_LABELS: tuple[str, ...] = ("Plate", "Name", "Team")

# The canvas's own dash for a solo rider's Team cell
# ("123 Sam Ellis —").
SOLO_TEAM_TEXT = "—"

# ui/ids.py is generated from the .xrc files (R-05); this name never
# appears there since XRC cannot author a wxInfoBar at all
# (xrc-windows.md's own code-side footnote, main_frame.py's precedent).
ROSTER_INFOBAR = "roster_infobar"

# D16: the canvas draws this dialog at 640px; XRC has no window-level
# minsize (riders.xrc's own header notes this and defers to code).
# Height is Fit()'s own measurement of the real, demo-populated
# sizer content -- see this task's own report for how it was measured.
MIN_SIZE = (640, 281)


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
        self.name_input = self._find(ids.NAME_INPUT, wx.TextCtrl)
        self.team_choice = self._find(ids.TEAM_CHOICE, wx.Choice)
        self.add_btn = self._find(ids.ADD_BTN, wx.Button)
        self.save_btn = self._find(ids.SAVE_BTN, wx.Button)
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
            name=self.name_input.GetValue(),
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
        """Render ``csv_preview_dlg``; not this task's own scope.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` itself
                is wired in task E3.4.
        """
        raise NotImplementedError("csv_preview_dlg wiring lands in task E3.4")

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate ``csv_preview_dlg``'s Import; not this task's own scope.

        Raises:
            NotImplementedError: Always -- ``csv_preview_dlg`` itself
                is wired in task E3.4.
        """
        raise NotImplementedError("csv_preview_dlg wiring lands in task E3.4")

    def show_form(self, *, plate: str, name: str, team: str) -> None:
        """Fill plate_input/name_input/team_choice (R-20)."""
        self.plate_input.SetValue(plate)
        self.name_input.SetValue(name)
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
