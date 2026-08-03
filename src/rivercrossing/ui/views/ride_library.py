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

**Why no shared ``_find``/``CardImageList`` helper module (SIMPLECODE
Rule 7):** this task's file batch is exactly four view modules plus
their tests, with ``views/main_frame.py`` itself off limits to edit.
Each of the four views below duplicates the same small,
address-reuse-safe control lookup ``MainFrame._find`` already proved
out. Extracting a shared helper is a real, worthwhile follow-up once
a batch permits touching more than one view file at a time; doing it
here would mean editing a file outside this task's batch.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import ids

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ride import RideStatus
    from rivercrossing.ui.presenters.data_source import DataSource, RideSummary

__all__ = [
    "COLUMN_LABELS",
    "COL_DATE",
    "COL_ENTRIES",
    "COL_NAME",
    "COL_STATUS",
    "MIN_SIZE",
    "RideLibrary",
    "RidesListModel",
    "format_ride_status",
]

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

# See MainFrame._find's docstring (views/main_frame.py): retries for
# the measured wxPython 4.3.1/wxWidgets 3.3.3 stale-lookup hazard.
_FIND_SETTLE_ATTEMPTS = 25


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
    presenter contract); row selection and the Open/New/Duplicate/
    Delete actions ``LibraryPresenter`` will drive are a later
    phase's job (its own docstring: "Phase 5 wires...") and are not
    in this task's scope.
    """

    def __init__(self, dialog: wx.Dialog, *, data_source: DataSource | None = None) -> None:
        """Decorate an already-loaded ``ride_library_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``library.xrc``.
            data_source: The display-data seam; defaults to
                :class:`DemoDataSource`.
        """
        self.dialog = dialog
        self.data_source: DataSource = data_source if data_source is not None else DemoDataSource()

        self.rides_list = self._find(ids.RIDES_LIST, wx.dataview.DataViewCtrl)
        self._build_columns()
        self._model: RidesListModel | None = None

        self.show_rides(self.data_source.rides())
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See ``MainFrame._find``'s docstring (``views/main_frame.py``)
        for the full measured reasoning this mirrors: an explicit
        ``parent=self.dialog`` scopes the lookup, and the retry loop
        settles the address-reuse hazard this wx build exhibits
        under sustained window churn.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        control = wx.Window.FindWindowByName(name, self.dialog)
        attempts = 0
        while not isinstance(control, expected_type) and attempts < _FIND_SETTLE_ATTEMPTS:
            wx.SafeYield()
            control = wx.Window.FindWindowByName(name, self.dialog)
            attempts += 1
        if not isinstance(control, expected_type):
            raise LookupError(f"ride_library_dlg has no control named {name!r}")  # noqa: TRY004
        return control

    def _build_columns(self) -> None:
        """Append ``rides_list``'s four columns in canvas order."""
        for col, label in enumerate(COLUMN_LABELS):
            self.rides_list.AppendTextColumn(label, col)

    def show_rides(self, rows: list[RideSummary]) -> None:
        """Render ``rides_list`` (``LibraryView``)."""
        self._model = RidesListModel(rows)
        self.rides_list.AssociateModel(self._model)

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
