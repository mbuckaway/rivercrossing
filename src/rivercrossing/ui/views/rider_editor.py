# SPDX-License-Identifier: GPL-3.0-only
"""``RiderEditor``: ``rider_editor_dlg`` (1d/2b), the roster (E1.5.2).

xrc-windows.md section C's code-side footnote puts ``riders_list``'s
rows, and its Team column's solo-only visibility, in code --
``riders.xrc``'s own header explains why (``wxDataViewListCtrl``
would overwrite the frozen name). This module is that binding,
following the pattern ``views/main_frame.py`` already established
for ``crossings_list``.

CSV import preview (``csv_preview_dlg``) and the roster-editing
actions (Add/Save/Delete, team assignment) are a later phase's job
per ``RidersPresenter``'s own docstring ("Phase 5 wires...") and are
not in this task's scope -- this module only shows the demo roster.

``_find`` is now shared via ``ui.views._support.find_control`` --
see that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, RiderRow

__all__ = [
    "COLUMN_LABELS",
    "COL_NAME",
    "COL_PLATE",
    "COL_TEAM",
    "MIN_SIZE",
    "SOLO_TEAM_TEXT",
    "RiderEditor",
    "RidersListModel",
    "format_team",
    "is_solo_only",
]

COL_PLATE = 0
COL_NAME = 1
COL_TEAM = 2

# xrc-windows.md C's exact column order: "Plate | Name | Team".
COLUMN_LABELS: tuple[str, ...] = ("Plate", "Name", "Team")

# The canvas's own dash for a solo rider's Team cell
# ("123 Sam Ellis —").
SOLO_TEAM_TEXT = "—"

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


def is_solo_only(rows: Sequence[RiderRow]) -> bool:
    """Return whether every row in *rows* is a solo rider (R-15/C).

    ``riders_list``'s Team column hides entirely in a solo-only ride
    (xrc-windows.md C's own annotation) -- an empty *rows* counts as
    solo-only too, since there is then no team to show either.
    """
    return all(row.team is None for row in rows)


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
    """Code-side behaviour for ``rider_editor_dlg`` (1d/2b).

    Implements ``RidersView.show_riders`` (module-skeletons.md's
    presenter contract); the CSV preview and roster-editing surface
    are a later phase's job and are not in this task's scope.
    """

    def __init__(self, dialog: wx.Dialog, *, data_source: DataSource) -> None:
        """Decorate an already-loaded ``rider_editor_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``riders.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
        """
        self.dialog = dialog
        self.data_source = data_source

        self.riders_list = self._find(ids.RIDERS_LIST, wx.dataview.DataViewCtrl)
        self._team_column = self._build_columns()
        self._model: RidersListModel | None = None

        self.show_riders(self.data_source.riders())
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
            The Team column object, for :meth:`show_riders` to hide
            or show per :func:`is_solo_only`.
        """
        columns = [
            self.riders_list.AppendTextColumn(label, col)
            for col, label in enumerate(COLUMN_LABELS)
        ]
        return columns[COL_TEAM]

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render ``riders_list``, hiding Team in a solo-only ride.

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._model = RidersListModel(rows)
        associate_model(self.riders_list, self._model)
        self._team_column.SetHidden(is_solo_only(rows))

    def _apply_min_size(self) -> None:
        """Force the canvas's 640px floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()
