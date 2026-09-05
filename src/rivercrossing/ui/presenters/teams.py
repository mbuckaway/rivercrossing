# SPDX-License-Identifier: GPL-3.0-only
"""Teams presenter -- team_editor_dlg (Phase 4).

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view pairing ``RidersPresenter``/``rider_editor_dlg`` uses
(E3.2), since the editor reads and writes the roster itself rather
than a display-only projection of it. The editor owns a team's
*record* fields -- display name, relay plate, notes and the logo
card or image -- while membership is read-only here (the read-only
``members_list``) and stays with the Rider Editor.

**Team creation (Phase 4's one model-imposed shape).** The roster
cannot hold a member-less team: spec S2 defines an entry as a solo
rider or a team of riders, and even R-12's deferred floor only
tolerates the transient size-1 team (:meth:`Roster.
create_team_entry_of_one`) while DRAFT. The Teams Editor form
collects no rider, so :meth:`TeamsPresenter.on_add` anchors the new
team with exactly one rider whose name is the prompted team name
(plate-model shaped: the entry's next free plate on a team_relay
ride, the anchor rider's own next free plate on a rider_pooled one).
The anchor is a real, renameable rider -- the operator names it via
the Rider Editor, whose DRAFT edits (rename, replate, move) this
phase does not duplicate. Add/Remove are DRAFT-only
(:func:`~rivercrossing.roster.can_edit_structure`), like every other
structural roster edit (R-15); the roster's own refusals surface via
:meth:`TeamsView.show_validation`.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.roster import (
    EntryType,
    PlateModel,
    Rider,
    RosterError,
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
    """One ``teams_list`` row: the team's display name and logo state.

    ``logo_card`` is the team's card code (rendered by the view with
    its suit glyph), or ``None``; ``has_image`` says a logo image is
    set instead -- an image wins over a card, so a row shows the
    card code only when ``logo_card`` is set and no image is.
    """

    name: str
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

    def show_form(  # noqa: PLR0913 -- the passive view fills the five form slots verbatim
        self,
        *,
        name: str,
        relay_plate: str,
        notes: str,
        logo_card: str | None,
        has_image: bool,
    ) -> None:
        """Fill the team record form (R-20)."""
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

    def prompt_team_name(self) -> str | None:
        """Ask for a new team's name; None if the operator cancels."""
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
        self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from ``teams_list`` row *index*."""
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

    def on_add(self) -> None:
        """Handle add_btn: prompt a team name, then create the team.

        A refusal (solo-only ride, the ride has left DRAFT, ...)
        shows via :meth:`TeamsView.show_validation` and leaves the
        roster unchanged. A cancelled prompt is a no-op: nothing
        created.
        """
        name = self.view.prompt_team_name()
        if name is None:
            return
        try:
            self._create_team(name)
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

        A relay team's plate routes through
        :meth:`Roster.change_team_plate` (its own DRAFT lock); the
        name and notes through :meth:`Roster.update_entry`. A refusal
        (the ride has left DRAFT) shows via
        :meth:`TeamsView.show_validation`. A no-op if nothing is
        selected.
        """
        entry = self._selected
        if entry is None:
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
        """Handle pick_card_btn: cycle the selected team's logo card.

        Each click advances to the next unused code in the roster's
        seeded sequence (skipping every other team's card), clearing
        any logo image -- a picked card wins. A no-op if nothing is
        selected; when every one of the 52 codes is already claimed,
        says so instead of silently doing nothing.
        """
        entry = self._selected
        if entry is None:
            return
        code = self.roster.next_team_logo_card(after=entry.logo_card)
        if code is None:
            self.view.show_validation("every card logo is already in use by a team")
            return
        self.roster.set_team_logo_card(entry, code=code)
        self._refresh_rows()
        self._show_entry(entry)

    def on_pick_image(self, image: bytes) -> None:
        """Handle a picked logo image: set it on the selected team.

        An image wins over a card -- :meth:`Roster.set_team_logo_image`
        clears any ``logo_card``. A no-op if nothing is selected.
        """
        entry = self._selected
        if entry is None:
            return
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

    def _refresh_rows(self) -> None:
        """Re-render ``teams_list`` from the roster."""
        self.view.show_teams(
            [
                TeamRow(
                    name=entry.display_name,
                    logo_card=entry.logo_card,
                    has_image=entry.logo_png is not None,
                )
                for entry in self._visible_teams()
            ]
        )

    def _show_entry(self, entry: Entry) -> None:
        """Render *entry*'s record form and its read-only members.

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
            logo_card=entry.logo_card,
            has_image=entry.logo_png is not None,
        )
        self.view.show_members([rider.full_name for rider in entry.riders])

    def _show_add_form(self) -> None:
        """Reset the form: nothing selected, blank fields."""
        self._selected = None
        self.view.show_form(name="", relay_plate="", notes="", logo_card=None, has_image=False)
        self.view.show_members([])

    def _create_team(self, name: str) -> Entry:
        """Create *name*'s team entry (module docstring's anchor).

        ``create_team_entry_of_one`` shapes the entry to the ride's
        plate model: a team_relay team takes the next free plate for
        the entry (its anchor rider is plateless, S1); a rider_pooled
        team's anchor rider must carry a plate, so it takes the next
        free one and the entry adopts it. Either way the anchor rider
        is named from the team's own name -- a DRAFT-only placeholder
        the Rider Editor's own flows rename as real members arrive.
        """
        plate = self.roster.next_free_plate()
        if self.roster.plate_model is PlateModel.TEAM_RELAY:
            return self.roster.create_team_entry_of_one(
                display_name=name, rider=Rider(first_name=name), plate=plate
            )
        return self.roster.create_team_entry_of_one(
            display_name=name, rider=Rider(first_name=name, plate=plate)
        )
