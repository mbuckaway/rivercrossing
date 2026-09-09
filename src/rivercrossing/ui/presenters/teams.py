# SPDX-License-Identifier: GPL-3.0-only
"""Teams presenters -- team_editor_dlg + add_team_dlg (W8 rework).

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view pairing ``RidersPresenter``/``rider_editor_dlg`` uses
(E3.2), since the editor reads and writes the roster itself rather
than a display-only projection of it. The editor owns a team's
*record* fields -- display name, relay plate, notes and the logo
card or image -- while membership is read-only here (the read-only
``members_list``) and stays with the Rider Editor. ``teams_list``
rows carry the team's rider count (:class:`TeamRow.rider_count`, the
``Riders`` column) so the operator sees size at a glance.

**Team creation (W8's empty-team shape).** R-81's anchor-rider
mandate is amended in this workstream: a new team is a zero-rider
TEAM entry (:meth:`Roster.create_empty_team`) whose members arrive
later through the Rider Editor -- nothing invents a rider named from
the team's name. The editor's own in-form Add is retired (the W7
rider-editor shape): ``add_btn`` opens the dedicated ``add_team_dlg``
window (``ui/views/team_editor.run_add_team_flow``), which pairs with
a *second* presenter class, :class:`AddTeamPresenter`. It owns the
create logic -- name/notes off the dialog's form plus the staged
logo, a blank or duplicate team name refused (trimmed,
case-insensitive; the same guard applies to a rename on Save), the
relay plate row shown only on team_relay rides and prefilled with the
next free plate -- and reports success as a bool so the view closes
only on a real commit. A roster refusal (solo-only ride, the ride has
left DRAFT, ...) leaves the dialog open, showing why on its own
infobar, and never mutates the roster. A committed Add closes the
dialog; the editor's own :class:`TeamsPresenter` then re-renders
through :meth:`TeamsPresenter.on_add_committed`.

**Logo picks.** The editor's ``on_pick_card``/``on_pick_image`` apply
to the selected team only (each pick is an immediate roster write);
the Add dialog stages its own pending card or image
(``AddTeamPresenter._pending_logo_card``/``_pending_logo_image``)
until Add commits it. A picked card wins over a picked image in both
places, matching the roster's own either-or rule
(:meth:`Roster.set_team_logo_card`/:meth:`Roster.set_team_logo_image`).
Add/Remove are DRAFT-only
(:func:`~rivercrossing.roster.can_edit_structure`), like every other
structural roster edit (R-15); the roster's own refusals surface via
each view's ``show_validation``.

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
    """One ``teams_list`` row: name, rider count and logo state.

    ``rider_count`` is the team's current size (the ``Riders``
    column). ``logo_card`` is the team's card code or ``None``;
    ``has_image`` says a logo image is set instead -- an image wins
    over a card, so a row shows ``"Card"`` only when ``logo_card``
    is set and no image is.
    """

    name: str
    rider_count: int
    logo_card: str | None
    has_image: bool


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
        """Fill the team record form's three text fields (R-20)."""
        ...

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only)."""
        ...

    def show_members(self, names: list[str]) -> None:
        """Render the read-only ``members_list`` rows."""
        ...

    def show_logo(self, *, card: str | None, image: bytes | None) -> None:
        """Render the logo preview bitmap (``logo_bmp``).

        *card* is the logo card code to draw; *image* the logo PNG
        bytes (an image wins). Neither means a blank preview.
        """
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-operation message (teams_infobar)."""
        ...


@runtime_checkable
class AddTeamView(Protocol):
    """View surface for the Add Team dialog (add_team_dlg, W8)."""

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Show/hide the Plate (relay) row (team_relay rides only)."""
        ...

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Fill the dialog's three text fields (R-20)."""
        ...

    def show_logo(self, *, card: str | None, image: bytes | None) -> None:
        """Render the staged logo preview (``logo_bmp``)."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-add message on the dialog's infobar."""
        ...


def _team_entries(roster: Roster) -> tuple[Entry, ...]:
    """Return every TEAM entry of *roster*, in creation order."""
    return tuple(entry for entry in roster.entries if entry.type is EntryType.TEAM)


def _duplicate_team_entry(roster: Roster, name: str, *, exclude: Entry | None = None) -> Entry | None:
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


