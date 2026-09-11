# SPDX-License-Identifier: GPL-3.0-only
"""Teams presenters -- team_editor_dlg + add_team_dlg (Phase 3 rework).

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view pairing ``RidersPresenter``/``rider_editor_dlg`` uses, since
the editor reads the roster itself rather than a display-only
projection of it. The editor is now a *read-only record display*: the
selected team's name, relay plate and notes render into a read-only
form, its members into the read-only ``members_list``, and every
mutating action lives elsewhere -- membership stays with the Rider
Editor, a team's record with the Add/Edit Team dialog.

**Key-based selection.** Selection arrives as the row's *display
name*, never a positional index. ``teams_list`` sorts through native
header arrows (:class:`TeamsListModel.Compare`), so a row's screen
position no longer matches its roster position; resolving by name --
unique per ride, the duplicate-name guard -- is what makes a sorted
list select the team actually clicked. An unknown name (a stale event
after a delete) selects nothing at all.

**Add/Edit.** R-81's anchor-rider mandate is amended in this
workstream: a new team is a zero-rider TEAM entry
(:meth:`Roster.create_empty_team`) whose members arrive later through
the Rider Editor. ``add_btn`` and ``edit_btn`` both open the same
dedicated ``add_team_dlg`` window (``ui/views/team_editor``), which
pairs with :class:`AddTeamPresenter` in one of two modes: **Add**
(``editing=None``) creates the entry from the form, **Edit**
(``editing=<entry>``) preloads the entry's own record and writes the
form back through :meth:`Roster.update_entry` and
:meth:`Roster.change_team_plate`. Both modes share the blank and
duplicate-name guards; the dialog reports success as a bool so it
closes only on a real commit, and the editor catches up through
:meth:`TeamsPresenter.on_add_committed` / :meth:`on_edit_committed`.

**Logo picks.** A team's logo is a natural card code alone (the image
logo is retired). The dialog stages its pick
(:attr:`AddTeamPresenter._pending_logo_card`) and commits it with the
rest of the form; :meth:`Roster.random_team_card` draws each pick at
random from the seeded deck, never repeating an assigned card or the
one already staged, so every click visibly changes the preview. Add
and Remove are DRAFT-only
(:func:`~rivercrossing.roster.can_edit_structure`), like every
other structural roster edit (R-15); the roster's own
refusals surface via each view's ``show_validation``, and Remove asks
:meth:`TeamsView.confirm` first.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.roster import (
    EntryType,
    PlateModel,
    RosterError,
    rider_name_key,
)

if TYPE_CHECKING:
    from rivercrossing.roster import Entry, Roster

__all__ = [
    "AddTeamPresenter",
    "AddTeamView",
    "TeamFormValues",
    "TeamRow",
    "TeamsPresenter",
    "TeamsView",
]


@dataclass(frozen=True, slots=True)
class TeamRow:
    """One ``teams_list`` row: name and rider count.

    ``rider_count`` is the team's current size (the ``Riders``
    column), which the list sorts numerically.
    """

    name: str
    rider_count: int


@dataclass(frozen=True, slots=True)
class TeamFormValues:
    """A team form's text fields, forwarded verbatim by the view.

    ``relay_plate`` is whatever ``relay_plate_input`` currently holds
    even on a rider_pooled ride, where the row is hidden -- the
    presenters ignore it there (a pooled team's plate is derived from
    its riders, never set), the passive-view contract.
    """

    name: str
    relay_plate: str
    notes: str


@runtime_checkable
class TeamsView(Protocol):
    """View surface for the teams editor (team_editor_dlg)."""

    def show_teams(self, rows: list[TeamRow]) -> None:
        """Render ``teams_list`` from *rows*, in order."""
        ...

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Fill the read-only record form's three text fields (R-20)."""
        ...

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only)."""
        ...

    def show_members(self, names: list[str]) -> None:
        """Render the read-only ``members_list`` rows."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-operation message (teams_infobar)."""
        ...

    def set_edit_enabled(self, *, enabled: bool) -> None:
        """Gate edit_btn: enabled only while a team is selected."""
        ...

    def confirm(  # noqa: PLR0913 -- the four fields the confirm seam names
        self, title: str, message: str, *, ok_label: str, cancel_label: str
    ) -> bool:
        """Ask a destructive confirm; return whether OK was chosen.

        The view owns the parent window and opens the native confirm;
        the presenter reads only the boolean verdict, so the flow
        stays headless-testable.
        """
        ...


