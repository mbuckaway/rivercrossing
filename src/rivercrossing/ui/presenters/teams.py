# SPDX-License-Identifier: GPL-3.0-only
"""Teams presenter -- team_editor_dlg (Phase 4, reworked Add flow).

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

**Team creation (W8's empty-team shape).** R-81's original anchor-rider
mandate is amended in this workstream: a new team is a zero-rider TEAM
entry (:meth:`Roster.create_empty_team`) whose members arrive later
through the Rider Editor -- nothing invents a rider named from the
team's name. :meth:`TeamsPresenter.on_add` reads ``name``/``notes``
straight from the form (no native name prompt) and validates the name
non-blank and not a duplicate of any existing team's (trimmed,
case-insensitive; the same guard applies to a rename on Save). The
entry's plate follows the roster's own plate-model rules -- a
team_relay team carries the next free relay plate, a rider_pooled one
the provisional next-free claim :meth:`Roster.create_empty_team`
documents -- and the staged logo applies: a picked card is passed
straight to the create, a picked image lands through
:meth:`Roster.set_team_logo_image` (an image wins over a card, so
either clears the other). Add/Remove are DRAFT-only
(:func:`~rivercrossing.roster.can_edit_structure`), like every other
structural roster edit (R-15); the roster's own refusals surface via
:meth:`TeamsView.show_validation`.

**Staged logo picks.** ``on_pick_card``/``on_pick_image`` apply to
the selected team when one is selected; with nothing selected (the
Add form) they stage into ``_pending_logo_card``/
``_pending_logo_image`` instead -- the logo preview
(:meth:`TeamsView.show_logo`) shows the staged card or image, and
the next successful Add consumes it. Selecting a row discards any
staged logo (the form becomes that team's editor); a successful Add
or Remove resets both pending slots with the blank Add form.

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
    """The team editor's text fields, forwarded verbatim by the view.

    ``relay_plate`` is whatever ``relay_plate_input`` currently holds
    even on a rider_pooled ride, where the row is hidden -- the
    presenter ignores it there (a pooled team's plate is derived from
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


class TeamsPresenter:
    """Presenter for the teams editor (team_editor_dlg, Phase 4).

    Rows are the roster's TEAM entries, in creation order; the
    selection drives the record form and the read-only members list.
    Save/Remove follow the module docstring's lock shape: roster
    refusals surface through :meth:`TeamsView.show_validation` and
    leave the roster unchanged, never raising past a handler.
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
        self._pending_logo_card: str | None = None
        self._pending_logo_image: bytes | None = None
        self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from ``teams_list`` row *index*.

        Selecting a team leaves the Add form: any staged logo is
        discarded (module docstring) and the entry's own logo shows.
        """
        entry = self._visible_teams()[index]
        self._selected = entry
        self._pending_logo_card = None
        self._pending_logo_image = None
        self._show_entry(entry)

    def on_toggle_single_member(self, *, enabled: bool) -> None:
        """Handle the one-rider-teams filter, then re-render the list.

        *enabled* is the checkbox's new state: checked shows only TEAM
        entries with a single rider; unchecked shows every TEAM entry.
        """
        self._single_member_only = enabled
        self._refresh_rows()

    def on_add(self, form: TeamFormValues) -> None:
        """Handle add_btn: create an empty team from the form (W8).

        Reads ``name``/``notes`` off the form and applies the staged
        logo (module docstring): the created entry carries zero
        riders, and members arrive later through the Rider Editor. A
        blank or duplicate name (trimmed, case-insensitive) refuses
        via :meth:`TeamsView.show_validation`; a roster refusal
        (solo-only ride, the ride has left DRAFT, ...) shows the same
        way. Nothing is created on any refusal and the form keeps
        what the operator typed.
        """
        name = form.name.strip()
        if not name:
            self.view.show_validation("enter a team name")
            return
        if self._duplicate_team_name(name) is not None:
            self.view.show_validation(f'a team named "{name}" already exists')
            return
        try:
            entry = self.roster.create_empty_team(
                display_name=name,
                logo_card=self._pending_logo_card,
                logo_png=self._pending_logo_image,
            )
            if form.notes:
                self.roster.update_entry(entry, notes=form.notes)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
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
            and self._duplicate_team_name(name, exclude=entry) is not None
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
        self._refresh_rows()

    def on_pick_card(self) -> None:
        """Handle pick_card_btn: cycle a logo card, or stage one.

        With a team selected, each click advances to the next unused
        code in the roster's seeded sequence (skipping every other
        team's card), clearing any logo image -- a picked card wins.
        With nothing selected (the Add form) the card is staged for
        the next Add instead (module docstring). Either way, when
        every one of the 52 codes is already claimed (or the roster
        has no seed), says so instead of silently doing nothing.
        """
        if self._selected is None:
            self._stage_logo_card()
            return
        entry = self._selected
        code = self.roster.next_team_logo_card(after=entry.logo_card)
        if code is None:
            self.view.show_validation("every card logo is already in use by a team")
            return
        self.roster.set_team_logo_card(entry, code=code)
        self._refresh_rows()
        self._show_entry(entry)

    def on_pick_image(self, image: bytes) -> None:
        """Handle a picked logo image: set or stage it.

        With a team selected, the image is set on it -- an image wins
        over a card, and :meth:`Roster.set_team_logo_image` clears
        any ``logo_card``. With nothing selected (the Add form) the
        bytes are staged for the next Add instead (module docstring).
        """
        if self._selected is None:
            self._stage_logo_image(image)
            return
        entry = self._selected
        self.roster.set_team_logo_image(entry, image=image)
        self._refresh_rows()
        self._show_entry(entry)

    def _load(self) -> None:
        """Render the editor's full initial state from the roster."""
        self.view.set_relay_plate_visible(visible=self.roster.plate_model is PlateModel.TEAM_RELAY)
        self._refresh_rows()
        self._show_add_form()

    def _teams(self) -> tuple[Entry, ...]:
        """Return every TEAM entry, in ``teams_list``'s row order."""
        return tuple(entry for entry in self.roster.entries if entry.type is EntryType.TEAM)

    def _visible_teams(self) -> tuple[Entry, ...]:
        """Return the TEAM entries the list should currently show.

        The one-rider filter hides every team that has more than one
        rider; unchecked, the full TEAM tuple is returned unchanged.
        """
        teams = self._teams()
        if not self._single_member_only:
            return teams
        return tuple(entry for entry in teams if entry.team_size == 1)

    def _duplicate_team_name(self, name: str, *, exclude: Entry | None = None) -> Entry | None:
        """Return a TEAM entry whose name collides with *name*.

        A collision compares :func:`~rivercrossing.roster.
        rider_name_key` keys -- trimmed, whitespace-collapsed and
        case-folded -- so ``"Trail Blazers"`` and ``" trail
        blazers "`` name the same team. *exclude* (the entry being
        renamed) never collides with its own name.
        """
        key = rider_name_key(name)
        return next(
            (
                entry
                for entry in self._teams()
                if entry is not exclude and rider_name_key(entry.display_name) == key
            ),
            None,
        )

    def _stage_logo_card(self) -> None:
        """Stage the next unused seeded card for the Add form.

        Each click walks the deck past the previously staged card
        (module docstring). A staged card wins over a staged image,
        matching the roster's own either-or rule.
        """
        code = self.roster.next_team_logo_card(after=self._pending_logo_card)
        if code is None:
            self.view.show_validation("every card logo is already in use by a team")
            return
        self._pending_logo_image = None
        self._pending_logo_card = code
        self.view.show_logo(card=code, image=None)

    def _stage_logo_image(self, image: bytes) -> None:
        """Stage *image* for the Add form (module docstring)."""
        self._pending_logo_card = None
        self._pending_logo_image = image
        self.view.show_logo(card=None, image=image)

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
        """Reset the form: nothing selected, blank, no staged logo."""
        self._selected = None
        self._pending_logo_card = None
        self._pending_logo_image = None
        self.view.show_form(name="", relay_plate="", notes="")
        self.view.show_logo(card=None, image=None)
        self.view.show_members([])
