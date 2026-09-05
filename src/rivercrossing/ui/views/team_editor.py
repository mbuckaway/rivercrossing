# SPDX-License-Identifier: GPL-3.0-only
"""``TeamEditor``: team_editor_dlg (Phase 4), on a real Roster.

Phase 4 wires ``team_editor_dlg`` to a real, in-memory
:class:`~rivercrossing.roster.Roster` that
:class:`~rivercrossing.ui.presenters.teams.TeamsPresenter`
(``ui.presenters.teams``) reads and writes directly --
:class:`TeamEditor` takes ``roster=`` and constructs its own
presenter (mirroring ``views/rider_editor.py``'s
presenter-inside-the-view wiring), binding
Add team/Remove/Save/Pick card/Image/row selection to it. The two
lists' rows and columns live here (``teams.xrc``'s own header
explains why -- ``wxDataViewListCtrl`` would overwrite the frozen
name), the read-only members_list renders rider names only:
membership is managed in the Rider Editor, never here.

A refused operation (add/remove after start, a relay plate change
once locked, ...) renders as a code-side ``wxInfoBar``
(:data:`TEAMS_INFOBAR`) -- the same measured pattern
``rider_editor.py``'s ``RiderEditor`` uses, slide effects disabled
for the same reason. ``_find`` is shared via
``ui.views._support.find_control``.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.teams import TeamFormValues, TeamRow, TeamsPresenter
from rivercrossing.ui.views._support import associate_model, find_control
from rivercrossing.ui.views.results_win import format_card

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.roster import Roster

__all__ = [
    "COLUMN_LABELS",
    "COL_LOGO",
    "COL_MEMBER",
    "COL_NAME",
    "IMAGE_TEXT",
    "MEMBERS_COLUMN_LABELS",
    "MIN_SIZE",
    "TEAMS_INFOBAR",
    "MembersListModel",
    "TeamEditor",
    "TeamsListModel",
    "format_logo",
    "pick_logo_image_path",
]

COL_NAME = 0
COL_LOGO = 1

# xrc-windows.md C (Teams Editor): "Team | Logo".
COLUMN_LABELS: tuple[str, ...] = ("Team", "Logo")

COL_MEMBER = 0
MEMBERS_COLUMN_LABELS: tuple[str, ...] = ("Member",)

# The logo cell text when a PNG is set (image wins over a card --
# the presenter clears the card when an image lands).
IMAGE_TEXT = "Image"

# ui/ids.py is generated from the .xrc files (R-05); teams_infobar
# never appears there since XRC cannot author a wxInfoBar at all
# (teams.xrc's own header, rider_editor.py's precedent).
TEAMS_INFOBAR = "teams_infobar"

# teams.xrc notes the XRC no-window-minsize rule; this is the
# editor's own code-side floor (SetMinSize + Fit, the RiderEditor
# shape) -- wide enough for the two-pane Team | Logo + record form
# layout to stay usable on a 1366x768 field laptop (UX-DESKTOP §6).
MIN_SIZE = (760, 380)


def format_logo(logo_card: str | None, *, has_image: bool) -> str:
    """Return a team's ``Logo`` cell / preview text.

    An image wins: ``"Image"`` while ``logo_png`` is set, whatever
    the card column holds; otherwise the card code with its suit
    glyph (``format_card``'s own ``"AS"`` -> ``"A♠"``), or an empty
    cell when the team carries no logo.
    """
    if has_image:
        return IMAGE_TEXT
    if logo_card is None:
        return ""
    return format_card(logo_card)


_TEXT_ACCESSORS: tuple[Callable[[TeamRow], str], ...] = (
    lambda row: row.name,
    lambda row: format_logo(row.logo_card, has_image=row.has_image),
)


class TeamsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``TeamRow`` rows for ``teams_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[TeamRow]) -> None:
        """Wrap *rows* in the roster's own team order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the editor's fixed two columns."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every ``teams_list`` column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


class MembersListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over member names for ``members_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, names: Sequence[str]) -> None:
        """Wrap *names* in the selected team's member order."""
        super().__init__(len(names))
        self._names = tuple(names)

    def GetColumnCount(self) -> int:
        """Return the members list's fixed one column."""
        return len(MEMBERS_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- the single column is text
        """Return "string" -- every ``members_list`` cell is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Return the member name at *row*."""
        return self._names[row]


class TeamEditor:
    """Code-side behaviour for ``team_editor_dlg`` (Phase 4).

    Implements :class:`~rivercrossing.ui.presenters.teams.TeamsView`
    (``ui.presenters.teams``) and constructs its own
    :class:`~rivercrossing.ui.presenters.teams.TeamsPresenter` over
    *roster*, following ``rider_editor.py``'s presenter-inside-the-
    view wiring: the view stays dumb, forwarding every control event
    straight to the presenter and rendering whatever it is told.
    """

    def __init__(self, dialog: wx.Dialog, *, roster: Roster) -> None:
        """Decorate an already-loaded ``team_editor_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``teams.xrc``.
            roster: The in-memory :class:`~rivercrossing.roster.
                Roster` this editor reads and writes directly --
                never a ``DataSource`` projection of one.
        """
        self.dialog = dialog

        self.teams_list = self._find(ids.TEAMS_LIST, wx.dataview.DataViewCtrl)
        self.single_member_only_chk = self._find(ids.SINGLE_MEMBER_ONLY_CHK, wx.CheckBox)
        self._build_team_columns()
        # Replaced by the presenter's own show_teams() call below,
        # before any event can fire -- typed non-optional so
        # _on_row_selected never has to narrow it.
        self._teams_model: TeamsListModel = TeamsListModel([])

        self.name_input = self._find(ids.NAME_INPUT, wx.TextCtrl)
        self.relay_plate_input = self._find(ids.RELAY_PLATE_INPUT, wx.TextCtrl)
        self.notes_input = self._find(ids.NOTES_INPUT, wx.TextCtrl)
        self.logo_preview = self._find(ids.LOGO_PREVIEW, wx.StaticText)
        self.pick_card_btn = self._find(ids.PICK_CARD_BTN, wx.Button)
        self.image_btn = self._find(ids.IMAGE_BTN, wx.Button)
        self.members_list = self._find(ids.MEMBERS_LIST, wx.dataview.DataViewCtrl)
        self._build_member_columns()
        # Replaced by the presenter's own show_members() call below.
        self._members_model: MembersListModel = MembersListModel([])

        self.add_btn = self._find(ids.ADD_BTN, wx.Button)
        self.remove_btn = self._find(ids.REMOVE_BTN, wx.Button)
        self.save_btn = self._find(ids.SAVE_BTN, wx.Button)

        self.teams_infobar = self._build_infobar()

        self.presenter = TeamsPresenter(self, roster)

        self._bind_events()
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

    def _build_team_columns(self) -> None:
        """Append ``teams_list``'s two columns in canvas order."""
        for col, label in enumerate(COLUMN_LABELS):
            self.teams_list.AppendTextColumn(label, col)

    def _build_member_columns(self) -> None:
        """Append ``members_list``'s one column."""
        for col, label in enumerate(MEMBERS_COLUMN_LABELS):
            self.members_list.AppendTextColumn(label, col)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`TEAMS_INFOBAR`, wrapped on top.

        ``teams.xrc``'s already-authored top sizer has no reserved
        InfoBar slot (it predates this decision, like ``riders.xrc``
        before it), so the existing sizer is kept alive and nested
        inside a new outer vertical one instead of edited in the
        frozen XRC. Measured (wxPython 4.3.1 / wxWidgets 3.3.3):
        calling ``Dismiss()``/``ShowMessage()`` on a ``wx.InfoBar``
        with its default slide effect never returns -- disabling both
        effects here is what makes :meth:`show_validation` safe,
        the identical fix ``RiderEditor._build_infobar`` documents.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(TEAMS_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_add, self.add_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_remove, self.remove_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_save, self.save_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_pick_card, self.pick_card_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_image_click, self.image_btn)
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_row_selected, self.teams_list
        )
        self.dialog.Bind(
            wx.EVT_CHECKBOX, self._on_toggle_single_member, self.single_member_only_chk
        )

    def _form_values(self) -> TeamFormValues:
        """Return the form's current fields, read verbatim (R-20)."""
        return TeamFormValues(
            name=self.name_input.GetValue(),
            relay_plate=self.relay_plate_input.GetValue(),
            notes=self.notes_input.GetValue(),
        )

    def _on_add(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``add_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_add()

    def _on_remove(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``remove_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_remove()

    def _on_save(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``save_btn``: forward the form to the presenter."""
        event.Skip()
        self.presenter.on_save(self._form_values())

    def _on_pick_card(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``pick_card_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_pick_card()

    def _on_image_click(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``image_btn``: pick a file, read it, forward bytes.

        A cancelled picker is a silent no-op. The picked file is read
        here -- the presenter stays pure Python, and the file dialog
        is this view's own OS-native seam (R-71).
        """
        event.Skip()
        path = pick_logo_image_path(self.dialog)
        if path is None:
            return
        self.presenter.on_pick_image(path.read_bytes())

    def _on_row_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``teams_list`` selection: forward its row index.

        No-op when nothing is selected (a stale event after a row it
        pointed to was deleted, say) -- there is no row index to
        forward the presenter could act on.
        """
        event.Skip()
        item = self.teams_list.GetSelection()
        if not item.IsOk():
            return
        row = self._teams_model.GetRow(item)
        self.presenter.on_row_selected(row)

    def _on_toggle_single_member(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle the one-rider-teams filter checkbox."""
        event.Skip()
        self.presenter.on_toggle_single_member(enabled=self.single_member_only_chk.GetValue())

    def show_teams(self, rows: list[TeamRow]) -> None:
        """Render ``teams_list`` (``TeamsView``).

        Dismisses any prior :data:`TEAMS_INFOBAR` warning first: this
        is only ever called after a successful add/remove/save
        refresh (``TeamsPresenter``'s own call order), so the next
        successful action is exactly when a stale warning should
        clear. See ``ui.views._support.associate_model``'s docstring
        for why this also repaints explicitly (unverified remedy).
        """
        self.teams_infobar.Dismiss()
        self._teams_model = TeamsListModel(rows)
        associate_model(self.teams_list, self._teams_model)

    def show_form(  # noqa: PLR0913 -- the passive view fills the five form slots verbatim
        self,
        *,
        name: str,
        relay_plate: str,
        notes: str,
        logo_card: str | None,
        has_image: bool,
    ) -> None:
        """Fill the record form (``TeamsView``, R-20)."""
        self.name_input.SetValue(name)
        self.relay_plate_input.SetValue(relay_plate)
        self.notes_input.SetValue(notes)
        self.logo_preview.SetLabel(format_logo(logo_card, has_image=has_image))

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only).

        ``teams.xrc``'s "Plate (relay)" label carries no frozen name
        to find it by (only ``relay_plate_input`` itself does), so
        its sizer item is located structurally instead: it is always
        the item immediately before ``relay_plate_input`` in their
        shared ``wxFlexGridSizer`` row.
        """
        sizer = self.relay_plate_input.GetContainingSizer()
        items = list(sizer.GetChildren())
        index = next(
            i for i, item in enumerate(items) if item.GetWindow() is self.relay_plate_input
        )
        label = items[index - 1].GetWindow()
        sizer.Show(label, visible)
        sizer.Show(self.relay_plate_input, visible)
        self.dialog.Layout()

    def show_members(self, names: list[str]) -> None:
        """Render ``members_list`` rows read-only (``TeamsView``)."""
        self._members_model = MembersListModel(names)
        associate_model(self.members_list, self._members_model)

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`TEAMS_INFOBAR` (``TeamsView``).

        Non-modal, per the rider editor's own E3.2 decision: it stays
        up until :meth:`show_teams` dismisses it on the next
        successful action, never blocking the operator from
        correcting the form.
        """
        self.teams_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def prompt_team_name(self) -> str | None:
        """Ask for a new team's name via a native prompt (R-20).

        ``wx.TextEntryDialog``, the same seam ``RiderEditor.
        prompt_new_team_name`` uses -- ``teams.xrc`` authors no such
        dialog of its own. Returns ``None`` if the operator cancels,
        exactly the seam functional tests monkeypatch rather than
        drive.
        """
        with wx.TextEntryDialog(self.dialog, "Team name:", "Add team") as prompt:
            if prompt.ShowModal() != wx.ID_OK:
                return None
            return str(prompt.GetValue())

    def _apply_min_size(self) -> None:
        """Force this editor's own width floor, then Fit() the rest.

        See :meth:`rider_editor.RiderEditor._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()


def pick_logo_image_path(parent: wx.Window) -> Path | None:
    """Ask the operator which image to use as the team's logo.

    A thin ``wx.FileDialog`` seam: tests monkeypatch this function
    itself (module-level) rather than ever driving the native picker,
    which no test in this suite can do (harness.py's own module
    docstring). Image bytes are read by the caller; this only picks.
    """
    with wx.FileDialog(
        parent,
        message="Choose team logo image",
        wildcard="Images (*.png;*.jpg;*.jpeg)|*.png;*.jpg;*.jpeg",
        style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
    ) as picker:
        if picker.ShowModal() != wx.ID_OK:
            return None
        return Path(picker.GetPath())