class AddTeamPresenter:
    """Presenter for the Add Team dialog (add_team_dlg, W8).

    Owns team creation for the whole editor: a zero-rider TEAM entry
    (:meth:`Roster.create_empty_team`) whose plate follows the ride's
    plate model -- the dialog's relay row (team_relay only) is
    prefilled with the next free plate, a pooled team takes the
    roster's provisional claim -- plus the form's notes and the
    staged logo. ``on_submit`` reports success as a bool so the view
    can close only on a real commit: a refusal (blank or duplicate
    name, a solo-only ride, the ride has left DRAFT, a blank relay
    plate, ...) leaves the dialog open, showing why on its own
    infobar, and never mutates the roster.
    """

    def __init__(self, view: AddTeamView, roster: Roster) -> None:
        """Store the collaborators and render the dialog's start state.

        Args:
            view: The add_team_dlg view driving this roster.
            roster: The in-memory roster this presenter reads/writes.
        """
        self.view = view
        self.roster = roster
        self._pending_logo_card: str | None = None
        self._pending_logo_image: bytes | None = None
        relay = self.roster.plate_model is PlateModel.TEAM_RELAY
        self.view.set_relay_plate_visible(visible=relay)
        self.view.show_form(
            name="",
            relay_plate=self.roster.next_free_plate() if relay else "",
            notes="",
        )
        self.view.show_logo(card=None, image=None)

    def on_submit(self, form: TeamFormValues) -> bool:
        """Create *form*'s zero-rider team, or refuse with a message.

        A blank or duplicate name (trimmed, case-insensitive) refuses
        before any roster rule runs; a relay ride's plate row is
        forwarded to the roster's own non-empty and duplicate guards.
        A roster refusal (solo-only ride, the ride has left DRAFT,
        ...) shows via :meth:`AddTeamView.show_validation` and leaves
        the roster unchanged, never raising past this handler.

        Returns:
            True only once the team entry actually exists.
        """
        name = form.name.strip()
        if not name:
            self.view.show_validation("enter a team name")
            return False
        if _duplicate_team_entry(self.roster, name) is not None:
            self.view.show_validation(f'a team named "{name}" already exists')
            return False
        try:
            entry = self.roster.create_empty_team(
                display_name=name,
                plate=form.relay_plate if self.roster.plate_model is PlateModel.TEAM_RELAY else None,
                logo_card=self._pending_logo_card,
                logo_png=self._pending_logo_image,
            )
            if form.notes:
                self.roster.update_entry(entry, notes=form.notes)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        return True

    def on_pick_card(self) -> None:
        """Handle pick_card_btn: stage the next unused seeded card.

        Each click walks the deck past the previously staged card
        (module docstring). A staged card wins over a staged image,
        matching the roster's own either-or rule. When every one of
        the 52 codes is already claimed (or the roster has no seed),
        says so instead of silently doing nothing.
        """
        code = self.roster.next_team_logo_card(after=self._pending_logo_card)
        if code is None:
            self.view.show_validation("every card logo is already in use by a team")
            return
        self._pending_logo_image = None
        self._pending_logo_card = code
        self.view.show_logo(card=code, image=None)

    def on_pick_image(self, image: bytes) -> None:
        """Handle a picked logo image: stage *image* for the Add.

        An image wins over a card -- any staged ``logo_card`` clears,
        matching :meth:`Roster.set_team_logo_image`.
        """
        self._pending_logo_card = None
        self._pending_logo_image = image
        self.view.show_logo(card=None, image=image)


