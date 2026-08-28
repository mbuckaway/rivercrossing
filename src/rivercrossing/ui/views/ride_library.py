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

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import wx
import wx.dataview

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import RideSummary

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
    "RidesSource",
    "format_ride_status",
]


@runtime_checkable
class RidesSource(Protocol):
    """The one read this view needs: the library's rows.

    Narrower than the full ``DataSource`` display seam on purpose: the
    ride library renders exactly one method -- ``rides()`` -- so both
    ``DemoDataSource`` and the store-backed
    ``app._StoreLibrarySource`` satisfy it, and a future library that
    needs more adds the member it actually calls (the "add the member
    once the presenter calls it" precedent main_frame.py records).
    """

    def rides(self) -> list[RideSummary]:
        """Return the library rows."""
        ...


# Real XRC name FindWindowByName resolves, but excluded from ui/ids.py
# by tools/gen_ids.py's STOCK_IDS set (spec.md §15b) -- the same
# literal views/dialogs.py repeats for the identical reason; pages.py
# is test-only and production cannot import it.
WX_ID_DELETE = "wxID_DELETE"
WX_ID_OPEN = "wxID_OPEN"
WX_ID_NEW = "wxID_NEW"

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
    writes its backup first). E5.4.1 wires the live library's other
    three buttons the same way: ``wxID_OPEN`` and ``duplicate_btn``
    are enabled only while a ride row is selected (the "no ride
    selected" disable rule the store-backed library carries over from
    Delete), ``wxID_NEW`` is always enabled, and each forwards its
    selection to the injected ``on_open``/``on_new``/``on_duplicate``
    callbacks -- the seams ``app.py`` wires to ``Store.load_engine``
    + console switch, the ride-setup flow, and ``Store.duplicate_ride``
    + :meth:`refresh`.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, data_source) + the four injected action callbacks
        self,
        dialog: wx.Dialog,
        *,
        data_source: RidesSource,
        on_delete: Callable[[str], None] | None = None,
        on_open: Callable[[RideSummary], None] | None = None,
        on_new: Callable[[], None] | None = None,
        on_duplicate: Callable[[RideSummary], None] | None = None,
    ) -> None:
        """Decorate an already-loaded ``ride_library_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``library.xrc``.
            data_source: The library's row source -- any object with a
                ``rides()`` (the :class:`RidesSource` Protocol), so
                both ``DemoDataSource`` and the store-backed source
                ``app.py`` wires in apply.
            on_delete: Called with the selected ride's name when
                ``delete_ride_dlg`` confirms a Delete; ``None`` leaves
                the dialog's confirm a no-op (the app threads a
                store-backed callback when a store is open, E5.3.2's
                module-docstring resolution).
            on_open: Called with the selected ride when Open is
                clicked; ``None`` leaves the button a no-op (the demo
                library, which has no store ride to load).
            on_new: Called when New is clicked (the app opens the ride
                setup flow); ``None`` leaves it a no-op.
            on_duplicate: Called with the selected ride when Duplicate
                is clicked; ``None`` leaves it a no-op. The view
                refreshes its rows after the callback returns so a
                successful duplicate appears immediately.
        """
        self.dialog = dialog
        self.data_source = data_source
        self._on_delete = on_delete
        self._on_open = on_open
        self._on_new = on_new
        self._on_duplicate = on_duplicate
        self._rows: tuple[RideSummary, ...] = ()
        self._selected: RideSummary | None = None

        self.rides_list = self._find(ids.RIDES_LIST, wx.dataview.DataViewCtrl)
        self.delete_button = self._find(WX_ID_DELETE, wx.Button)
        self.open_button = self._find(WX_ID_OPEN, wx.Button)
        self.new_button = self._find(WX_ID_NEW, wx.Button)
        self.duplicate_button = self._find(ids.DUPLICATE_BTN, wx.Button)
        self._build_columns()
        self._model: RidesListModel | None = None

        self.show_rides(self.data_source.rides())
        self.rides_list.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_selection_changed
        )
        self.open_button.Bind(wx.EVT_BUTTON, self._on_open_clicked)
        self.new_button.Bind(wx.EVT_BUTTON, self._on_new_clicked)
        self.duplicate_button.Bind(wx.EVT_BUTTON, self._on_duplicate_clicked)
        self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete_clicked)
        self._update_action_enablement()
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
        self._update_action_enablement()

    def refresh(self) -> None:
        """Re-read ``rides()`` and re-render the list (E5.4.1).

        The store-backed source's ``rides()`` queries the database
        live, so after ``Store.duplicate_ride`` (or a delete) a
        refresh makes the change appear immediately -- the duplicate
        flow calls this after its confirm returns.
        """
        self.show_rides(self.data_source.rides())

    # ------------------- E5.4.1 live library: Open / New / Duplicate

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
        """Track the selected ride; re-apply button enablement."""
        self._selected = self._selected_row()
        self._update_action_enablement()
        event.Skip()

    def _update_action_enablement(self) -> None:
        """Gate the library's action buttons (E5.3.2 rules).

        The library's "no ride selected" disable rule covers every
        selection-driven action: Open and Duplicate are enabled only
        while a ride row is selected, exactly as Delete is (and Delete
        additionally stays off for a RUNNING ride -- R-18, spec §3).
        New is never selection-dependent.
        """
        selected = self._selected
        self.open_button.Enable(selected is not None)
        self.duplicate_button.Enable(selected is not None)
        self.delete_button.Enable(
            selected is not None and selected.status is not RideStatus.RUNNING
        )

    def _on_open_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Forward the selected ride to ``on_open`` (E5.4.1).

        The app-side callback ends this modal and switches the console
        to the ride (``Store.load_engine`` + context switch). A
        selection is required -- the button is disabled without one,
        and this guard re-checks anyway.
        """
        selected = self._selected
        event.Skip()
        # logic-coverage-exempt: T-3 -- the button is disabled for a
        # None selection (_update_action_enablement), so a click cannot
        # carry one here; the guard keeps the open path safe by
        # construction (mirrors the delete-click guard).
        if selected is None or self._on_open is None:
            return
        self._on_open(selected)

    def _on_new_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Forward the New click to ``on_new`` (E5.4.1).

        The app-side callback ends this modal and opens the ride setup
        flow (File ▸ New Ride…'s target). Always enabled -- no
        selection needed.
        """
        event.Skip()
        if self._on_new is not None:
            self._on_new()

    def _on_duplicate_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Confirm and duplicate the selected ride, then refresh.

        Shows ``duplicate_ride_dlg`` with the ride's name in
        ``message_lbl`` (E5.4.1 mock-first), and on a confirmed
        ``wxID_OK`` invokes the injected ``on_duplicate`` -- the seam
        the app wires to ``Store.duplicate_ride`` (R-15: setup +
        roster, no timing data) -- then refreshes the list so the new
        DRAFT ride appears immediately. A selection is required (the
        button is disabled without one; this guard re-checks).
        """
        selected = self._selected
        event.Skip()
        # logic-coverage-exempt: T-3 -- the button is disabled for a
        # None selection, so a click cannot carry one here.
        if selected is None or self._on_duplicate is None:
            return
        import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

        from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- wx-touching, deferred

        dialog = wx.xrc.XmlResource.Get().LoadDialog(self.dialog, ids.DUPLICATE_RIDE_DLG)
        if dialog is None:
            # logic-coverage-exempt: T-3 -- duplicate_ride_dlg is
            # authored in dialogs.xrc and loaded before any route opens
            # the library; a None here means the resource is missing,
            # which the functional load-time verification fails on.
            return
        try:
            message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
            if message_lbl is not None:
                message_lbl.SetLabel(dialogs.duplicate_ride_message(selected.name))
            result = dialogs.run_dialog(dialog, opener=self.duplicate_button)
            if result == wx.ID_OK:
                self._on_duplicate(selected)
                self.refresh()
        finally:
            if not dialog.IsBeingDeleted():
                dialog.Destroy()

    # --------------------------------------- E5.3.2 R-18 delete surface

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