@runtime_checkable
class AddTeamView(Protocol):
    """View surface for the Add/Edit Team dialog (add_team_dlg)."""

    def set_mode(self, *, editing: bool) -> None:
        """Render the dialog's Add or Edit mode: title and label."""
        ...

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only)."""
        ...

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Fill the dialog's three text fields (R-20)."""
        ...

    def show_logo(self, *, card: str | None) -> None:
        """Render the staged card preview (``logo_bmp``)."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-add message on the dialog's infobar."""
        ...


def _team_entries(roster: Roster) -> tuple[Entry, ...]:
    """Return every TEAM entry of *roster*, in creation order."""
    return tuple(entry for entry in roster.entries if entry.type is EntryType.TEAM)


def _duplicate_team_entry(
    roster: Roster, name: str, *, exclude: Entry | None = None
) -> Entry | None:
    """Return a TEAM entry of *roster* whose name collides with *name*.

    A collision compares :func:`~rivercrossing.roster.rider_name_key`
    keys -- trimmed, whitespace-collapsed and case-folded -- so
    ``"Trail Blazers"`` and ``" trail blazers "`` name the same team.
    *exclude* (the entry being renamed) never collides with its own
    name.
    """
    key = rider_name_key(name)
    return next(
        (
            entry
            for entry in _team_entries(roster)
            if entry is not exclude and rider_name_key(entry.display_name) == key
        ),
        None,
    )


def _logo_pick_refusal(roster: Roster) -> str:
    """Return why *roster* offered no logo card to pick.

    :meth:`Roster.random_team_card` returns ``None`` for two different
    causes and the pick site must tell them apart: no
    ``team_logo_seed`` means no card deck exists for this ride at all,
    while a seeded roster that has returned ``None`` has every one of
    its 52 codes claimed by a team.
    """
    if roster.team_logo_seed is None:
        return "no card deck is available for this ride"
    return "every card logo is already in use by a team"


