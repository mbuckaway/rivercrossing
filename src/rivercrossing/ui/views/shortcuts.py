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
the full command table); production wiring uses the default, and an
injected sequence is what the functional suite uses to prove the
dialog renders its input. ``wxID_CLOSE`` needs no wiring here: every
dialog's Escape/click-to-dismiss handling for that stock id comes
from ``ui.views.dialogs.wire_close_button``, applied once by
``dialogs.run_dialog`` around every dialog this codebase shows.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.accelerators import ACCELERATOR_TABLE, Accelerator
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "COL_ACTION",
    "COL_KEY",
    "SHORTCUT_COLUMN_LABELS",
    "ShortcutsDialog",
    "ShortcutsListModel",
]

COL_KEY = 0
COL_ACTION = 1

# xrc-windows.md E's exact column order.
SHORTCUT_COLUMN_LABELS: tuple[str, ...] = ("Key", "Action")

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


class ShortcutsDialog:
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
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``dialogs.xrc``.
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
        """Append the dialog's two text columns in canvas order."""
        for col, label in enumerate(SHORTCUT_COLUMN_LABELS):
            self.shortcuts_list.AppendTextColumn(label, col)
