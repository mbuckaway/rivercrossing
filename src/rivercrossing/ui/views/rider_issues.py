# SPDX-License-Identifier: GPL-3.0-only
"""``RiderIssuesView`` + ``run_rider_issues_flow`` (R-78).

The "Check for Rider Issues…" dialog renders the read-only defect
report :func:`~rivercrossing.rider_issues.rider_issues` still finds on
a live, in-memory :class:`~rivercrossing.roster.Roster`, and offers one-
click fixes: open that issue's own editor (team-of-one opens the teams
editor preselecting that team, everything else the rider editor
preselecting the issue's rider), convert a pooled size-1 team's lone
rider back into their own solo entry, assign a missing-number rider the
next free plate, and renumber a duplicate-number issue's later
claimant.

The view is dumb, forwarding every control event straight to
:class:`~rivercrossing.ui.presenters.rider_issues.RiderIssuesPresenter`
and rendering whatever it is told -- the same presenter-inside-the-view
wiring ``views/rider_editor.py`` and ``views/team_editor.py`` use. A
refused conversion renders as a code-side ``wxInfoBar``
(:data:`ISSUES_INFOBAR`) -- ``riders.xrc`` cannot author a ``wxInfoBar``
at all, so it is wrapped around the dialog's existing sizer with both
slide effects disabled, the measured hang remedy ``RiderEditor.
_build_infobar`` documents.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview
import wx.xrc

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.rider_issues import RiderIssueRow, RiderIssuesPresenter
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import associate_model, clamp_to_display, find_control

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.roster import Roster

__all__ = [
    "ISSUES_COLUMN_LABELS",
    "ISSUES_INFOBAR",
    "MIN_SIZE",
    "IssuesListModel",
    "RiderIssuesView",
    "run_rider_issues_flow",
]

# xrc-windows.md C's rider_issues_dlg mock: "Plate | Name | Issue".
ISSUES_COLUMN_LABELS: tuple[str, ...] = ("Plate", "Name", "Issue")

# ui/ids.py is generated from the .xrc files (R-05); issues_infobar
# never appears there since XRC cannot author a wxInfoBar at all
# (rider_editor.py's ROSTER_INFOBAR precedent).
ISSUES_INFOBAR = "issues_infobar"

# Phase 6: XRC has no window-level minsize (riders.xrc's own header
# notes this and defers to code), so _apply_min_size floors the report
# here -- wide enough for the four action buttons on one row beside
# Close, tall enough for the issues list to read as a list rather than
# a letterbox. The height matches the sibling editors' 560
# (rider_editor 1280x560, team_editor 940x560).
MIN_SIZE = (900, 560)


class IssuesListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``RiderIssueRow`` rows, ``issues_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[RiderIssueRow]) -> None:
        """Wrap *rows* in the report's own issue order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the issues list's fixed three columns."""
        return len(ISSUES_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every ``issues_list`` column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        issue = self._rows[row]
        return (issue.plate, issue.name, issue.message)[col]


class RiderIssuesView:
    """Code-side behaviour for ``rider_issues_dlg`` (R-78).

    Implements the ``RiderIssuesView`` Protocol
    (``ui.presenters.rider_issues``) and constructs its own
    :class:`~rivercrossing.ui.presenters.rider_issues.
    RiderIssuesPresenter` over *roster*, following ``views/
    rider_editor.py``'s presenter-inside-the-view wiring.
    """

    def __init__(self, dialog: wx.Dialog, *, roster: Roster) -> None:
        """Decorate an already-loaded ``rider_issues_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``riders.xrc``.
            roster: The in-memory :class:`~rivercrossing.roster.
                Roster` this dialog reports on and, on a conversion,
                writes to.
        """
        self.dialog = dialog

        self.issues_summary_lbl = self._find(ids.ISSUES_SUMMARY_LBL, wx.StaticText)
        self.issues_list = self._find(ids.ISSUES_LIST, wx.dataview.DataViewCtrl)
        self._build_columns()
        # Replaced by the presenter's own show_issues() call below,
        # before any event can fire -- typed non-optional so
        # _on_row_selected never has to narrow it.
        self._model: IssuesListModel = IssuesListModel(())

        self.open_editor_btn = self._find(ids.OPEN_EDITOR_BTN, wx.Button)
        self.convert_solo_btn = self._find(ids.CONVERT_SOLO_BTN, wx.Button)
        self.assign_plate_btn = self._find(ids.ASSIGN_PLATE_BTN, wx.Button)
        self.renumber_btn = self._find(ids.RENUMBER_BTN, wx.Button)

        self.issues_infobar = self._build_infobar()

        # ``load=False``: the presenter must own ``self.presenter``'s
        # target before any render runs, since show_issues now
        # reconciles the selection straight back through the presenter.
        self.presenter = RiderIssuesPresenter(self, roster, load=False)

        self._bind_events()
        self._apply_min_size()
        self.presenter.refresh()

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
        """Append ``issues_list``'s three columns in canvas order."""
        for col, label in enumerate(ISSUES_COLUMN_LABELS):
            self.issues_list.AppendTextColumn(label, col)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`ISSUES_INFOBAR`, wrapped on top.

        ``riders.xrc``'s rider_issues_dlg top sizer is a plain
        ``wxBoxSizer`` with no reserved InfoBar slot (XRC cannot author
        a wxInfoBar), so the existing sizer is kept alive and nested
        inside a new outer vertical one instead of edited in the frozen
        XRC. Both slide effects are disabled for the measured hang
        ``RiderEditor._build_infobar`` documents.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(ISSUES_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_open_editor, self.open_editor_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_convert_solo, self.convert_solo_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_assign_plate, self.assign_plate_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_renumber, self.renumber_btn)
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_row_selected, self.issues_list
        )

    def _on_row_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle an ``issues_list`` selection: forward its row index.

        Two guards, both measured-needed (the same pair
        ``views/rider_editor.py`` carries): nothing selected is a stale
        event after the row it pointed to was deleted, and a row index
        past the model's current count is that staleness one step
        later -- wx can deliver the event after ``show_issues`` has
        already replaced the model, and an index into the *new* model
        would address the wrong record. Either way there is no row to
        forward.
        """
        event.Skip()
        item = self.issues_list.GetSelection()
        if not item.IsOk():
            return
        row = self._model.GetRow(item)
        if not 0 <= row < self._model.GetCount():
            return
        self.presenter.on_row_selected(row)

    def _reconcile_selection(self) -> None:
        """Re-apply the list's live selection after a render (D1).

        ``show_issues`` replaces the model, so whatever the control
        still has selected must be re-resolved against the *new* model
        and re-forwarded; a programmatic ``Select`` fires the selection
        event on macOS's generic control but not on MSW's native one
        (``RiderEditor.select_rider_by_plate``'s measured note), so
        relying on the event alone leaves the buttons stale. This owns
        both outcomes -- a valid row re-enables through the presenter's
        idempotent ``on_row_selected``, anything else disables through
        ``on_nothing_selected`` -- and never calls ``refresh``, which
        would recurse ``_load -> show_issues -> reconcile``.
        """
        item = self.issues_list.GetSelection()
        if item.IsOk():
            row = self._model.GetRow(item)
            if 0 <= row < self._model.GetCount():
                self.presenter.on_row_selected(row)
                return
        self.presenter.on_nothing_selected()

    def _on_convert_solo(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``convert_solo_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_convert_solo()

    def _on_assign_plate(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``assign_plate_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_assign_plate()

    def _on_renumber(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``renumber_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_renumber()

    def _on_open_editor(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``open_editor_btn``: open the selected issue's editor.

        A team-of-one issue opens the teams editor, anything else the
        rider editor; the nested editor edits the same in-memory roster,
        so the report re-lists once it closes.
        """
        event.Skip()
        target = self.presenter.on_open_editor()
        if target in ("team", "rider"):
            self._open_nested_editor(target)
        self.presenter.refresh()

    def _open_nested_editor(self, target: str) -> None:
        """Open the selected issue's editor modally over this dialog."""
        from rivercrossing.ui.views.rider_editor import RiderEditor  # noqa: PLC0415
        from rivercrossing.ui.views.team_editor import TeamEditor  # noqa: PLC0415

        dialog_name = ids.TEAM_EDITOR_DLG if target == "team" else ids.RIDER_EDITOR_DLG
        window = wx.xrc.XmlResource.Get().LoadDialog(None, dialog_name)
        if window is None:
            return
        try:
            if target == "team":
                editor = TeamEditor(window, roster=self.presenter.roster)
                editor.select_team_by_name(self.presenter.selected_team_name())
            else:
                rider_editor = RiderEditor(window, roster=self.presenter.roster)
                rider_editor.select_rider_by_plate(self.presenter.selected_plate())
            dialogs.run_dialog(window, opener=self.dialog)
        finally:
            if not window.IsBeingDeleted():
                window.Destroy()

    def show_issues(self, rows: list[RiderIssueRow]) -> None:
        """Render ``issues_list`` from *rows* (``RiderIssuesView``).

        Reconciles the selection straight after ``associate_model``:
        the model just changed, so the buttons must derive from the
        control's post-render selection, never from the pre-render one.
        """
        self.issues_infobar.Dismiss()
        self._model = IssuesListModel(rows)
        associate_model(self.issues_list, self._model)
        self._reconcile_selection()

    def show_summary(self, text: str) -> None:
        """Render the issue-count summary line (``RiderIssuesView``)."""
        self.issues_summary_lbl.SetLabel(text)

    def set_convert_solo_enabled(self, *, enabled: bool) -> None:
        """Gate ``convert_solo_btn`` on *enabled*."""
        self.convert_solo_btn.Enable(enabled)

    def set_assign_plate_enabled(self, *, enabled: bool) -> None:
        """Gate ``assign_plate_btn`` on *enabled*."""
        self.assign_plate_btn.Enable(enabled)

    def set_renumber_enabled(self, *, enabled: bool) -> None:
        """Gate ``renumber_btn`` on *enabled*."""
        self.renumber_btn.Enable(enabled)

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`ISSUES_INFOBAR`."""
        self.issues_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def _apply_min_size(self) -> None:
        """Open the report at its own floor, clamped to the display.

        ``Fit()`` first, so the size being floored is whatever this
        platform actually measured for the built window, then the
        larger of that and :data:`MIN_SIZE` is clamped to the display's
        work area (:func:`~rivercrossing.ui.views._support.
        clamp_to_display`), so a small screen still gets a fully
        visible dialog. The clamped size is both the floor
        (``SetMinSize``) and the size the dialog opens at
        (``SetSize``): a floor alone would still let the loaded window
        keep the narrow size XRC gave it.
        """
        self.dialog.Fit()
        fitted = self.dialog.GetSize()
        width, height = clamp_to_display(
            max(MIN_SIZE[0], fitted.width),
            max(MIN_SIZE[1], fitted.height),
        )
        self.dialog.SetMinSize(wx.Size(width, height))
        self.dialog.SetSize(wx.Size(width, height))


def run_rider_issues_flow(parent: wx.Window, roster: Roster) -> bool:
    """Open the rider-issues dialog modally; report whether it changed.

    No picker runs ahead of this dialog (unlike ``run_csv_import_flow``)
    -- the report reads the roster already in memory.

    Args:
        parent: The window to parent the dialog on, and to return focus
            to once it ends.
        roster: The in-memory roster the report reads and, on any fix
            or nested-editor edit, writes.

    Returns:
        Whether the roster was mutated in any way while the dialog was
        open, measured as an :attr:`~rivercrossing.roster.Roster.
        audit_log` length delta. The presenter's own ``did_change``
        covers only its three fixes, so it misses a nested rider/team
        editor's edit (a rename, a move) made through the Open Editor
        button; the audit delta catches every roster mutation, so the
        caller always knows to persist + rebuild. Nothing in this
        dialog mutates the roster without auditing, and
        ``load_entries`` (the store restore that does not audit) never
        runs here.
    """
    window = wx.xrc.XmlResource.Get().LoadDialog(None, ids.RIDER_ISSUES_DLG)
    if window is None:
        return False

    audit_before = len(roster.audit_log)
    try:
        RiderIssuesView(window, roster=roster)
        default_button = dialogs.default_button_for(ids.RIDER_ISSUES_DLG)
        if default_button is not None:
            dialogs.set_default_button(window, default_button)
        dialogs.run_dialog(window, opener=parent)
    finally:
        # Fault A: construction runs inside the close guard -- a
        # post-load raise (RiderIssuesView's _find can exhaust its 25
        # retries under hosted-runner load) must not leave the
        # just-loaded dialog fully alive, rerun-masked until the reap
        # pin catches it.
        if not window.IsBeingDeleted():
            window.Destroy()
    return len(roster.audit_log) != audit_before
