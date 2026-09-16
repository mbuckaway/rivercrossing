# SPDX-License-Identifier: GPL-3.0-only
"""``AuditDialog``: audit_dlg (D), the read-only audit trail (E7.3.1).

xrc-windows.md section D's code-side footnote puts ``audit_list``'s
columns and rows in code -- ``audit.xrc``'s own header explains why
(``wxDataViewListCtrl`` would overwrite the frozen name). This module
is that binding: it appends the When | Action | Entry | Reason columns,
renders ``AuditRow`` rows through a ``DataViewIndexListModel``
subclass, wires ``audit_search`` and ``action_choice`` to the
presenter's two filters, and implements the
:class:`~rivercrossing.ui.presenters.audit.AuditView` contract
(``show_audit_rows``, ``set_entry_filter``). The one live presenter is
built here, the same ``RideSetup``/``ResultsWindow`` precedent.

The Who column is retired (scope 6a): the engine never records another
actor, so every cell read the same word. Every remaining column is
appended with :data:`AUDIT_COLUMN_FLAGS` (sortable and resizable) and
answers the native header sort through :meth:`AuditListModel.Compare`,
and every render re-imposes the When-descending default
(:meth:`AuditDialog._apply_default_sort`, scope 6b).

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
from rivercrossing.ui.presenters.audit import ACTION_CHOICES, ALL_ACTIONS, AuditPresenter
from rivercrossing.ui.views._support import (
    DialogFindMixin,
    _ordering,
    associate_model,
    clamp_to_display,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import AuditRow, DataSource

__all__ = [
    "AUDIT_COLUMN_FLAGS",
    "AUDIT_COLUMN_LABELS",
    "AUDIT_COLUMN_WIDTHS",
    "COL_ACTION",
    "COL_ENTRY",
    "COL_REASON",
    "COL_WHEN",
    "DEFAULT_SORT_ASCENDING",
    "DEFAULT_SORT_COLUMN",
    "MIN_SIZE",
    "AuditDialog",
    "AuditListModel",
]

COL_WHEN = 0
COL_ACTION = 1
COL_ENTRY = 2
COL_REASON = 3

# xrc-windows.md D's column order, less the retired Who column.
AUDIT_COLUMN_LABELS: tuple[str, ...] = ("When", "Action", "Entry", "Reason")

# Phase 6: one pinned opening width per column, in the labels' own
# order. A DataViewCtrl column never sizes itself to its content, so an
# unpinned column keeps the platform's 80 DIP default (measured on
# 4.3.1 osx-cocoa / wxWidgets 3.3.3) and the Reason cell -- the
# longest, an arbitrary sentence -- clipped at every window size. The
# three narrow columns fit what they hold (an ISO-ish timestamp, an
# action verb, a plate) and Reason takes the slack; every column stays
# resizable, so the operator tunes them by hand.
AUDIT_COLUMN_WIDTHS: tuple[int, ...] = (90, 180, 120, 330)

# Phase 6: the dialog's own floor, code-side because XRC has no
# window-level minsize and audit.xrc declares no <size> (its own
# header notes it). 1000x600 is ~2x the dialog's measured content at
# the pinned column widths above, and ``_apply_min_size`` clamps it to
# the display's work area so a small screen still gets a whole dialog.
MIN_SIZE = (1000, 600)

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the column
# NSTableColumnNoResizing -- so both bits must be spelled out (mirrors
# results_win.py's STANDINGS_COLUMN_FLAGS).
AUDIT_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE

# The dialog's opening sort (scope 6b): When descending, so the newest
# event is row 0 -- the order the source itself hands the model, and
# the order ``_apply_default_sort`` re-imposes after every rebuild.
DEFAULT_SORT_COLUMN = COL_WHEN
DEFAULT_SORT_ASCENDING = False

# The dropdown draws labels, the audit trail records action strings:
# this is the one place the two vocabularies meet, so the presenter only
# ever filters on an action (``presenters.audit.ACTION_CHOICES``).
_ACTION_BY_LABEL: dict[str, str] = dict(ACTION_CHOICES)

_TEXT_ACCESSORS: tuple[Callable[[AuditRow], str], ...] = (
    lambda row: row.when,
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
        """Return the audit trail's fixed four columns."""
        return len(AUDIT_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
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

        The native header arrows' answer: the control hands this two
        items and the model column, and the comparison runs through
        :data:`_TEXT_ACCESSORS` -- the very accessors the cells render
        with, so what is drawn is what is sorted -- on the rows those
        items index (``DataViewIndexListModel.GetRow``).

        Equal keys fall back to the row's own position, which is
        unique: wx's control-side sort is not stable, so without the
        tie-break two rows showing the same cell could reorder freely
        between sorts. The tie-break is deliberately *not* negated for
        the downward arrow, so equal-key rows keep their original
        order in both directions (mirrors ``StandingsListModel.Compare``
        in ``ui.views.results_win``). *ascending* is the arrow's own
        direction.
        """
        first_row = self.GetRow(item1)
        second_row = self.GetRow(item2)
        accessor = _TEXT_ACCESSORS[col]
        result = _ordering(accessor(self._rows[first_row]), accessor(self._rows[second_row]))
        if result == 0:
            return _ordering(first_row, second_row)
        return result if ascending else -result


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
            action_filter: The audited action to start on, or
                :data:`ALL_ACTIONS` for no action filter (audit.xrc's
                own default).
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
        """Append ``audit_list``'s four text columns in canvas order.

        Each column takes its own pinned width from
        :data:`AUDIT_COLUMN_WIDTHS` (Phase 6) -- see that constant for
        the measured reason an unpinned DataView column is unreadable --
        and :data:`AUDIT_COLUMN_FLAGS` (scope 6b) so every header sorts
        and every border drags.
        """
        for col, label in enumerate(AUDIT_COLUMN_LABELS):
            self.audit_list.AppendTextColumn(
                label, col, width=AUDIT_COLUMN_WIDTHS[col], flags=AUDIT_COLUMN_FLAGS
            )

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
        self.presenter.on_action_selected(self._selected_action())

    def _selected_action(self) -> str:
        """Return the action ``action_choice`` currently names.

        ``action_choice`` draws labels, the audit trail records action
        strings, and :data:`_ACTION_BY_LABEL` is the one place the two
        meet -- so the presenter only ever filters on an action. A
        label the tuple does not carry (impossible while the ``.xrc``
        and :data:`~rivercrossing.ui.presenters.audit.ACTION_CHOICES`
        agree, which ``test_audit.py`` pins) reads as "All actions"
        rather than blanking the list.
        """
        return _ACTION_BY_LABEL.get(self.action_choice.GetStringSelection(), ALL_ACTIONS)

    # ------------------------------------------------------ AuditView

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """Render ``audit_list`` (``AuditView``).

        The rebuilt model drops the sort key the control was holding,
        so the default When-descending order is re-imposed right after
        (:meth:`_apply_default_sort`). See
        ``ui.views._support.associate_model``'s docstring for why this
        repaints explicitly (unverified remedy).
        """
        self._model = AuditListModel(rows)
        associate_model(self.audit_list, self._model)
        self._apply_default_sort()

    def _apply_default_sort(self) -> None:
        """Re-impose the default sort on the freshly built model (6b).

        Called from every :meth:`show_audit_rows`: the rebuild
        associates a new model, which drops the sort key the control
        was holding, so the arrow and the row order are restored from
        :data:`DEFAULT_SORT_COLUMN`/:data:`DEFAULT_SORT_ASCENDING`
        (When descending -- the newest event first). Mirrors
        ``main_frame._apply_feed_sort``.

        The ``UnsetAsSortKey`` first is the load-bearing macOS step
        (measured): ``SetSortOrder`` is a no-op when the direction is
        unchanged, so without the clear the rebuilt model would keep
        the presenter's own order and the sort would silently revert.
        The clear is guarded by ``IsSortKey``: on the first render the
        column has never sorted the control, and Windows' generic
        ``DataViewColumn.UnsetAsSortKey`` asserts ("column is not used
        for sorting") and aborts the process there.
        """
        model = self._model
        if model is None:
            return
        column = self.audit_list.GetColumn(DEFAULT_SORT_COLUMN)
        if column is None:
            return
        if column.IsSortKey():
            column.UnsetAsSortKey()
        column.SetSortOrder(DEFAULT_SORT_ASCENDING)
        model.Resort()

    def set_entry_filter(self, entry: str) -> None:
        """Pre-fill ``audit_search`` (``AuditView``, R-38 deep-link).

        ``SetValue`` fires ``EVT_TEXT`` on this build (measured), but
        the presenter binds that event only after construction, so the
        deep-link pre-fill cannot loop back into the presenter.
        """
        self.audit_search.SetValue(entry)
