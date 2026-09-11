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
from rivercrossing.ui.views._support import (
    apply_glass_bezel,
    associate_model,
    find_control,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import RideSummary

__all__ = [
    "COLUMN_LABELS",
    "COL_DATE",
    "COL_DATE_WIDTH",
    "COL_ENTRIES",
    "COL_ENTRIES_WIDTH",
    "COL_NAME",
    "COL_NAME_WIDTH",
    "COL_STATUS",
    "COL_STATUS_WIDTH",
    "MIN_SIZE",
    "WX_ID_CLOSE",
    "WX_ID_DELETE",
    "RideLibrary",
    "RidesListModel",
    "RidesSource",
    "format_ride_status",
    "name_column_width",
]


@runtime_checkable
class RidesSource(Protocol):
    """The one read this view needs: the library's rows.

    Narrower than the full ``DataSource`` display seam on purpose: the
    ride library renders exactly one method -- ``rides()`` -- so the
    store-backed ``app._StoreLibrarySource``, the E5.4.2
    ``EmptyDataSource`` and a test stub all satisfy it, and a future
    library that needs more adds the member it actually calls (the
    "add the member once the presenter calls it" precedent main_frame.
    py records).
    """

    def rides(self) -> list[RideSummary]:
        """Return the library rows."""
        ...


# Real XRC name FindWindowByName resolves, but excluded from ui/ids.py
# by tools/gen_ids.py's STOCK_IDS set (spec.md §15b) -- the same
# literal views/dialogs.py repeats for the identical reason; pages.py
# is test-only and production cannot import it.
WX_ID_CLOSE = "wxID_CLOSE"
WX_ID_DELETE = "wxID_DELETE"
WX_ID_OPEN = "wxID_OPEN"

COL_NAME = 0
COL_DATE = 1
COL_STATUS = 2
COL_ENTRIES = 3

# xrc-windows.md D's exact order: Ride | Date | Status | Entries.
COLUMN_LABELS: tuple[str, ...] = ("Ride", "Date", "Status", "Entries")

# D16: the canvas draws this dialog at 520x182; XRC has no window-level
# minsize (library.xrc's own header notes this and defers to code).
# W10 doubles the canvas width (520 -> 1040) and triples its height
# (182 -> 546). At the 520 px floor the four columns did not fit and
# the last one clipped; the doubled width hands the elastic Ride
# column the slack, and the tripled height stops the list collapsing
# to the sizer's own small best height. Measured (this task's own
# probe on 4.3.1 osx-cocoa): ``SetMinSize`` + ``Fit()`` honours BOTH
# dimensions at this size, so no ``SetSize`` fallback is needed.
MIN_SIZE = (1040, 546)

# W10 column-width plan. A DataViewCtrl column never sizes itself to
# its content, and (measured on 4.3.1 osx-cocoa / wxWidgets 3.3.3)
# the control stretches only its *last* column to fill the window --
# the Ride column is first, so the old 80 px default clipped the
# name at every window size while a widened dialog's slack stranded
# past Entries. Date/Status/Entries therefore carry compact fixed
# widths that their short canvas content never exceeds (measured with
# ``GetFullTextExtent`` on the stock 13 px GUI font: "2026-09-20" =
# 66 px, "REOPENED" = 59 px, the "Entries" header = 37 px; each width
# keeps ~150% text-zoom headroom), and the Ride column is elastic:
# :func:`name_column_width` gives it every pixel the compact columns
# leave, re-applied on every size event so a widened dialog widens
# the name column.
COL_DATE_WIDTH = 110
COL_STATUS_WIDTH = 110
COL_ENTRIES_WIDTH = 70

# The Ride column's floor: 208 px is the fill the canvas's own 520 px
# dialog gave it (the list's client measures ~498 px there on 4.3.1
# osx-cocoa; 498 - 110 - 110 - 70 = 208) and fits the canvas's own
# names plus the longest a duplicate creates, "GORBA EPIC 2026
# (copy)" (135 px at the stock font). W10 widened the dialog's own
# floor to MIN_SIZE's 1040 px, so this is now only what a
# not-yet-laid-out list starts from -- :func:`name_column_width`
# replaces it on the first size event.
COL_NAME_WIDTH = 208

# One width per COLUMN_LABELS entry, in canvas order: the Ride column
# starts at the elastic floor; the first size event re-fills it from
# the live client width.
_COLUMN_WIDTHS: tuple[int, ...] = (
    COL_NAME_WIDTH,
    COL_DATE_WIDTH,
    COL_STATUS_WIDTH,
    COL_ENTRIES_WIDTH,
)


def name_column_width(client_width: int) -> int:
    """Return the Ride column's width in a *client_width*-px list.

    The elastic column: the width is every pixel the three compact
    columns (Date/Status/Entries) leave, so a widened list widens the
    Ride column rather than stranding the slack past Entries. Floored
    at :data:`COL_NAME_WIDTH` so a not-yet-laid-out list (whose client
    width is still a few pixels) keeps the canvas-minimum fill instead
    of a negative width.

    Args:
        client_width: ``rides_list``'s own client width in pixels.

    Returns:
        The Ride column width: *client_width* minus the three compact
        widths, never below :data:`COL_NAME_WIDTH`.
    """
    return max(
        client_width - (COL_DATE_WIDTH + COL_STATUS_WIDTH + COL_ENTRIES_WIDTH),
        COL_NAME_WIDTH,
    )


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
    """Read-only, sortable model over ``RideSummary`` rows.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.

    :meth:`Compare` is what makes the native header arrows work: the
    control hands it two items and the model column, and the model
    answers the Ordering on the *rows* those items index.
    ``DataViewIndexListModel.GetRow`` is the item-to-row mapping.
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

    def Compare(  # noqa: PLR0913, PLR0917 -- wx's own four-argument callback shape
        self,
        item1: Any,  # noqa: ANN401 -- wx ships no stubs
        item2: Any,  # noqa: ANN401 -- wx ships no stubs
        col: int,
        ascending: bool,  # noqa: FBT001 -- wx's own callback argument
    ) -> int:
        """Return the Ordering of *item1* versus *item2* on *col*.

        The base ``DataViewIndexListModel`` already compares each text
        column's *displayed* value, so this override exists for the two
        columns whose display text is the wrong sort key. Ride compares
        case-folded (so "alpha" sorts beside "Alpha", never after
        "Zulu"), and Entries compares as a number -- its cell is
        ``str(entries)``, and text order would put "10" before "2".
        Date compares its ISO text, which is already chronological, and
        Status compares :func:`format_ride_status`, the string the cell
        shows. *ascending* is the header arrow's own direction.
        """
        first = self._rows[self.GetRow(item1)]
        second = self._rows[self.GetRow(item2)]
        if col == COL_ENTRIES:
            result = _ordering(first.entries, second.entries)
        elif col == COL_NAME:
            result = _ordering(first.name.casefold(), second.name.casefold())
        elif col == COL_STATUS:
            result = _ordering(format_ride_status(first.status), format_ride_status(second.status))
        else:  # COL_DATE -- its ISO text is already chronological
            result = _ordering(first.date, second.date)
        return result if ascending else -result


def _ordering[T: (str, int)](first: T, second: T) -> int:
    """Return -1, 0 or 1: how *first* orders against *second*."""
    if first == second:
        return 0
    return -1 if first < second else 1


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
    writes its backup first) -- with the selected ride row, then the
    view refreshes so the deleted row disappears (W10). E5.4.1 wires
    the live library's other two buttons the same way: ``wxID_OPEN``
    and ``duplicate_btn`` are enabled only while a ride row is
    selected (the "no ride selected" disable rule the store-backed
    library carries over from Delete), and each forwards its selection
    to the injected ``on_open``/``on_duplicate`` callbacks -- the seams
    ``app.py`` wires to ``Store.load_engine`` + console switch and
    ``Store.duplicate_ride`` + :meth:`refresh`. W10 removes the New
    button: the library never owned the ride-setup flow, which stays
    File ▸ New Ride…'s own route. W10 also makes the columns natively
    sortable (the model's :meth:`~RidesListModel.Compare`), keeps the
    operator's chosen sort across rebuilds, and applies the macOS-26
    ``.glass`` bezel to the dialog's buttons.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, data_source) + the three injected action callbacks
        self,
        dialog: wx.Dialog,
        *,
        data_source: RidesSource,
        on_delete: Callable[[RideSummary], None] | None = None,
        on_open: Callable[[RideSummary], None] | None = None,
        on_duplicate: Callable[[RideSummary], None] | None = None,
    ) -> None:
        """Decorate an already-loaded ``ride_library_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``library.xrc``.
            data_source: The library's row source -- any object with a
                ``rides()`` (the :class:`RidesSource` Protocol), so
                the store-backed source ``app.py`` wires in and the
                E5.4.2 ``EmptyDataSource`` both apply.
            on_delete: Called with the selected ride row when
                ``delete_ride_dlg`` confirms a Delete -- the row the
                app side deletes by its ``ride_id``, never by name
                (W10); ``None`` leaves the dialog's confirm a no-op
                (the app threads a store-backed callback when a store
                is open, E5.3.2's module-docstring resolution). The
                view refreshes its rows after the callback returns so
                a successful delete disappears immediately.
            on_open: Called with the selected ride when Open is
                clicked; ``None`` leaves the button a no-op (the empty
                library, which has no store ride to load).
            on_duplicate: Called with the selected ride when Duplicate
                is clicked; ``None`` leaves it a no-op. The view
                refreshes its rows after the callback returns so a
                successful duplicate appears immediately.
        """
        self.dialog = dialog
        self.data_source = data_source
        self._on_delete = on_delete
        self._on_open = on_open
        self._on_duplicate = on_duplicate
        self._rows: tuple[RideSummary, ...] = ()
        self._selected: RideSummary | None = None
        # The operator's current header sort, re-applied whenever the
        # model is rebuilt (a new model drops the control's sort key).
        self._sort_column: int = COL_NAME
        self._sort_ascending: bool = True

        self.rides_list = self._find(ids.RIDES_LIST, wx.dataview.DataViewCtrl)
        self.delete_button = self._find(WX_ID_DELETE, wx.Button)
        self.open_button = self._find(WX_ID_OPEN, wx.Button)
        self.duplicate_button = self._find(ids.DUPLICATE_BTN, wx.Button)
        self.close_button = self._find(WX_ID_CLOSE, wx.Button)
        self._build_columns()
        # Replaced by show_rides() below, before any event can fire --
        # typed non-optional so _apply_sort never has to narrow it.
        self._model: RidesListModel = RidesListModel([])

        self.rides_list.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_selection_changed
        )
        # Remember the operator's header arrow, so the next show_rides
        # rebuild can put it back.
        self.rides_list.Bind(wx.dataview.EVT_DATAVIEW_COLUMN_SORTED, self._on_column_sorted)
        # W10: the elastic Ride column follows the list's own width.
        self.rides_list.Bind(wx.EVT_SIZE, self._on_rides_list_resize)
        self.open_button.Bind(wx.EVT_BUTTON, self._on_open_clicked)
        self.duplicate_button.Bind(wx.EVT_BUTTON, self._on_duplicate_clicked)
        self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete_clicked)
        self.show_rides(self.data_source.rides())
        self._update_action_enablement()
        self._apply_min_size()
        # W10: the macOS-26 .glass bezel reaches the native NSButton,
        # which GetHandle() only yields once the dialog is realized --
        # hence CallAfter rather than a direct call here.
        for button in (
            self.open_button,
            self.duplicate_button,
            self.delete_button,
            self.close_button,
        ):
            wx.CallAfter(apply_glass_bezel, button)

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
        """Append ``rides_list``'s four columns in canvas order.

        Each column gets its explicit width from ``_COLUMN_WIDTHS``
        (W10): a ``wxDataViewCtrl`` column never sizes itself to its
        content, so the unpinned default clipped the Ride name at
        every window size (measured on 4.3.1 osx-cocoa: the control
        stretches only its last column, and the name is first).
        Date/Status/Entries keep their compact fixed widths; the Ride
        column starts at the elastic floor and
        :meth:`_on_rides_list_resize` re-fills it from the live
        client width on every size event.

        Every column is SORTABLE (W10 -- the native header arrow,
        answered by :meth:`RidesListModel.Compare`) and RESIZABLE. An
        explicit ``flags`` argument *replaces* ``AppendTextColumn``'s
        RESIZABLE default rather than being OR'd with it (wxWidgets
        3.3.3 ``dataview.h``: explicit flags are never merged), so
        both are spelled out -- on macOS a SORTABLE-only column is
        actively set ``NSTableColumnNoResizing``.
        """
        for col, label in enumerate(COLUMN_LABELS):
            self.rides_list.AppendTextColumn(
                label,
                col,
                width=_COLUMN_WIDTHS[col],
                flags=wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE,
            )

    def _on_rides_list_resize(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Re-fill the Ride column after this resize settles (W10).

        Deferred through ``wx.CallAfter``: the native layout pass for
        a resize runs after this handler returns, and a synchronous
        ``SetWidth`` here is overwritten by the control's own
        last-column stretch. The deferred call runs after that pass,
        so the pinned widths win and the Ride column takes the exact
        leftover (measured on 4.3.1 osx-cocoa).
        """
        event.Skip()
        wx.CallAfter(self._stretch_name_column)

    def _stretch_name_column(self) -> None:
        """Give the Ride column every pixel the compact columns leave.

        Re-pins the Entries column too: the native last-column stretch
        moves it on every resize, and the Ride column's fill is exact
        only while the other three widths are the pinned ones.
        """
        client_width = self.rides_list.GetClientSize().GetWidth()
        self.rides_list.GetColumn(COL_ENTRIES).SetWidth(COL_ENTRIES_WIDTH)
        self.rides_list.GetColumn(COL_NAME).SetWidth(name_column_width(client_width))

    def _apply_sort(self) -> None:
        """Re-apply the remembered header sort to the current model.

        ``show_rides`` replaces the model, which drops the sort key the
        control was holding; setting it on the column again and asking
        the model to resort restores exactly the order the operator
        left the list in (the team editor's own shape).
        """
        column = self.rides_list.GetColumn(self._sort_column)
        if column is None:
            # logic-coverage-exempt: T-3 -- _sort_column is always a
            # real column's GetModelColumn(), so this lookup cannot
            # miss; the guard keeps the resort total (the team editor's
            # own arm, kept for parity).
            return
        column.SetSortOrder(self._sort_ascending)
        self._model.Resort()

    def show_rides(self, rows: list[RideSummary]) -> None:
        """Render ``rides_list`` (``LibraryView``).

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._rows = tuple(rows)
        self._selected = None
        self._model = RidesListModel(rows)
        associate_model(self.rides_list, self._model)
        self._apply_sort()
        self._update_action_enablement()

    def refresh(self) -> None:
        """Re-read ``rides()`` and re-render the list (E5.4.1).

        The store-backed source's ``rides()`` queries the database
        live, so after ``Store.duplicate_ride`` (or a delete) a
        refresh makes the change appear immediately -- the duplicate
        flow calls this after its confirm returns.
        """
        self.show_rides(self.data_source.rides())

    # ------------------------- E5.4.1 live library: Open / Duplicate

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

    def _on_column_sorted(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Remember the header sort the operator just chose (W10)."""
        event.Skip()
        column = self.rides_list.GetSortingColumn()
        if column is None:
            # logic-coverage-exempt: T-3 -- this event fires from a
            # header click, which always carries a sorting column; the
            # guard only covers wx's documented "nothing is sorted"
            # state, which the library's own rebuilds never reach.
            return
        self._sort_column = column.GetModelColumn()
        self._sort_ascending = column.IsSortOrderAscending()

    def _update_action_enablement(self) -> None:
        """Gate the library's action buttons (E5.3.2 rules).

        The library's "no ride selected" disable rule covers every
        selection-driven action: Open and Duplicate are enabled only
        while a ride row is selected, exactly as Delete is (and Delete
        additionally stays off for a RUNNING ride -- R-18, spec §3).
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

    def _on_duplicate_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Confirm and duplicate the selected ride, then refresh.

        H2: asks through the native ``std_dialogs.show_prompt`` with
        the ride's name in the message (E5.4.1 mock-first), and on a
        confirmed ``wxID_OK`` invokes the injected ``on_duplicate`` --
        the seam the app wires to ``Store.duplicate_ride`` (R-15:
        setup + roster, no timing data) -- then refreshes the list so
        the new DRAFT ride appears immediately. The question is
        non-destructive, so OK is the prompt's default button. A
        selection is required (the button is disabled without one;
        this guard re-checks).
        """
        selected = self._selected
        event.Skip()
        # logic-coverage-exempt: T-3 -- the button is disabled for a
        # None selection, so a click cannot carry one here.
        if selected is None or self._on_duplicate is None:
            return
        from rivercrossing.ui import std_dialogs  # noqa: PLC0415 -- wx-touching, deferred
        from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- wx-touching, deferred

        result = std_dialogs.show_prompt(
            self.dialog,
            "Duplicate Ride",
            dialogs.duplicate_ride_message(selected.name),
            "Duplicate",
            "Cancel",
        )
        if result == wx.ID_OK:
            self._on_duplicate(selected)
            self.refresh()

    # --------------------------------------- E5.3.2 R-18 delete surface

    def _on_delete_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Open ``delete_ride_dlg`` for the selected ride (R-18).

        Interpolates the ride's name into ``message_lbl`` (UX-DESKTOP
        §4 -- a blank label is a failed assertion), arms the
        type-to-confirm gate, and on a confirmed ``wxID_DELETE``
        invokes the injected ``on_delete`` callback -- the seam E5.4
        wires to ``Store.delete_ride``, which writes its backup first
        -- with the selected ride row, then refreshes the list so the
        deleted row disappears immediately (W10, mirroring the
        duplicate flow's own post-action refresh). A RUNNING selection
        (or none) cannot reach here: the button is disabled, and this
        guard re-checks anyway.
        """
        selected = self._selected
        event.Skip()
        # logic-coverage-exempt: T-3 -- both True arms are defensive:
        # the Delete button is disabled for None/RUNNING selections
        # (_update_action_enablement), so a click cannot carry one here;
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
                self._on_delete(selected)
                self.refresh()
        finally:
            if not dialog.IsBeingDeleted():
                dialog.Destroy()

    def _apply_min_size(self) -> None:
        """Force :data:`MIN_SIZE`, then Fit() the rest (D16, W10).

        ``SetMinSize`` alone only stops *future* shrinking; ``Fit()``
        is what actually grows the dialog to respect it right now.
        W10 pins BOTH dimensions (the old call passed ``-1`` for the
        height, so only the width was floored and the list collapsed
        to the sizer's own small best height); ``Fit()`` honours both
        -- measured on this task's own probe on 4.3.1 osx-cocoa, so no
        ``SetSize`` fallback is needed. ``library.xrc``'s header
        anticipates exactly this: "Code re-applies SetMinSize() if a
        screen-fit minimum is ever specified".
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], MIN_SIZE[1]))
        self.dialog.Fit()
