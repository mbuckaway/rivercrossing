# SPDX-License-Identifier: GPL-3.0-only
"""``ShortcutsDialog``: ``shortcuts_dlg`` (section E), the Help dialog.

xrc-windows.md section E's code-side footnote puts ``shortcuts_list``'s
rows in code: "filled in code from the accelerator table -- cannot
drift" (E8.2.1). This module is that binding -- it appends the
Key | Action columns and renders one row per :class:`Accelerator`
through a ``DataViewIndexListModel`` subclass, the same idiom
``audit.py``/``ride_library.py`` use for their read-only
DataViewCtrls. The ``rows`` parameter defaults to
:data:`ACCELERATOR_TABLE`, the single source of truth
(``ui.accelerators``' own docstring: E8.2.1 imports only that, never
the full command table); production wiring uses the default, and a
test can inject its own sequence to prove the dialog renders its
input. ``wxID_CLOSE`` needs no wiring here: every
dialog's Escape/click-to-dismiss handling for that stock id comes
from ``ui.views.dialogs.wire_close_button``, applied once by
``dialogs.run_dialog`` around every dialog this codebase shows.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.accelerators import ACCELERATOR_TABLE, Accelerator
from rivercrossing.ui.views._support import DialogFindMixin, associate_model

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "COL_ACTION",
    "COL_KEY",
    "SHORTCUTS_MIN_SIZE",
    "SHORTCUT_COLUMN_LABELS",
    "SHORTCUT_COLUMN_WIDTHS",
    "ShortcutsDialog",
    "ShortcutsListModel",
]

COL_KEY = 0
COL_ACTION = 1

# xrc-windows.md E's exact column order.
SHORTCUT_COLUMN_LABELS: tuple[str, ...] = ("Key", "Action")

# Phase 6: a DataViewCtrl column never sizes itself to its content, so
# an unpinned column keeps the platform's 80 DIP default and the Action
# text clips on first open (ride_library.py's own measured note). Key
# takes the accelerator column's 120 px, Action the wider 320 px -- the
# canvas's own two-column proportions.
SHORTCUT_COLUMN_WIDTHS: tuple[int, ...] = (120, 320)

# Phase 6: the dialog's own floor. XRC has no window-level minsize and
# dialogs.xrc declares no <size>, so ShortcutsDialog applies this in
# code. 480x300 fits the two pinned columns above plus the button row,
# and sits inside the 1366x768 floor display (UX-DESKTOP section 6).
SHORTCUTS_MIN_SIZE = (480, 300)

_TEXT_ACCESSORS: tuple[Callable[[Accelerator], str], ...] = (
    lambda accel: accel.key,
    lambda accel: accel.action,
)


class ShortcutsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``Accelerator`` rows for ``shortcuts_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[Accelerator]) -> None:
        """Wrap *rows* in the accelerator table's order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the shortcuts dialog's fixed two columns."""
        return len(SHORTCUT_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


class ShortcutsDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``shortcuts_dlg`` (section E).

    The dialog's whole content is the generated shortcuts table, so
    the view renders directly -- no presenter, mirroring
    ``SelfTestDialog``'s shape. ``wxID_CLOSE`` is handled by
    ``dialogs.run_dialog``'s ``wire_close_button`` (Escape + click).
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        *,
        rows: Sequence[Accelerator] = ACCELERATOR_TABLE,
    ) -> None:
        """Decorate an already-loaded ``shortcuts_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the caller already loaded
                from ``dialogs.xrc``.
            rows: The accelerator rows to render; defaults to
                :data:`ACCELERATOR_TABLE` -- the single source of
                truth (xrc-windows.md E). Tests inject a fake row to
                prove the dialog renders its input.
        """
        self.dialog = dialog
        self.shortcuts_list = self._find(ids.SHORTCUTS_LIST, wx.dataview.DataViewCtrl)
        self._build_columns()
        self._model = ShortcutsListModel(rows)
        associate_model(self.shortcuts_list, self._model)
        # Keep the view alive for the dialog's lifetime: this dialog
        # binds no events, so nothing else references it after the
        # caller (``_open_target``) drops the construction result --
        # and a collected view collects its model, leaving
        # ``GetModel()`` to re-wrap the C++ model as a base
        # ``DataViewModel`` without ``GetCount`` (measured in the VM).
        # The ``frame.console = self`` / ``frame.presenter`` precedent
        # (main_frame.py, results_win.py) attaches the same way.
        dialog.shortcuts_view = self
        self._apply_min_size()

    def _build_columns(self) -> None:
        """Append the dialog's two text columns in canvas order.

        Each column takes its own pinned width from
        :data:`SHORTCUT_COLUMN_WIDTHS` (Phase 6) -- see that constant
        for the measured reason an unpinned DataView column clips
        ``shortcuts_list``'s Action text.
        """
        for col, label in enumerate(SHORTCUT_COLUMN_LABELS):
            self.shortcuts_list.AppendTextColumn(label, col, width=SHORTCUT_COLUMN_WIDTHS[col])

    def _apply_min_size(self) -> None:
        """Floor the dialog at :data:`SHORTCUTS_MIN_SIZE`, then Fit().

        ``SetMinSize`` is the floor, ``Fit()`` is what grows the loaded
        window to respect it now (``ride_library._apply_min_size``'s
        measured note), and the floor is re-applied afterwards -- belt
        and braces, so no platform's ``Fit()`` can leave the minimum
        lower than :data:`SHORTCUTS_MIN_SIZE`.
        """
        self.dialog.SetMinSize(wx.Size(*SHORTCUTS_MIN_SIZE))
        self.dialog.Fit()
        self.dialog.SetMinSize(wx.Size(*SHORTCUTS_MIN_SIZE))
