# SPDX-License-Identifier: GPL-3.0-only
"""``AuditDialog``: audit_dlg (D), the read-only audit trail (E7.3.1).

xrc-windows.md section D's code-side footnote puts ``audit_list``'s
columns and rows in code -- ``audit.xrc``'s own header explains why
(``wxDataViewListCtrl`` would overwrite the frozen name). This module
is that binding: it appends the When | Who | Action | Entry | Reason
columns, renders ``AuditRow`` rows through a
``DataViewIndexListModel`` subclass, wires ``audit_search`` and
``action_choice`` to the presenter's two filters, and implements the
:class:`~rivercrossing.ui.presenters.audit.AuditView` contract
(``show_audit_rows``, ``set_entry_filter``). The one live presenter is
built here, the same ``RideSetup``/``ResultsWindow`` precedent.

The dialog is shared by two entry points: the menu route (app.py's
``_decorate``) and the entry-detail deep-link (``views.corrections``'
``run_audit``, which passes the entry's plate as ``entry_filter`` --
R-38). Both hand in the live ``EngineDataSource`` and roster, so the
search can resolve a plate to its entry's display name.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.audit import ALL_ACTIONS, AuditPresenter
from rivercrossing.ui.views._support import (
    DialogFindMixin,
    associate_model,
    clamp_to_display,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import AuditRow, DataSource

__all__ = [
    "AUDIT_COLUMN_LABELS",
    "AUDIT_COLUMN_WIDTHS",
    "COL_ACTION",
    "COL_ENTRY",
    "COL_REASON",
    "COL_WHEN",
    "COL_WHO",
    "MIN_SIZE",
    "AuditDialog",
    "AuditListModel",
]

COL_WHEN = 0
COL_WHO = 1
COL_ACTION = 2
COL_ENTRY = 3
COL_REASON = 4

# xrc-windows.md D's exact column order.
AUDIT_COLUMN_LABELS: tuple[str, ...] = ("When", "Who", "Action", "Entry", "Reason")

# Phase 6: one pinned opening width per column, in the labels' own
# order. A DataViewCtrl column never sizes itself to its content, so an
# unpinned column keeps the platform's 80 DIP default (measured on
# 4.3.1 osx-cocoa / wxWidgets 3.3.3) and the Reason cell -- the
# longest, an arbitrary sentence -- clipped at every window size. The
# three narrow columns fit what they hold (an ISO-ish timestamp, a
# plate, an action verb) and Reason takes the slack; every column stays
# resizable, so the operator tunes them by hand.
AUDIT_COLUMN_WIDTHS: tuple[int, ...] = (90, 120, 180, 120, 330)

# Phase 6: the dialog's own floor, code-side because XRC has no
# window-level minsize and audit.xrc declares no <size> (its own
# header notes it). 1000x600 is ~2x the dialog's measured content at
# the pinned column widths above, and ``_apply_min_size`` clamps it to
# the display's work area so a small screen still gets a whole dialog.
MIN_SIZE = (1000, 600)

_TEXT_ACCESSORS: tuple[Callable[[AuditRow], str], ...] = (
    lambda row: row.when,
    lambda row: row.who,
    lambda row: row.action,
    lambda row: row.entry,
    lambda row: row.reason,
)


class AuditListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``AuditRow`` rows for ``audit_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: list[AuditRow]) -> None:
        """Wrap *rows*, in the source's newest-first order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the audit trail's fixed five columns."""
        return len(AUDIT_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


class AuditDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``audit_dlg`` (D, R-38).

    Implements the :class:`~rivercrossing.ui.presenters.audit.
    AuditView` contract in full: ``show_audit_rows`` renders
    ``audit_list``, and ``set_entry_filter`` pre-fills
    ``audit_search`` (the entry-detail deep-link). The one live
    presenter is built here, the same ``RideSetup``/``ResultsWindow``
    precedent; ``audit_search`` text changes and ``action_choice``
    selections forward straight to it.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, data_source) + the three open seams
        self,
        dialog: wx.Dialog,
        *,
        data_source: DataSource,
        roster: Roster | None = None,
        entry_filter: str = "",
        action_filter: str = ALL_ACTIONS,
    ) -> None:
        """Decorate an already-loaded ``audit_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the caller already loaded
                from ``audit.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
            roster: The live roster, resolving a plate to its entry's
                display name for the search filter; ``None`` searches
                by plate alone.
            entry_filter: The deep-linked entry's plate (entry detail's
                audit button, R-38); pre-fills ``audit_search``.
            action_filter: The ``action_choice`` bucket to start on;
                defaults to "All actions" (audit.xrc's own default).
        """
        self.dialog = dialog
        self.data_source = data_source

        self.audit_search = self._find(ids.AUDIT_SEARCH, wx.SearchCtrl)
        self.action_choice = self._find(ids.ACTION_CHOICE, wx.Choice)
        self.audit_list = self._find(ids.AUDIT_LIST, wx.dataview.DataViewCtrl)
        self._build_columns()
        self._apply_min_size()
        self._model: AuditListModel | None = None

        self.presenter = AuditPresenter(
            self,
            data_source,
            roster=roster,
            entry_filter=entry_filter,
            action_filter=action_filter,
        )

        self._bind_events()

    def _build_columns(self) -> None:
        """Append ``audit_list``'s five text columns in canvas order.

        Each column takes its own pinned width from
        :data:`AUDIT_COLUMN_WIDTHS` (Phase 6) -- see that constant for
        the measured reason an unpinned DataView column is unreadable.
        """
        for col, label in enumerate(AUDIT_COLUMN_LABELS):
            self.audit_list.AppendTextColumn(label, col, width=AUDIT_COLUMN_WIDTHS[col])

    def _apply_min_size(self) -> None:
        """Floor *and* grow the dialog at :data:`MIN_SIZE` (Phase 6).

        ``SetMinSize`` is the floor; ``Fit()`` is what actually grows
        the loaded window to respect it now (the same measured note
        ``ride_library._apply_min_size`` carries). The floor is clamped
        to the display's work area
        (:func:`~rivercrossing.ui.views._support.clamp_to_display`), so
        a small screen still gets a whole dialog.
        """
        width, height = clamp_to_display(*MIN_SIZE)
        self.dialog.SetMinSize(wx.Size(width, height))
        self.dialog.Fit()

    def _bind_events(self) -> None:
        """Forward the two filters' events straight to the presenter.

        ``audit_search`` is a ``wxSearchCtrl``: text changes (typing,
        a programmatic ``SetValue``, the native clear X) all re-run the
        search, and the search button (Enter) does too -- every path
        reads the control's current value, so one handler serves all
        three events.
        """
        self.dialog.Bind(wx.EVT_TEXT, self._on_search_text, self.audit_search)
        self.dialog.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._on_search_text, self.audit_search)
        self.dialog.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_search_text, self.audit_search)
        self.dialog.Bind(wx.EVT_CHOICE, self._on_action_selected, self.action_choice)

    def _on_search_text(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a search-text change; forward it to the presenter."""
        event.Skip()
        self.presenter.on_search_text(self.audit_search.GetValue())

    def _on_action_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle an action_choice selection; forward to presenter."""
        event.Skip()
        self.presenter.on_action_selected(self.action_choice.GetStringSelection())

    # ------------------------------------------------------ AuditView

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """Render ``audit_list`` (``AuditView``).

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._model = AuditListModel(rows)
        associate_model(self.audit_list, self._model)

    def set_entry_filter(self, entry: str) -> None:
        """Pre-fill ``audit_search`` (``AuditView``, R-38 deep-link).

        ``SetValue`` fires ``EVT_TEXT`` on this build (measured), but
        the presenter binds that event only after construction, so the
        deep-link pre-fill cannot loop back into the presenter.
        """
        self.audit_search.SetValue(entry)