class AddTeamPresenter:
    """Presenter for the Add/Edit Team dialog (add_team_dlg).

    One class, two modes. **Add** (``editing`` None) creates a
    zero-rider TEAM entry (:meth:`Roster.create_empty_team`) whose
    plate follows the ride's plate model -- the dialog's relay row
    (team_relay only) is prefilled with the next free plate, a pooled
    team takes the roster's provisional claim. **Edit** (*editing* an
    existing entry) preloads that entry's record and writes the form
    back onto it. Either way ``on_submit`` reports success as a bool
    so the view can close only on a real commit: a refusal (blank or
    duplicate name, a solo-only ride, the ride has left DRAFT, a
    blank relay plate, ...) leaves the dialog open, showing why on its
    own infobar, and never mutates the roster.
    """

    def __init__(self, view: AddTeamView, roster: Roster, *, editing: Entry | None = None) -> None:
        """Store the collaborators and render the dialog's start state.

        Args:
            view: The add_team_dlg view driving this roster.
            roster: The in-memory roster this presenter reads/writes.
            editing: The team this dialog edits, or ``None`` to add one.
        """
        self.view = view
        self.roster = roster
        self._editing = editing
        self._pending_logo_card: str | None = editing.logo_card if editing is not None else None
        relay = self.roster.plate_model is PlateModel.TEAM_RELAY
        self.view.set_mode(editing=editing is not None)
        self.view.set_relay_plate_visible(visible=relay)
        if editing is None:
            self.view.show_form(
                name="",
                relay_plate=self.roster.next_free_plate() if relay else "",
                notes="",
            )
        else:
            self.view.show_form(
                name=editing.display_name,
                relay_plate=editing.plate if relay else "",
                notes=editing.notes,
            )
        self.view.show_logo(card=self._pending_logo_card)

    def on_submit(self, form: TeamFormValues) -> bool:
        """Commit *form* as a new team, or as the edited one.

        A blank or duplicate name (trimmed, case-insensitive) refuses
        before any roster rule runs -- on an edit the entry's own name
        never collides with itself, so re-casing a name is allowed. A
        relay ride's plate row is forwarded to the roster's own
        non-empty and duplicate guards. A roster refusal (solo-only
        ride, the ride has left DRAFT, ...) shows via
        :meth:`AddTeamView.show_validation` and leaves the roster
        unchanged, never raising past this handler.

        Returns:
            True only once the roster actually holds the change.
        """
        name = form.name.strip()
        if not name:
            self.view.show_validation("enter a team name")
            return False
        if _duplicate_team_entry(self.roster, name, exclude=self._editing) is not None:
            self.view.show_validation(f'a team named "{name}" already exists')
            return False
        try:
            if self._editing is None:
                self._create(form, name)
            else:
                self._write_back(self._editing, form, name)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        return True

    def on_pick_card(self) -> None:
        """Handle pick_card_btn: stage a random unused seeded card.

        Each click draws through :meth:`Roster.random_team_card`,
        excluding the card already staged, so the preview always
        changes. When nothing is left to draw, says why instead of
        silently doing nothing: no card deck at all when the roster
        has no seed, every card in use when the deck is genuinely
        exhausted (:func:`_logo_pick_refusal`).
        """
        code = self.roster.random_team_card(exclude=self._pending_logo_card)
        if code is None:
            self.view.show_validation(_logo_pick_refusal(self.roster))
            return
        self._pending_logo_card = code
        self.view.show_logo(card=code)

    def _create(self, form: TeamFormValues, name: str) -> None:
        """Create a zero-rider team from *form* (the Add path)."""
        entry = self.roster.create_empty_team(
            display_name=name,
            plate=(form.relay_plate if self.roster.plate_model is PlateModel.TEAM_RELAY else None),
            logo_card=self._pending_logo_card,
        )
        if form.notes:
            self.roster.update_entry(entry, notes=form.notes)

    def _write_back(self, entry: Entry, form: TeamFormValues, name: str) -> None:
        """Apply *form* to the edited *entry* (the Edit path).

        Only the fields that actually changed are written, so an
        untouched form commits without an audit event.
        """
        if self.roster.plate_model is PlateModel.TEAM_RELAY and form.relay_plate != entry.plate:
            self.roster.change_team_plate(entry, plate=form.relay_plate)
        changes: dict[str, str] = {}
        if name != entry.display_name:
            changes["display_name"] = name
        if form.notes != entry.notes:
            changes["notes"] = form.notes
        if changes:
            self.roster.update_entry(entry, **changes)
        staged = self._pending_logo_card
        if staged is not None and staged != entry.logo_card:
            self.roster.set_team_logo_card(entry, code=staged)


