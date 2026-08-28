# SPDX-License-Identifier: GPL-3.0-only
"""``RideLibrary``: ``ride_library_dlg`` (1g), the ride list (E1.5.2).

xrc-windows.md section D's code-side footnote puts ``rides_list``'s
columns and rows in code -- ``library.xrc``'s own header explains why
(``wxDataViewListCtrl`` would overwrite the frozen name). This module
is that binding, following the pattern ``views/main_frame.py``
already established for ``crossings_list``: a ``wx.dataview.
DataViewCtrl`` shell from XRC, a code-side ``DataViewIndexListModel``
subclass, and a plain Python class that decorates the already-loaded
dialog.

``_find``'s control lookup used to be duplicated across each of
these view modules, plus ``main_frame.py``. Both it and
``main_frame.py``'s card-imagelist cache now live in one shared
home, ``ui.views._support`` -- see that module's docstring.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, RideSummary

__all__ = [
    "COLUMN_LABELS",
    "COL_DATE",
    "COL_ENTRIES",
    "COL_NAME",
    "COL_STATUS",
    "MIN_SIZE",
    "WX_ID_DELETE",
    "RideLibrary",
    "RidesListModel",
    "format_ride_status",
]

# Real XRC name FindWindowByName resolves, but excluded from ui/ids.py
# by tools/gen_ids.py's STOCK_IDS set (spec.md §15b) -- the same
# literal views/dialogs.py repeats for the identical reason; pages.py
# is test-only and production cannot import it.
WX_ID_DELETE = "wxID_DELETE"

COL_NAME = 0
COL_DATE = 1
COL_STATUS = 2
COL_ENTRIES = 3

# xrc-windows.md D's exact order: Ride | Date | Status | Entries.
COLUMN_LABELS: tuple[str, ...] = ("Ride", "Date", "Status", "Entries")

# D16: the canvas draws this dialog at 520px; XRC has no window-level
# minsize (library.xrc's own header notes this and defers to code).
# Height is Fit()'s own measurement of the real, demo-populated
# sizer content, not a second canvas number -- see this task's own
# report for how it was measured.
MIN_SIZE = (520, 182)


def format_ride_status(status: RideStatus) -> str:
    """Return *status*'s ``rides_list`` display text.

    Upper-case, matching the canvas ("RUNNING", "FINISHED") and the
    same convention ``MainFrame.set_state`` already uses for
    ``ride_status_lbl``.
    """
    return status.value.upper()


_TEXT_ACCESSORS: tuple[Callable[[RideSummary], str], ...] = (
    lambda ride: ride.name,
    lambda ride: ride.date,
    lambda ride: format_ride_status(ride.status),
    lambda ride: str(ride.entries),
)


class RidesListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``RideSummary`` rows for ``rides_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[RideSummary]) -> None:
        """Wrap *rows* in the ride library's canvas order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the library's fixed four columns."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every ``rides_list`` column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