class TeamsPresenter:
    """Presenter for the teams editor (team_editor_dlg, Phase 4).

    Rows are the roster's TEAM entries, in creation order; the
    selection drives the record form and the read-only members list.
    W8: the editor never adds (its own form is record-only --
    ``add_btn`` opens ``add_team_dlg``, and :meth:`on_add_committed`
    catches the roster up when that dialog commits). Save/Remove
    follow the module docstring's lock shape: roster refusals surface
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
        # W8 close-persist flag: True once any add/save/remove/logo
        # edit has actually committed this session (app.py's
        # editor-close save consults :attr:`roster_changed`).
        self._roster_changed = False
        self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from ``teams_list`` row *index*.

        The selection makes the form that team's editor; nothing is
        staged in the editor any more (W8 -- Add lives in its own
        dialog), so the entry's own record and logo simply show.
        """
        entry = self._visible_teams()[index]
        self._selected = entry
        self._show_entry(entry)

    def on_toggle_single_member(self, *, enabled: bool) -> None:
        """Handle the one-rider-teams filter, then re-render the list.

        *enabled* is the checkbox's new state: checked shows only TEAM
        entries with a single rider; unchecked shows every TEAM entry.
        """
        self._single_member_only = enabled
        self._refresh_rows()

    def on_add_committed(self) -> None:
        """Re-render after add_team_dlg committed into this roster.

        W8: ``add_team_dlg``'s own :class:`AddTeamPresenter` did the
        creating; this editor only catches its rows/form up -- refresh
        the list, then reset to the blank no-selection form.
        """
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_remove(self) -> None:
        """Handle remove_btn: delete the selected team (R-15).

        A refusal (recorded data, or the ride has left DRAFT) shows
        via :meth:`TeamsView.show_validation`, naming the reason. A
        no-op if nothing is selected.
        """
        if self._selected is None:
            return
        try:
            self.roster.delete_entry(self._selected)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_save(self, form: TeamFormValues) -> None:
        """Handle save_btn: apply the form to the selected team.

        A rename onto another team's name (trimmed, case-insensitive)
        refuses before anything is touched. A relay team's plate
        routes through :meth:`Roster.change_team_plate` (its own
        DRAFT lock); the name and notes through
        :meth:`Roster.update_entry`. A refusal (the ride has left
        DRAFT) shows via :meth:`TeamsView.show_validation`. A no-op
        if nothing is selected.
        """
        entry = self._selected
        if entry is None:
            return
        name = form.name.strip()
        if (
            name != entry.display_name
            and _duplicate_team_entry(self.roster, name, exclude=entry) is not None
        ):
            self.view.show_validation(f'a team named "{name}" already exists')
            return
        try:
            if (
                self.roster.plate_model is PlateModel.TEAM_RELAY
                and form.relay_plate != entry.plate
            ):
                self.roster.change_team_plate(entry, plate=form.relay_plate)
            changes: dict[str, str] = {}
            if form.name != entry.display_name:
                changes["display_name"] = form.name
            if form.notes != entry.notes:
                changes["notes"] = form.notes
            if changes:
                self.roster.update_entry(entry, **changes)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        self._roster_changed = True
        self._refresh_rows()

    def on_pick_card(self) -> None:
        """Handle pick_card_btn on the selected team: cycle its card.

        Each click advances to the next unused code in the roster's
        seeded sequence (skipping every other team's card), clearing
        any logo image -- a picked card wins. When every one of the
        52 codes is already claimed (or the roster has no seed), says
        so instead of silently doing nothing.
        """
        entry = self._selected
        if entry is None:
            return
        code = self.roster.next_team_logo_card(after=entry.logo_card)
        if code is None:
            self.view.show_validation("every card logo is already in use by a team")
            return
        self.roster.set_team_logo_card(entry, code=code)
        self._roster_changed = True
        self._refresh_rows()
        self._show_entry(entry)

    def on_pick_image(self, image: bytes) -> None:
        """Handle a picked logo image on the selected team.

        The image is set on it -- an image wins over a card, and
        :meth:`Roster.set_team_logo_image` clears any ``logo_card``.
        """
        entry = self._selected
        if entry is None:
            return
        self.roster.set_team_logo_image(entry, image=image)
        self._roster_changed = True
        self._refresh_rows()
        self._show_entry(entry)

    @property
    def roster_changed(self) -> bool:
        """Return whether this session committed a roster change (W8).

        The app's editor-close persistence hook reads this after the
        modal ends; only real commits (an add, save, remove or logo
        edit that actually applied) set it, never a refusal or a
        no-op.
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
                TeamRow(
                    name=entry.display_name,
                    rider_count=entry.team_size,
                    logo_card=entry.logo_card,
                    has_image=entry.logo_png is not None,
                )
                for entry in self._visible_teams()
            ]
        )

    def _show_entry(self, entry: Entry) -> None:
        """Render *entry*'s record form, logo and read-only members.

        ``relay_plate_input`` holds the entry's plate only on a
        team_relay ride, where the row is visible and the plate is
        the entry's own; a rider_pooled team's derived plate is never
        offered as settable text (the row is hidden there).
        """
        relay_plate = entry.plate if self.roster.plate_model is PlateModel.TEAM_RELAY else ""
        self.view.show_form(
            name=entry.display_name,
            relay_plate=relay_plate,
            notes=entry.notes,
        )
        self.view.show_logo(card=entry.logo_card, image=entry.logo_png)
        self.view.show_members([rider.full_name for rider in entry.riders])

    def _show_add_form(self) -> None:
        """Reset the editor: nothing selected, blank form, no logo."""
        self._selected = None
        self.view.show_form(name="", relay_plate="", notes="")
        self.view.show_logo(card=None, image=None)
        self.view.show_members([])