class TeamsPresenter:
    """Presenter for the teams editor (team_editor_dlg).

    Rows are the roster's TEAM entries, in creation order; the
    selection -- by display name -- drives the read-only record form
    and members list. The editor never edits a record in place any
    more: ``add_btn`` and ``edit_btn`` open ``add_team_dlg``, whose
    own :class:`AddTeamPresenter` commits, and
    :meth:`on_add_committed`/:meth:`on_edit_committed` catch the
    editor up afterwards. Remove asks the view to confirm first, then
    keeps the module docstring's lock shape: roster refusals surface
    through :meth:`TeamsView.show_validation` and leave the roster
    unchanged, never raising past a handler.
    """

    def __init__(self, view: TeamsView, roster: Roster) -> None:
        """Store *view*/*roster*, then render the initial state.

        Args:
            view: The team-editor view driving this roster.
            roster: The in-memory roster this presenter reads/writes.
        """
        self.view = view
        self.roster = roster
        self._selected: Entry | None = None
        self._single_member_only: bool = False
        # Close-persist flag: True once any add/edit/remove/logo edit
        # has actually committed this session (app.py's editor-close
        # save consults :attr:`roster_changed`).
        self._roster_changed = False
        self._load()

    @property
    def selected(self) -> Entry | None:
        """Return the team currently shown in the editor, if any.

        The view reads this to open the edit dialog for it; the
        presenter keeps ownership of the selection so a sorted list
        can never hand back the wrong entry.
        """
        return self._selected

    def on_row_selected(self, display_name: str) -> None:
        """Fill the form from the ``teams_list`` row named *name*.

        Rows are matched by name, not index: ``teams_list`` sorts, so
        a row's position carries no relation to the roster's own
        order. A name that is not currently visible (a stale event, or
        a team hidden by the one-rider filter) selects nothing rather
        than selecting a neighbour.
        """
        entry = next(
            (one for one in self._visible_teams() if one.display_name == display_name), None
        )
        if entry is None:
            return
        self._selected = entry
        self._show_entry(entry)

    def select_by_name(self, display_name: str) -> None:
        """Preselect the named ``teams_list`` row, if it is visible.

        The name-keyed preselect seam ``TeamEditor.select_team_by_name``
        forwards to (the rider-issues dialog uses it to land on a
        team-of-one's form). A thin, documented entry point over
        :meth:`on_row_selected`, which already resolves a row by its
        display name -- the list sorts, so a row position names no
        entry. A no-op when no visible team bears that name.
        """
        self.on_row_selected(display_name)

    def on_toggle_single_member(self, *, enabled: bool) -> None:
        """Handle the one-rider-teams filter, then re-render the list.

        *enabled* is the checkbox's new state: checked shows only TEAM
        entries with a single rider; unchecked shows every TEAM entry.
        """
        self._single_member_only = enabled
        self._refresh_rows()

    def on_add_committed(self) -> None:
        """Re-render after add_team_dlg committed into this roster.

        ``add_team_dlg``'s own :class:`AddTeamPresenter` did the
        creating; this editor only catches its rows/form up -- refresh
        the list, then reset to the blank no-selection form.
        """
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_edit_committed(self) -> None:
        """Re-render after add_team_dlg committed an edit.

        The dialog wrote the record itself; this editor re-renders the
        (possibly renamed) row and resets to the blank no-selection
        form, the same shape :meth:`on_add_committed` uses.
        """
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_remove(self) -> None:
        """Handle remove_btn: confirm, then delete the selected team.

        The destructive confirm comes first (:meth:`TeamsView.confirm`)
        -- a declined confirm leaves the roster untouched. A refusal
        from the roster (recorded data, or the ride has left DRAFT)
        shows via :meth:`TeamsView.show_validation`, naming the
        reason. A no-op if nothing is selected.
        """
        entry = self._selected
        if entry is None:
            return
        if not self.view.confirm(
            "Remove team",
            f'Remove the team "{entry.display_name}"?',
            ok_label="Remove",
            cancel_label="Cancel",
        ):
            return
        try:
            self.roster.delete_entry(entry)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    @property
    def roster_changed(self) -> bool:
        """Return whether this session committed a roster change.

        The app's editor-close persistence hook reads this after the
        modal ends; only real commits (an add, edit, remove or logo
        edit that actually applied) set it, never a refusal, a
        declined confirm or a no-op.
        """
        return self._roster_changed

    def _load(self) -> None:
        """Render the editor's full initial state from the roster."""
        self.view.set_relay_plate_visible(visible=self.roster.plate_model is PlateModel.TEAM_RELAY)
        self._refresh_rows()
        self._show_add_form()

    def _teams(self) -> tuple[Entry, ...]:
        """Return every TEAM entry, in ``teams_list``'s row order."""
        return _team_entries(self.roster)

    def _visible_teams(self) -> tuple[Entry, ...]:
        """Return the TEAM entries the list should currently show.

        The one-rider filter hides every team that has more than one
        rider; unchecked, the full TEAM tuple is returned unchanged.
        """
        teams = self._teams()
        if not self._single_member_only:
            return teams
        return tuple(entry for entry in teams if entry.team_size == 1)

    def _refresh_rows(self) -> None:
        """Re-render ``teams_list`` from the roster."""
        self.view.show_teams(
            [
                TeamRow(name=entry.display_name, rider_count=entry.team_size)
                for entry in self._visible_teams()
            ]
        )

    def _show_entry(self, entry: Entry) -> None:
        """Render *entry*'s read-only form, members and edit gate.

        ``relay_plate_input`` holds the entry's plate only on a
        team_relay ride, where the row is visible and the plate is
        the entry's own; a rider_pooled team's derived plate is never
        offered as text (the row is hidden there).
        """
        relay_plate = entry.plate if self.roster.plate_model is PlateModel.TEAM_RELAY else ""
        self.view.show_form(
            name=entry.display_name,
            relay_plate=relay_plate,
            notes=entry.notes,
        )
        self.view.show_members([rider.full_name for rider in entry.riders])
        self.view.set_edit_enabled(enabled=True)

    def _show_add_form(self) -> None:
        """Reset the editor: nothing selected, blank form, no rows."""
        self._selected = None
        self.view.show_form(name="", relay_plate="", notes="")
        self.view.show_members([])
        self.view.set_edit_enabled(enabled=False)