class RideLibrary:
    """Code-side behaviour for ``ride_library_dlg`` (1g).

    Implements ``LibraryView.show_rides`` (module-skeletons.md's
    presenter contract). E5.3.2 adds the R-18 delete surface: the
    library's ``wxID_DELETE`` button stays disabled while nothing is
    selected and for a RUNNING selected ride (never deletable, spec
    §3), and a click on it opens ``delete_ride_dlg`` with the ride's
    name interpolated into ``message_lbl`` and the type-to-confirm
    gate armed; a confirmed Delete invokes the injected ``on_delete``
    callback -- the seam E5.4 wires to ``Store.delete_ride`` (which
    writes its backup first). Row selection and the Open/New/
    Duplicate actions ``LibraryPresenter`` will drive are a later
    phase's job (E5.4.1) and are not in this task's scope.
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        *,
        data_source: DataSource,
        on_delete: Callable[[str], None] | None = None,
    ) -> None:
        """Decorate an already-loaded ``ride_library_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``library.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
            on_delete: Called with the selected ride's name when
                ``delete_ride_dlg`` confirms a Delete; ``None`` leaves
                the dialog's confirm a no-op (the app threads a
                store-backed callback when a store is open, E5.3.2's
                module-docstring resolution).
        """
        self.dialog = dialog
        self.data_source = data_source
        self._on_delete = on_delete
        self._rows: tuple[RideSummary, ...] = ()
        self._selected: RideSummary | None = None

        self.rides_list = self._find(ids.RIDES_LIST, wx.dataview.DataViewCtrl)
        self.delete_button = self._find(WX_ID_DELETE, wx.Button)
        self._build_columns()
        self._model: RidesListModel | None = None

        self.show_rides(self.data_source.rides())
        self.rides_list.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_selection_changed
        )
        self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete_clicked)
        self._update_delete_enablement()
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
        """Append ``rides_list``'s four columns in canvas order."""
        for col, label in enumerate(COLUMN_LABELS):
            self.rides_list.AppendTextColumn(label, col)

    def show_rides(self, rows: list[RideSummary]) -> None:
        """Render ``rides_list`` (``LibraryView``).

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._rows = tuple(rows)
        self._selected = None
        self._model = RidesListModel(rows)
        associate_model(self.rides_list, self._model)
        self._update_delete_enablement()

    # --------------------------------------- E5.3.2 R-18 delete surface

    def _selected_row(self) -> RideSummary | None:
        """Return the currently selected ride row, or None.

        ``GetSelection()`` returns an invalid item when nothing is
        selected; ``GetRow`` on the model maps a valid item back to
        its index.
        """
        item = self.rides_list.GetSelection()
        if not item.IsOk():
            return None
        row = int(self.rides_list.GetModel().GetRow(item))
        if 0 <= row < len(self._rows):
            return self._rows[row]
        # logic-coverage-exempt: T-3 -- a valid selection's GetRow index
        # is always in the model's row count; the fallback exists only
        # to keep the return type total (defensive, untestable via the
        # library's own selection events).
        return None

    def _on_selection_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Track the selected ride; re-apply Delete enablement."""
        self._selected = self._selected_row()
        self._update_delete_enablement()
        event.Skip()

    def _update_delete_enablement(self) -> None:
        """Gate the library's Delete button (R-18, spec §3).

        Enabled only for a selected, non-RUNNING ride: with nothing
        selected there is nothing to delete, and a RUNNING ride is
        never deletable.
        """
        selected = self._selected
        self.delete_button.Enable(
            selected is not None and selected.status is not RideStatus.RUNNING
        )

    def _on_delete_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Open ``delete_ride_dlg`` for the selected ride (R-18).

        Interpolates the ride's name into ``message_lbl`` (UX-DESKTOP
        §4 -- a blank label is a failed assertion), arms the
        type-to-confirm gate, and on a confirmed ``wxID_DELETE``
        invokes the injected ``on_delete`` callback -- the seam E5.4
        wires to ``Store.delete_ride``, which writes its backup first.
        A RUNNING selection (or none) cannot reach here: the button is
        disabled, and this guard re-checks anyway.
        """
        selected = self._selected
        event.Skip()
        # logic-coverage-exempt: T-3 -- both True arms are defensive:
        # the Delete button is disabled for None/RUNNING selections
        # (_update_delete_enablement), so a click cannot carry one here;
        # the re-check keeps the open path safe by construction.
        if selected is None or selected.status is RideStatus.RUNNING:
            return
        import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

        from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- wx-touching, deferred

        dialog = wx.xrc.XmlResource.Get().LoadDialog(self.dialog, ids.DELETE_RIDE_DLG)
        if dialog is None:
            # logic-coverage-exempt: T-3 -- delete_ride_dlg is authored
            # in library.xrc and loaded before any route opens the
            # library; a None here means the resource is missing, which
            # the functional load-time verification already fails on.
            return
        try:
            message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
            if message_lbl is not None:
                message_lbl.SetLabel(dialogs.delete_ride_message(selected.name))
            dialogs.bind_delete_confirmation_gate(dialog, selected.name)
            delete_button = wx.Window.FindWindowByName(WX_ID_DELETE, dialog)
            if delete_button is not None:
                # wxID_DELETE is not one of the ids wx auto-binds to
                # end a modal (harness.py's measured note), so the
                # confirmed Delete ends the dialog with its own id.
                delete_button.Bind(wx.EVT_BUTTON, lambda click: dialog.EndModal(click.GetId()))
            # logic-coverage-exempt: T-3 -- message_lbl and wxID_DELETE
            # are frozen names in delete_ride_dlg's XRC (pages.py lists
            # both), so the None arms above are unreachable defensive
            # guards for wx's name lookup; the dialogs tests already
            # cover the negative control-lookup path.
            result = dialogs.run_dialog(dialog, opener=self.dialog)
            if result == wx.ID_DELETE and self._on_delete is not None:
                self._on_delete(selected.name)
        finally:
            if not dialog.IsBeingDeleted():
                dialog.Destroy()

    def _apply_min_size(self) -> None:
        """Force the canvas's 520px floor, then Fit() the rest (D16).

        ``SetMinSize`` alone only stops *future* shrinking; ``Fit()``
        is what actually grows the dialog to respect it right now --
        measured to honour an already-set minimum width (this task's
        own probe script; ``library.xrc``'s header anticipates
        exactly this: "Code re-applies SetMinSize() if a screen-fit
        minimum is ever specified").
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()
