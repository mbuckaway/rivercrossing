# SPDX-License-Identifier: GPL-3.0-only
"""``TeamEditor``: team_editor_dlg (Phase 3 rework), on a real Roster.

``team_editor_dlg`` is wired to a real, in-memory
:class:`~rivercrossing.roster.Roster` that
:class:`~rivercrossing.ui.presenters.teams.TeamsPresenter`
(``ui.presenters.teams``) reads directly --
:class:`TeamEditor` takes ``roster=`` and constructs its own
presenter (mirroring ``views/rider_editor.py``'s
presenter-inside-the-view wiring), binding Remove/Edit/Add/row
selection to it. The two lists' rows and columns live here
(``teams.xrc``'s own header explains why --
``wxDataViewListCtrl`` would overwrite the frozen name).

Phase 3's rework makes the editor a read-only record display: the
``Team | Riders`` list (two columns -- :data:`COLUMN_LABELS`), a
read-only name/relay-plate/notes form and the read-only members list.
The logo surface (``Logo`` column, bitmap preview, Pick card / Image
/ Remove logo) and the in-place Save are gone; a team's record is
edited in the Add/Edit Team dialog, which ``add_btn`` and ``edit_btn``
both open through :func:`run_add_team_flow` (``editing=`` an entry for
the edit route, with a row double-click as the second way in).

**Native sorting.** Each column is appended with
``TEAMS_LIST_COLUMN_FLAGS`` -- sortable *and* resizable, since an
explicit ``flags=`` argument replaces rather than extends wx's
default flags -- and sorted through
:class:`TeamsListModel.Compare` -- the Team column by case-folded
name, Riders numerically -- so the header arrows the platform draws
actually reorder the rows. The Team column opens at
:data:`COL_NAME_WIDTH` (double the platform's 80 DIP default); Riders
keeps :data:`COL_RIDERS_WIDTH`. The dialog opens on an ascending Team
sort, and :meth:`TeamEditor._apply_sort` re-applies the operator's
current sort after every ``show_teams`` rebuild (replacing the model
drops the control's sort key). Because the rows move under the
selection, the view forwards the selected row's *display name* --
never a positional index (``_on_row_selected``).

W8 (like W7's rider editor) retired the in-form Add: ``add_btn``
opens the dedicated ``add_team_dlg`` window, whose own presenter
creates zero-rider TEAM entries and now also writes an edited team's
record back. Remove asks for a destructive confirm
(:meth:`TeamEditor.confirm`) before the presenter deletes anything.

A refused operation (add/remove after start, a blank or duplicate
team name, ...) renders as a code-side ``wxInfoBar``
(:data:`TEAMS_INFOBAR`) -- the same measured pattern
``rider_editor.py``'s ``RiderEditor`` uses, slide effects disabled for
the same reason. ``_find`` is shared via
``ui.views._support.find_control``.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.feed_model import card_asset_key_or_none
from rivercrossing.ui.presenters.teams import (
    AddTeamPresenter,
    TeamFormValues,
    TeamRow,
    TeamsPresenter,
)
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import associate_model, default_card_images, find_control

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.roster import Entry, Roster

__all__ = [
    "ADD_TEAM_INFOBAR",
    "CARD_LOGO_BOX",
    "COLUMN_LABELS",
    "COL_MEMBER",
    "COL_NAME",
    "COL_NAME_WIDTH",
    "COL_RIDERS",
    "COL_RIDERS_WIDTH",
    "LOGO_PREVIEW_BOX",
    "MEMBERS_COLUMN_LABELS",
    "MEMBERS_MIN_HEIGHT",
    "MIN_SIZE",
    "NOTES_MIN_LINES",
    "TEAMS_INFOBAR",
    "TEAMS_LIST_COLUMN_FLAGS",
    "AddTeamDialog",
    "MembersListModel",
    "TeamEditor",
    "TeamsListModel",
    "logo_fit_size",
    "run_add_team_flow",
]

COL_NAME = 0
COL_RIDERS = 1

# xrc-windows.md C (Teams Editor), reworked: "Team | Riders".
COLUMN_LABELS: tuple[str, ...] = ("Team", "Riders")

# Both columns default to wxDVC_DEFAULT_WIDTH (80 DIP). The first
# ("Team") column carries the team name the operator reads, so it
# opens at double that; the second ("Riders") keeps the platform
# default -- it is the last column, which wx stretches to fill the
# control, so it needs no width of its own.
COL_NAME_WIDTH = 160
COL_RIDERS_WIDTH = 80

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the
# column NSTableColumnNoResizing -- so both bits must be spelled out.
TEAMS_LIST_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE

COL_MEMBER = 0
MEMBERS_COLUMN_LABELS: tuple[str, ...] = ("Member",)

# ui/ids.py is generated from the .xrc files (R-05); teams_infobar
# never appears there since XRC cannot author a wxInfoBar at all
# (teams.xrc's own header, rider_editor.py's precedent).
TEAMS_INFOBAR = "teams_infobar"

# add_team_dlg's own code-side infobar (W8), the same wxInfoBar
# exception.
ADD_TEAM_INFOBAR = "add_team_infobar"

# The Add/Edit dialog's two modes: the caption and the OK button's
# label follow whichever one this dialog was opened in.
ADD_MODE_TITLE = "Add Team"
EDIT_MODE_TITLE = "Edit Team"
ADD_MODE_LABEL = "Add"
EDIT_MODE_LABEL = "Save"

# Logo-preview bounds (px): the Add/Edit dialog's card preview renders
# the packaged card bitmap at the card's 3:4 ratio scaled into a 96x128
# box. logo_bmp itself carries SetMaxSize(LOGO_PREVIEW_BOX) so no
# bitmap can push the dialog's button row off the dialog.
LOGO_PREVIEW_BOX = (128, 128)
CARD_LOGO_BOX = (96, 128)

# The Notes box's code-side floor (teams.xrc declares the style):
# wxTE_MULTILINE plus a minimum tall enough for three text lines, so
# a fresh team's notes read as a text area, not a one-line field.
NOTES_MIN_LINES = 3
_TEXT_CTRL_VERTICAL_PADDING = 8

# members_list's bounded floor (px): the Members box does not grow
# with the dialog -- teams_list takes the extra height and a long
# member list scrolls inside its own box (teams.xrc's own header).
MEMBERS_MIN_HEIGHT = 120

# teams.xrc notes the XRC no-window-minsize rule; this is the
# editor's own code-side floor (SetMinSize + Fit, the RiderEditor
# shape) -- wide enough for the reworked two ~50%-wide panes (two
# list columns + the record form) to stay usable on a 1366x768 field
# laptop (UX-DESKTOP §6).
MIN_SIZE = (940, 560)


class TeamsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only, sortable model over ``TeamRow`` for ``teams_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.

    :meth:`Compare` is what makes the native header arrows work: the
    control hands it two items and the model column, and the model
    answers the Ordering on the *rows* those items index.
    ``DataViewIndexListModel.GetRow`` is the item-to-row mapping.
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
        team = self._rows[row]
        if col == COL_RIDERS:
            return str(team.rider_count)
        return team.name

    def Compare(  # noqa: PLR0913, PLR0917 -- wx's own four-argument callback shape
        self,
        item1: Any,  # noqa: ANN401 -- wx ships no stubs
        item2: Any,  # noqa: ANN401 -- wx ships no stubs
        col: int,
        ascending: bool,  # noqa: FBT001 -- wx's own callback argument
    ) -> int:
        """Return the Ordering of *item1* versus *item2* on *col*.

        Team compares case-folded names (so "alpha" sorts beside
        "Alpha", never after "Zulu"); Riders compares rider counts as
        numbers (so a 2-rider team precedes a 10-rider one), falling
        back to the name so equal sizes keep a stable, meaningful
        order. *ascending* is the header arrow's own direction.
        """
        first = self._rows[self.GetRow(item1)]
        second = self._rows[self.GetRow(item2)]
        if col == COL_RIDERS:
            result = _ordering(first.rider_count, second.rider_count)
            if result == 0:
                result = _ordering(first.name.casefold(), second.name.casefold())
        else:
            result = _ordering(first.name.casefold(), second.name.casefold())
        return result if ascending else -result


def _ordering[T: (str, int)](first: T, second: T) -> int:
    """Return -1, 0 or 1: how *first* orders against *second*."""
    if first == second:
        return 0
    return -1 if first < second else 1


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


def logo_fit_size(  # noqa: PLR0913 -- (width, height, within, upscale): the pure fit rule's inputs
    width: int,
    height: int,
    *,
    within: tuple[int, int],
    upscale: bool = False,
) -> tuple[int, int]:
    """Return *width*/*height* scaled into the *within* box.

    The pure fit rule behind the logo previews: card bitmaps render
    into their fixed 3:4 box with ``upscale`` True, so the packaged
    24x32/48x64 faces both land at the same preview size. The aspect
    ratio survives, up to one rounding pixel per side.
    """
    box_w, box_h = within
    if not upscale and width <= box_w and height <= box_h:
        return (width, height)
    scale = min(box_w / width, box_h / height)
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _scaled_bitmap(
    bitmap: Any,  # noqa: ANN401 -- wx ships no stubs
    *,
    within: tuple[int, int],
    upscale: bool,
) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return *bitmap* scaled into *within* (see :func:`logo_fit_size`).

    A bitmap already at or under the box (and not asked to upscale)
    is returned as-is; anything else rescales through ``wx.Image``
    with the high-quality filter so the preview never distorts.
    """
    image = wx.Image(bitmap)
    width, height = image.GetWidth(), image.GetHeight()
    fitted = logo_fit_size(width, height, within=within, upscale=upscale)
    if fitted == (width, height):
        return bitmap
    return wx.Bitmap(image.Rescale(*fitted, wx.IMAGE_QUALITY_HIGH))


def _logo_bitmap(card: str | None) -> Any:  # noqa: ANN401
    """Return the bounded card-preview bitmap for *card*.

    A card code draws its packaged card bitmap scaled into
    :data:`CARD_LOGO_BOX` -- the card's 3:4 ratio -- resolved the same
    way ``main_frame``'s crossings feed resolves one; unknown codes
    and no code at all render a blank ``wx.NullBitmap``.
    """
    if card is not None:
        key = card_asset_key_or_none(card)
        if key is not None:
            return _scaled_bitmap(
                default_card_images().bitmap(key), within=CARD_LOGO_BOX, upscale=True
            )
    return wx.NullBitmap


def _floor_notes_min_height(notes_input: wx.TextCtrl) -> None:
    """Floor the Notes box at :data:`NOTES_MIN_LINES` text lines.

    ``teams.xrc`` declares ``wxTE_MULTILINE``; the style alone leaves
    the control one line tall inside its flex-grid row, so the row's
    minimum is computed from the control's own font metrics
    (measured-safe across the 90-150% text zoom) plus a native bezel
    allowance. Shared by the editor's and the Add dialog's Notes box.
    """
    line_height = notes_input.GetCharHeight()
    minimum = line_height * NOTES_MIN_LINES + _TEXT_CTRL_VERTICAL_PADDING
    notes_input.SetMinSize(wx.Size(-1, minimum))


def _set_relay_row_visible(relay_plate_input: wx.TextCtrl, *, visible: bool) -> None:
    """Show/hide the Plate (relay) row and its label.

    The row's "Plate (relay)" label carries no frozen name to find it
    by (only ``relay_plate_input`` itself does), so its sizer item is
    located structurally instead: it is always the item immediately
    before ``relay_plate_input`` in their shared ``wxFlexGridSizer``
    row. Shared by ``team_editor_dlg`` and ``add_team_dlg`` (each
    caller Layout()s its own dialog afterwards).
    """
    sizer = relay_plate_input.GetContainingSizer()
    items = list(sizer.GetChildren())
    index = next(i for i, item in enumerate(items) if item.GetWindow() is relay_plate_input)
    label = items[index - 1].GetWindow()
    sizer.Show(label, visible)
    sizer.Show(relay_plate_input, visible)


def _build_infobar(dialog: wx.Dialog, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build the code-side InfoBar named *name*, wrapped on top.

    ``teams.xrc``'s dialogs carry no reserved InfoBar slot, so each
    dialog's existing sizer is kept alive and nested inside a new
    outer vertical one instead of edited in the frozen XRC. Measured
    (wxPython 4.3.1 / wxWidgets 3.3.3): calling ``Dismiss()``/
    ``ShowMessage()`` on a ``wx.InfoBar`` with its default slide
    effect never returns -- disabling both effects here is what makes
    ``show_validation`` safe, the identical fix
    ``rider_editor._build_infobar`` documents.
    """
    bar = wx.InfoBar(dialog)
    bar.SetName(name)
    bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
    content = dialog.GetSizer()
    outer = wx.BoxSizer(wx.VERTICAL)
    outer.Add(bar, 0, wx.EXPAND)
    outer.Add(content, 1, wx.EXPAND)
    dialog.SetSizer(outer, deleteOld=False)
    return bar


class TeamEditor:
    """Code-side behaviour for ``team_editor_dlg`` (Phase 3 rework).

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
            dialog: The ``wx.Dialog`` the app bootstrap already loaded
                from ``teams.xrc``.
            roster: The in-memory :class:`~rivercrossing.roster.
                Roster` this editor reads directly -- never a
                ``DataSource`` projection of one.
        """
        self.dialog = dialog

        self.teams_list = self._find(ids.TEAMS_LIST, wx.dataview.DataViewCtrl)
        self.single_member_only_chk = self._find(ids.SINGLE_MEMBER_ONLY_CHK, wx.CheckBox)
        # The operator's current header sort, re-applied whenever the
        # model is rebuilt (a new model drops the control's sort key).
        self._sort_column: int = COL_NAME
        self._sort_ascending: bool = True
        self._build_team_columns()
        # Replaced by the presenter's own show_teams() call below,
        # before any event can fire -- typed non-optional so
        # _on_row_selected never has to narrow it.
        self._teams_model: TeamsListModel = TeamsListModel([])

        self.name_input = self._find(ids.NAME_INPUT, wx.TextCtrl)
        self.relay_plate_input = self._find(ids.RELAY_PLATE_INPUT, wx.TextCtrl)
        self.notes_input = self._find(ids.NOTES_INPUT, wx.TextCtrl)
        self._apply_notes_min_height()
        self.members_list = self._find(ids.MEMBERS_LIST, wx.dataview.DataViewCtrl)
        self._build_member_columns()
        self.members_list.SetMinSize(wx.Size(-1, MEMBERS_MIN_HEIGHT))
        # Replaced by the presenter's own show_members() call below.
        self._members_model: MembersListModel = MembersListModel([])

        self.add_btn = self._find(ids.ADD_BTN, wx.Button)
        self.edit_btn = self._find(ids.EDIT_BTN, wx.Button)
        self.remove_btn = self._find(ids.REMOVE_BTN, wx.Button)

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

    def _apply_notes_min_height(self) -> None:
        """Floor the Notes box at :data:`NOTES_MIN_LINES` text lines."""
        _floor_notes_min_height(self.notes_input)

    def _build_team_columns(self) -> None:
        """Append ``teams_list``'s two sortable, resizable columns."""
        for col, (label, width) in enumerate(
            zip(COLUMN_LABELS, (COL_NAME_WIDTH, COL_RIDERS_WIDTH), strict=True)
        ):
            self.teams_list.AppendTextColumn(
                label, col, width=width, flags=TEAMS_LIST_COLUMN_FLAGS
            )

    def _build_member_columns(self) -> None:
        """Append ``members_list``'s one column."""
        for col, label in enumerate(MEMBERS_COLUMN_LABELS):
            self.members_list.AppendTextColumn(label, col)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`TEAMS_INFOBAR`, wrapped on top.

        See :func:`_build_infobar`'s docstring (this module) for the
        measured slide-effect hang and the shared wrapper.
        """
        return _build_infobar(self.dialog, TEAMS_INFOBAR)

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_add, self.add_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_edit, self.edit_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_remove, self.remove_btn)
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_row_selected, self.teams_list
        )
        # A double-click on a row is the second way into the edit
        # dialog (the operator's row, the same handler as edit_btn).
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_row_activated, self.teams_list
        )
        # Remember the operator's header arrow, so the next show_teams
        # rebuild can put it back.
        self.dialog.Bind(
            wx.dataview.EVT_DATAVIEW_COLUMN_SORTED, self._on_column_sorted, self.teams_list
        )
        self.dialog.Bind(
            wx.EVT_CHECKBOX, self._on_toggle_single_member, self.single_member_only_chk
        )

    def _on_add(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``add_btn``: open the Add Team dialog.

        The add dialog's own :class:`AddTeamPresenter` commits, and a
        real commit refreshes this editor's rows/form via
        :meth:`TeamsPresenter.on_add_committed` -- nothing else would
        tell this open editor the roster changed underneath it.
        """
        event.Skip()
        if run_add_team_flow(self.dialog, self.presenter.roster):
            self.presenter.on_add_committed()

    def _on_edit(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``edit_btn``: edit the selected team's record.

        A no-op when nothing is selected (edit_btn is disabled then,
        but the presenter stays the single source of truth). The
        dialog writes the entry back itself; a real commit refreshes
        this editor through :meth:`TeamsPresenter.on_edit_committed`.
        """
        event.Skip()
        entry = self.presenter.selected
        if entry is None:
            return
        if run_add_team_flow(self.dialog, self.presenter.roster, editing=entry):
            self.presenter.on_edit_committed()

    def _on_row_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``teams_list`` double-click: the same edit route."""
        event.Skip()
        entry = self.presenter.selected
        if entry is None:
            return
        if run_add_team_flow(self.dialog, self.presenter.roster, editing=entry):
            self.presenter.on_edit_committed()

    def _on_remove(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``remove_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_remove()

    def _on_column_sorted(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Remember the header sort the operator just chose."""
        event.Skip()
        column = self.teams_list.GetSortingColumn()
        if column is None:
            return
        self._sort_column = column.GetModelColumn()
        self._sort_ascending = column.IsSortOrderAscending()

    def _on_row_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a ``teams_list`` selection: forward the row's name.

        The list sorts, so a row index no longer names a roster entry;
        the view reads the row's own Team cell and the presenter
        resolves that display name. No-op when nothing is selected (a
        stale event after a row it pointed to was deleted, say).
        """
        event.Skip()
        item = self.teams_list.GetSelection()
        if not item.IsOk():
            return
        row = self._teams_model.GetRow(item)
        self.presenter.on_row_selected(self._teams_model.GetValueByRow(row, COL_NAME))

    def _on_toggle_single_member(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle the one-rider-teams filter checkbox."""
        event.Skip()
        self.presenter.on_toggle_single_member(enabled=self.single_member_only_chk.GetValue())

    def _apply_sort(self) -> None:
        """Re-apply the remembered header sort to the current model.

        ``show_teams`` replaces the model, which drops the sort key the
        control was holding; setting it on the column again and asking
        the model to resort restores exactly the order the operator
        left the list in.
        """
        column = self.teams_list.GetColumn(self._sort_column)
        if column is None:
            return
        column.SetSortOrder(self._sort_ascending)
        self._teams_model.Resort()

    def show_teams(self, rows: list[TeamRow]) -> None:
        """Render ``teams_list`` (``TeamsView``).

        Dismisses any prior :data:`TEAMS_INFOBAR` warning first: this
        is only ever called after a successful add/edit/remove
        refresh (``TeamsPresenter``'s own call order), so the next
        successful action is exactly when a stale warning should
        clear. See ``ui.views._support.associate_model``'s docstring
        for why this also repaints explicitly (unverified remedy).
        """
        self.teams_infobar.Dismiss()
        self._teams_model = TeamsListModel(rows)
        associate_model(self.teams_list, self._teams_model)
        self._apply_sort()

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Fill the record form's text fields (``TeamsView``)."""
        self.name_input.SetValue(name)
        self.relay_plate_input.SetValue(relay_plate)
        self.notes_input.SetValue(notes)

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only).

        ``TeamsView`` member; see :func:`_set_relay_row_visible`'s
        docstring for the structural label lookup.
        """
        _set_relay_row_visible(self.relay_plate_input, visible=visible)
        self.dialog.Layout()

    def set_edit_enabled(self, *, enabled: bool) -> None:
        """Toggle ``edit_btn`` on a selection (``TeamsView``)."""
        self.edit_btn.Enable(enabled)

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

    def confirm(  # noqa: PLR0913 -- the four fields the confirm seam names
        self, title: str, message: str, *, ok_label: str, cancel_label: str
    ) -> bool:
        """Ask a destructive confirm over this dialog (``TeamsView``).

        The native confirm is this view's own seam
        (``ui.std_dialogs.show_confirm``); the presenter reads only the
        boolean verdict, so the flow stays headless-testable.
        """
        result = std_dialogs.show_confirm(self.dialog, title, message, ok_label, cancel_label)
        ok_id: int = wx.ID_OK  # mypy: an int-typed local isolates wx's own Any
        return result == ok_id

    def _apply_min_size(self) -> None:
        """Force this editor's own width floor, then Fit() the rest.

        See :meth:`rider_editor.RiderEditor._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()


class AddTeamDialog:
    """Code-side behaviour for ``add_team_dlg`` (R-20).

    Implements ``AddTeamView`` (``ui.presenters.teams``) over its own
    :class:`~rivercrossing.ui.presenters.teams.AddTeamPresenter`
    instance, in whichever mode it was opened: Add (blank) or Edit
    (preloaded with an existing team). The dialog pairs per-open like
    ``rider_editor.AddRiderDialog``: it never renders
    ``team_editor_dlg``'s own rows, and a live ``TeamEditor`` sees the
    committed change through ``run_add_team_flow``'s ``True`` result,
    which refreshes the editor's own presenter.
    """

    def __init__(self, dialog: wx.Dialog, *, roster: Roster, editing: Entry | None = None) -> None:
        """Decorate an already-loaded ``add_team_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``run_add_team_flow`` loaded
                from ``teams.xrc``.
            roster: The in-memory roster this dialog reads/writes.
            editing: The team to edit, or ``None`` to add a new one.
        """
        self.dialog = dialog

        self.name_input = self._find(ids.NAME_INPUT, wx.TextCtrl)
        self.relay_plate_input = self._find(ids.RELAY_PLATE_INPUT, wx.TextCtrl)
        self.notes_input = self._find(ids.NOTES_INPUT, wx.TextCtrl)
        _floor_notes_min_height(self.notes_input)
        self.logo_bmp = self._find(ids.LOGO_BMP, wx.StaticBitmap)
        self.logo_bmp.SetMaxSize(wx.Size(*LOGO_PREVIEW_BOX))
        self.pick_card_btn = self._find(ids.PICK_CARD_BTN, wx.Button)
        self.ok_btn = self._find("wxID_OK", wx.Button)

        self.add_team_infobar = _build_infobar(self.dialog, ADD_TEAM_INFOBAR)

        self.presenter = AddTeamPresenter(self, roster, editing=editing)

        self._bind_events()

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

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_add, self.ok_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_pick_card, self.pick_card_btn)

    def _on_add(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK`` ("Add"/"Save"): commit, close if it did.

        Measured: ``wxID_OK`` is a stock id wx auto-binds to
        ``EndModal(wx.ID_OK)`` on any ``EVT_BUTTON`` whose handler
        calls ``event.Skip()`` (the note ``AddRiderDialog._on_add``
        carries), so *event* is never skipped here: this handler is
        the only thing allowed to decide whether the dialog closes. A
        refused commit (blank or duplicate name, a roster refusal,
        ...) leaves the dialog open, showing why on
        :data:`ADD_TEAM_INFOBAR`, so the operator can correct or
        Cancel -- never a silent, unexplained non-close.
        """
        if self.presenter.on_submit(self._form_values()):
            self.dialog.EndModal(wx.ID_OK)

    def _form_values(self) -> TeamFormValues:
        """Return the dialog's current fields, read verbatim (R-20)."""
        return TeamFormValues(
            name=self.name_input.GetValue(),
            relay_plate=self.relay_plate_input.GetValue(),
            notes=self.notes_input.GetValue(),
        )

    def _on_pick_card(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``pick_card_btn``: forward to the presenter."""
        event.Skip()
        self.presenter.on_pick_card()

    # ---------------------------------------------------- AddTeamView

    def set_mode(self, *, editing: bool) -> None:
        """Render the dialog's Add or Edit mode (``AddTeamView``).

        The caption and the OK button's label are the only things that
        differ between the two modes; the fields, the plate row and the
        card preview behave identically.
        """
        self.dialog.SetTitle(EDIT_MODE_TITLE if editing else ADD_MODE_TITLE)
        self.ok_btn.SetLabel(EDIT_MODE_LABEL if editing else ADD_MODE_LABEL)
        self.dialog.Layout()

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only)."""
        _set_relay_row_visible(self.relay_plate_input, visible=visible)
        self.dialog.Layout()

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Fill the dialog's three text fields (R-20)."""
        self.name_input.SetValue(name)
        self.relay_plate_input.SetValue(relay_plate)
        self.notes_input.SetValue(notes)

    def show_logo(self, *, card: str | None) -> None:
        """Render the staged card preview (``logo_bmp``)."""
        self.logo_bmp.SetBitmap(_logo_bitmap(card))
        self.dialog.Layout()

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`ADD_TEAM_INFOBAR`.

        ``AddTeamView`` member: non-modal, mirroring the editor's own
        refusal surface -- it stays up until the next successful commit
        re-renders (or the dialog closes), never blocking the operator
        from correcting the form.
        """
        self.add_team_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()


def run_add_team_flow(parent: wx.Window, roster: Roster, *, editing: Entry | None = None) -> bool:
    """Open the Add/Edit Team dialog; commit only if the operator Adds.

    ``team_editor_dlg``'s own ``add_btn``/``edit_btn`` handlers call
    this. The dialog pairs with its own
    :class:`~rivercrossing.ui.presenters.teams.AddTeamPresenter`
    instance over the same live roster; on a committed change the
    caller refreshes its own rows/form through
    :meth:`~rivercrossing.ui.presenters.teams.TeamsPresenter.on_add_committed`
    or
    :meth:`~rivercrossing.ui.presenters.teams.TeamsPresenter.on_edit_committed`.

    Args:
        parent: The window to return focus to once ``add_team_dlg``
            ends.
        roster: The roster a clean commit writes into.
        editing: The team to edit, or ``None`` for the Add route.

    Returns:
        Whether a commit actually landed.
    """
    window = wx.xrc.XmlResource.Get().LoadDialog(None, ids.ADD_TEAM_DLG)
    if window is None:
        return False
    try:
        AddTeamDialog(window, roster=roster, editing=editing)
        default_button = dialogs.default_button_for(ids.ADD_TEAM_DLG)
        if default_button is not None:
            dialogs.set_default_button(window, default_button)
        first_field = dialogs.first_field_for(ids.ADD_TEAM_DLG)
        if first_field is not None:
            dialogs.set_initial_focus(window, first_field)
        result = dialogs.run_dialog(window, opener=parent)
    finally:
        # Fault A: construction now runs inside the close guard -- a
        # post-load raise (AddTeamDialog's _find can exhaust its 25
        # retries under hosted-runner load) must not leave the
        # just-loaded dialog fully alive, rerun-masked until the reap
        # pin catches it.
        if not window.IsBeingDeleted():
            window.Destroy()
    ok_id: int = wx.ID_OK  # mypy: an int-typed local isolates wx's own Any
    return result == ok_id
