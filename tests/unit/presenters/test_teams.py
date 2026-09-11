# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the teams presenters (Phase 3 editors rework).

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view shape ``RidersPresenter``/``rider_editor_dlg`` use. The
editor is a read-only *record display* (name, relay plate, notes and
the read-only member list); membership and every record edit happen
elsewhere -- membership in the Rider Editor, a team's record in the
Add/Edit Team dialog whose own :class:`AddTeamPresenter` owns both
creation and write-back.

Phase 3's rework retires the editor's logo surface entirely (the
``Logo`` column, the bitmap preview and the Pick card / Image / Remove
logo buttons), drops the in-form Save (the form is read-only now) and
switches both the list and the selection to a key-based shape:
``teams_list`` sorts through :meth:`TeamsListModel.Compare` (native
header arrows) and the view forwards the selected row's *display
name* rather than a positional index, so a sorted row can never select
the wrong team.

``RecordingTeamsView`` follows ``test_riders.py``'s
``RecordingRidersView`` pattern: a hand-written fake recording every
call -- no ``unittest.mock``, since this presenter touches no I/O
boundary (T-10). Importing ``ui.views.team_editor`` does pull ``wx``,
which is inert without a display -- the ``test_list_columns.py``
precedent imports ``ui.views.results_win`` headless the same way;
``wx.dataview.DataViewItem`` (the item ``Compare`` receives) is
constructible with no ``wx.App``.
"""

import pytest
import wx.dataview
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import seeded_card_codes
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.teams import (
    AddTeamPresenter,
    TeamFormValues,
    TeamRow,
    TeamsPresenter,
)
from rivercrossing.ui.views.team_editor import (
    COL_NAME,
    COL_RIDERS,
    TeamsListModel,
    logo_fit_size,
)

_SEED = 8843


class RecordingTeamsView:
    """A complete ``TeamsView`` spy recording each call, in order.

    ``form`` snapshots the last ``show_form`` call's three text
    fields; ``confirm_result`` is the verdict :meth:`confirm` returns,
    so a test drives both the confirmed and the declined remove path
    without a real dialog.
    """

    def __init__(self) -> None:
        """Start with empty snapshots and a confirming view."""
        self.teams: list[TeamRow] = []
        self.form: dict[str, object] = {}
        self.relay_plate_visible: bool | None = None
        self.members: list[str] = []
        self.validation: list[str] = []
        self.edit_enabled: bool | None = None
        self.confirm_result = True
        self.confirmations: list[tuple[str, str, str, str]] = []

    def show_teams(self, rows: list[TeamRow]) -> None:
        """Record the rendered teams_list rows."""
        self.teams = list(rows)

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Record the filled form text fields."""
        self.form = {"name": name, "relay_plate": relay_plate, "notes": notes}

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Record the Plate (relay) row's visibility."""
        self.relay_plate_visible = visible

    def show_members(self, names: list[str]) -> None:
        """Record the rendered members_list rows."""
        self.members = list(names)

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.validation.append(message)

    def set_edit_enabled(self, *, enabled: bool) -> None:
        """Record edit_btn's selection-driven enabled state."""
        self.edit_enabled = enabled

    def confirm(  # noqa: PLR0913 -- mirrors the view protocol's four fields
        self, title: str, message: str, *, ok_label: str, cancel_label: str
    ) -> bool:
        """Record the confirm and return the canned verdict."""
        self.confirmations.append((title, message, ok_label, cancel_label))
        return self.confirm_result


class RecordingAddTeamView:
    """A complete ``AddTeamView`` spy recording each call, in order."""

    def __init__(self) -> None:
        """Start with an empty call log and a blank logo snapshot."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.logo: dict[str, object] = {"card": None}

    def set_mode(self, *, editing: bool) -> None:
        """Record the dialog's Add/Edit title-and-button mode."""
        self.calls.append(("set_mode", (editing,)))

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Record the Plate (relay) row's visibility."""
        self.calls.append(("set_relay_plate_visible", (visible,)))

    def show_form(self, *, name: str, relay_plate: str, notes: str) -> None:
        """Record the filled form text fields."""
        self.calls.append(("show_form", (name, relay_plate, notes)))

    def show_logo(self, *, card: str | None) -> None:
        """Record the card preview the dialog should render."""
        self.logo = {"card": card}
        self.calls.append(("show_logo", (card,)))

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.calls.append(("show_validation", (message,)))


# ------------------------------------------------------------- fixtures


def _draft_pooled_roster() -> Roster:
    """Return a seeded MIXED pooled DRAFT roster with one team."""
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        team_logo_seed=_SEED,
    )
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _draft_relay_roster() -> Roster:
    """Return a seeded MIXED team_relay DRAFT roster with one team."""
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.TEAM_RELAY,
        team_logo_seed=_SEED,
    )
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[
            Rider(first_name="R.", last_name="Dubois"),
            Rider(first_name="M.", last_name="Chen"),
        ],
        plate="88",
    )
    return roster


def _draft_pooled_roster_with_size_one_team() -> Roster:
    """Return a MIXED pooled DRAFT roster with 1- and 2-rider teams."""
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        team_logo_seed=_SEED,
    )
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="W.", last_name="Reed", plate="77"),
    )
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="78"),
            Rider(first_name="K.", last_name="Singh", plate="79"),
        ],
    )
    return roster


def _unseeded_mixed_roster() -> Roster:
    """Return a MIXED pooled roster with a team but no logo seed."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _exhausted_deck_roster() -> Roster:
    """Return a seeded roster whose 52 logo codes are all claimed."""
    return _roster_with_teams_claiming(52)


def _one_code_left_roster() -> tuple[Roster, str]:
    """Return a roster claiming all but one card, and that free code."""
    roster = _roster_with_teams_claiming(51)
    claimed = {code for entry in roster.entries for code in (entry.logo_card,)}
    free = next(code for code in seeded_card_codes(_SEED) if code not in claimed)
    return roster, free


def _roster_with_teams_claiming(count: int) -> Roster:
    """Return a seeded roster of *count* auto-logged two-rider teams."""
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        team_logo_seed=_SEED,
    )
    for index in range(count):
        roster.create_team_entry(
            display_name=f"Team {index}",
            riders=[
                Rider(first_name="A", last_name="B", plate=str(index * 2 + 1)),
                Rider(first_name="C", last_name="D", plate=str(index * 2 + 2)),
            ],
        )
    return roster


def _teams(roster: Roster) -> tuple[object, ...]:
    """Return every TEAM entry of *roster*, in list order."""
    return tuple(entry for entry in roster.entries if entry.type is EntryType.TEAM)


def _new_form(name: str, notes: str = "") -> TeamFormValues:
    """Return an Add/Save form for a pooled ride (no relay row)."""
    return TeamFormValues(name=name, relay_plate="", notes=notes)


def _item(row: int) -> wx.dataview.DataViewItem:
    """Return the item the control hands ``Compare`` for model *row*.

    ``DataViewItem(0)`` is wx's null item, so a model row's id is
    ``row + 1`` -- exactly the mapping
    ``DataViewIndexListModel.GetRow`` reverses.
    """
    return wx.dataview.DataViewItem(row + 1)


# ------------------------------------------------------ construction


def test_teams_presenter_loads_rows_with_rider_counts_from_a_pooled_mixed_roster() -> None:
    """Construction renders the TEAM entries with their rider counts."""
    view = RecordingTeamsView()

    TeamsPresenter(view, _draft_pooled_roster())

    assert view.teams == [TeamRow(name="Trail Blazers", rider_count=2)]
    assert view.relay_plate_visible is False
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.members == []
    assert view.edit_enabled is False


def test_teams_presenter_load_on_a_relay_ride_shows_the_relay_plate_row() -> None:
    """The relay plate row appears only on team_relay rides."""
    view = RecordingTeamsView()

    TeamsPresenter(view, _draft_relay_roster())

    assert view.relay_plate_visible is True


# ------------------------------------------------------------ selection


def test_teams_presenter_row_selected_by_display_name_fills_form_and_members() -> None:
    """Selecting a team by name shows its record and members."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    presenter.on_row_selected("Trail Blazers")

    assert view.form == {"name": "Trail Blazers", "relay_plate": "", "notes": ""}
    assert view.members == ["A. Roy", "K. Singh"]
    assert view.edit_enabled is True


def test_teams_presenter_row_selected_by_an_unknown_name_is_a_no_op() -> None:
    """A stale row name selects nothing rather than the wrong team."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    presenter.on_row_selected("Ghost Team")

    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.members == []
    assert view.edit_enabled is False


def test_teams_presenter_selected_is_none_until_a_row_is_chosen() -> None:
    """The view reads ``selected`` to decide which team to edit."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    assert presenter.selected is None


def test_teams_presenter_selected_is_the_entry_the_row_named() -> None:
    """After a selection, ``selected`` is that exact roster entry."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected("Trail Blazers")

    assert presenter.selected is _teams(roster)[0]


def test_teams_presenter_row_selected_shows_the_relay_plate_of_a_relay_team() -> None:
    """A relay team's own plate fills the Plate (relay) input."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_relay_roster())

    presenter.on_row_selected("Moss Ridge")

    assert view.form == {"name": "Moss Ridge", "relay_plate": "88", "notes": ""}


# ---------------------------------------- single-member filter


def test_teams_presenter_renders_all_teams_by_default() -> None:
    """The filter starts off, so every TEAM row renders."""
    view = RecordingTeamsView()

    TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())

    assert [(row.name, row.rider_count) for row in view.teams] == [
        ("Lone Wolf", 1),
        ("Trail Blazers", 2),
    ]


def test_teams_presenter_toggle_single_member_on_renders_only_size_one_teams() -> None:
    """With the filter on, only the size-1 team survives the list."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())

    presenter.on_toggle_single_member(enabled=True)

    assert [row.name for row in view.teams] == ["Lone Wolf"]


def test_teams_presenter_toggle_single_member_off_renders_all_teams_again() -> None:
    """Turning the filter back off restores every TEAM row."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())

    presenter.on_toggle_single_member(enabled=True)
    presenter.on_toggle_single_member(enabled=False)

    assert [(row.name, row.rider_count) for row in view.teams] == [
        ("Lone Wolf", 1),
        ("Trail Blazers", 2),
    ]


def test_teams_presenter_row_selected_resolves_against_the_filtered_list() -> None:
    """A filtered-out name selects nothing while the filter is on."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())
    presenter.on_toggle_single_member(enabled=True)

    presenter.on_row_selected("Trail Blazers")

    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.members == []


def test_teams_presenter_row_selected_still_finds_a_visible_team_when_filtered() -> None:
    """The surviving one-rider team is selectable while filtered."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())
    presenter.on_toggle_single_member(enabled=True)

    presenter.on_row_selected("Lone Wolf")

    assert view.form["name"] == "Lone Wolf"
    assert view.members == ["W. Reed"]


def test_teams_presenter_toggle_single_member_on_with_no_size_one_teams_renders_empty() -> None:
    """No one-rider teams means the filtered list is empty."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    presenter.on_toggle_single_member(enabled=True)

    assert view.teams == []


# ------------------------------------------------------------------ add


def test_teams_presenter_on_add_committed_renders_a_team_the_dialog_created() -> None:
    """A dialog-created team renders once the editor catches up."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    roster.create_empty_team(display_name="Dirt Dynamos")

    presenter.on_add_committed()

    assert [row.name for row in view.teams] == ["Trail Blazers", "Dirt Dynamos"]
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.members == []
    assert view.edit_enabled is False
    assert presenter.roster_changed is True


def test_teams_presenter_on_add_committed_with_no_change_is_still_a_noop_refresh() -> None:
    """Catching up when nothing changed renders the same state."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    presenter.on_add_committed()

    assert [row.name for row in view.teams] == ["Trail Blazers"]
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}


# ---------------------------------------------------------------- edit


def test_teams_presenter_on_edit_committed_renders_the_renamed_team() -> None:
    """A committed edit re-renders the list with the new name."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    presenter.on_row_selected("Trail Blazers")
    roster.update_entry(_teams(roster)[0], display_name="Dirt Dynamos")

    presenter.on_edit_committed()

    assert [row.name for row in view.teams] == ["Dirt Dynamos"]
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.edit_enabled is False
    assert presenter.roster_changed is True


# --------------------------------------------------------------- remove


def test_teams_presenter_remove_confirms_then_deletes_the_selected_draft_team() -> None:
    """A confirmed Remove deletes the team and resets the editor."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    presenter.on_row_selected("Trail Blazers")

    presenter.on_remove()

    assert _teams(roster) == ()
    assert view.teams == []
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.edit_enabled is False
    assert presenter.roster_changed is True
    assert view.confirmations == [
        ("Remove team", 'Remove the team "Trail Blazers"?', "Remove", "Cancel")
    ]


def test_teams_presenter_remove_declined_at_the_confirm_leaves_the_team() -> None:
    """Declining the confirm deletes nothing and saves nothing."""
    view = RecordingTeamsView()
    view.confirm_result = False
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    presenter.on_row_selected("Trail Blazers")

    presenter.on_remove()

    assert [entry.display_name for entry in _teams(roster)] == ["Trail Blazers"]
    assert presenter.roster_changed is False


def test_teams_presenter_remove_after_start_refuses_via_validation() -> None:
    """Remove is DRAFT-only: a started ride refuses on the info bar."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    roster.status = RideStatus.RUNNING
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected("Trail Blazers")
    presenter.on_remove()

    assert len(roster.entries) == 2
    assert any("can no longer be deleted" in message for message in view.validation)
    assert presenter.roster_changed is False


def test_teams_presenter_remove_with_no_selection_never_confirms() -> None:
    """Nothing selected, nothing asked, nothing removed."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_remove()

    assert len(roster.entries) == before
    assert presenter.roster_changed is False
    assert view.confirmations == []


# -------------------------------------------------- AddTeamPresenter
# The dialog owns both halves of a team's record: the Add path creates
# a zero-rider TEAM entry, the Edit path writes the form back onto the
# entry it was opened for.


def test_add_team_presenter_init_on_a_pooled_ride_hides_the_relay_row_and_blank_form() -> None:
    """Construction: no relay row on pooled, blank form, Add mode."""
    view = RecordingAddTeamView()

    AddTeamPresenter(view, _draft_pooled_roster())

    assert view.calls == [
        ("set_mode", (False,)),
        ("set_relay_plate_visible", (False,)),
        ("show_form", ("", "", "")),
        ("show_logo", (None,)),
    ]
    assert view.logo == {"card": None}


def test_add_team_presenter_init_on_a_relay_ride_prefills_the_next_free_plate() -> None:
    """The dialog's relay row shows on relay rides, prefilled."""
    view = RecordingAddTeamView()

    AddTeamPresenter(view, _draft_relay_roster())

    assert ("set_relay_plate_visible", (True,)) in view.calls
    assert ("show_form", ("", "124", "")) in view.calls


def test_add_team_presenter_editing_preloads_the_entry_and_switches_to_edit_mode() -> None:
    """Edit mode shows the entry's own record and logo card."""
    view = RecordingAddTeamView()
    roster = _draft_relay_roster()
    entry = _teams(roster)[0]

    AddTeamPresenter(view, roster, editing=entry)

    assert view.calls == [
        ("set_mode", (True,)),
        ("set_relay_plate_visible", (True,)),
        ("show_form", ("Moss Ridge", "88", "")),
        ("show_logo", (entry.logo_card,)),
    ]


def test_add_team_presenter_editing_a_pooled_team_hides_the_relay_row() -> None:
    """A pooled team's plate is derived, never offered as text."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    entry = _teams(roster)[0]

    AddTeamPresenter(view, roster, editing=entry)

    assert ("set_relay_plate_visible", (False,)) in view.calls
    assert ("show_form", ("Trail Blazers", "", "")) in view.calls


def test_add_team_presenter_submit_creates_a_zero_rider_team_and_returns_true() -> None:
    """Add commits an empty team entry (riders join later)."""
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster)

    committed = presenter.on_submit(_new_form("Dirt Dynamos"))

    assert committed is True
    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.type is EntryType.TEAM
    assert created.riders == []
    assert created.logo_card == seeded_card_codes(_SEED)[1]


def test_add_team_presenter_submit_applies_the_form_notes() -> None:
    """Notes typed into the dialog land on the created team."""
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster)

    presenter.on_submit(_new_form("Dirt Dynamos", notes="cap 88"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.notes == "cap 88"


def test_add_team_presenter_submit_on_a_relay_ride_uses_the_prefilled_relay_plate() -> None:
    """A relay Add commits the prefilled plate as the entry's own."""
    roster = _draft_relay_roster()
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster)

    committed = presenter.on_submit(
        TeamFormValues(name="Dirt Dynamos", relay_plate="124", notes="")
    )

    assert committed is True
    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert (created.plate, created.riders) == ("124", [])


def test_add_team_presenter_submit_given_a_blank_name_refuses_and_returns_false() -> None:
    """A whitespace-only name creates nothing and says so."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(view, roster)
    before = len(roster.entries)

    committed = presenter.on_submit(_new_form("   "))

    assert committed is False
    assert len(roster.entries) == before
    assert ("show_validation", ("enter a team name",)) in view.calls


def test_add_team_presenter_submit_given_a_duplicate_name_refuses_and_returns_false() -> None:
    """A name matching an existing team (folded, trimmed) is refused."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(view, roster)
    before = len(roster.entries)

    committed = presenter.on_submit(_new_form("  trail blazers  "))

    assert committed is False
    assert len(roster.entries) == before
    assert ("show_validation", ('a team named "trail blazers" already exists',)) in view.calls


def test_add_team_presenter_submit_given_a_blank_relay_plate_refuses_and_returns_false() -> None:
    """The no-blank-relay-plate rule holds in the Add dialog too."""
    view = RecordingAddTeamView()
    roster = _draft_relay_roster()
    presenter = AddTeamPresenter(view, roster)
    before = len(roster.entries)

    committed = presenter.on_submit(
        TeamFormValues(name="Dirt Dynamos", relay_plate="  ", notes="")
    )

    assert committed is False
    assert len(roster.entries) == before
    assert ("show_validation", ("plate '  ' must not be empty",)) in view.calls


def test_add_team_presenter_submit_on_a_solo_only_ride_refuses_and_returns_false() -> None:
    """A solo-only roster cannot hold teams (R-11), and says so."""
    view = RecordingAddTeamView()
    roster = Roster(entry_mode=EntryMode.SOLO)
    presenter = AddTeamPresenter(view, roster)
    before = len(roster.entries)

    committed = presenter.on_submit(_new_form("Dirt Dynamos"))

    assert committed is False
    assert len(roster.entries) == before
    assert (
        "show_validation",
        ("this ride is solo-only; team entries are not allowed",),
    ) in view.calls


def test_add_team_presenter_submit_after_start_refuses_and_returns_false() -> None:
    """Add is DRAFT-only: a started ride refuses in the dialog."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    roster.status = RideStatus.RUNNING
    presenter = AddTeamPresenter(view, roster)
    before = len(roster.entries)

    committed = presenter.on_submit(_new_form("Dirt Dynamos"))

    assert committed is False
    assert len(roster.entries) == before
    assert (
        "show_validation",
        ("a new team cannot be started once the ride is running",),
    ) in view.calls


# ------------------------------------------------- edit write-back


def test_add_team_presenter_editing_writes_back_name_notes_and_relay_plate() -> None:
    """Save applies the form to the edited team through the roster."""
    roster = _draft_relay_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster, editing=entry)

    committed = presenter.on_submit(
        TeamFormValues(name="Moss Ridge Riders", relay_plate="99", notes="cap 88")
    )

    assert committed is True
    assert (entry.display_name, entry.plate, entry.notes) == ("Moss Ridge Riders", "99", "cap 88")


def test_add_team_presenter_editing_given_a_blank_name_refuses_and_returns_false() -> None:
    """A whitespace-only name leaves the edited team untouched."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(view, roster, editing=entry)

    committed = presenter.on_submit(_new_form("   "))

    assert committed is False
    assert entry.display_name == "Trail Blazers"
    assert ("show_validation", ("enter a team name",)) in view.calls


def test_add_team_presenter_editing_given_another_teams_name_refuses() -> None:
    """A rename onto a different team's name is refused."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster_with_size_one_team()
    entry = _teams(roster)[1]
    presenter = AddTeamPresenter(view, roster, editing=entry)

    committed = presenter.on_submit(_new_form("lone wolf"))

    assert committed is False
    assert entry.display_name == "Trail Blazers"
    assert ("show_validation", ('a team named "lone wolf" already exists',)) in view.calls


def test_add_team_presenter_editing_allows_its_own_name_in_another_case() -> None:
    """A team may rename to its own name in any case (no self-dup)."""
    roster = _draft_pooled_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster, editing=entry)

    committed = presenter.on_submit(_new_form("TRAIL BLAZERS"))

    assert committed is True
    assert entry.display_name == "TRAIL BLAZERS"


def test_add_team_presenter_editing_a_blank_relay_plate_refuses() -> None:
    """A relay team's plate can never be blanked by an edit."""
    view = RecordingAddTeamView()
    roster = _draft_relay_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(view, roster, editing=entry)

    committed = presenter.on_submit(TeamFormValues(name="Moss Ridge", relay_plate="   ", notes=""))

    assert committed is False
    assert entry.plate == "88"
    assert ("show_validation", ("plate '   ' must not be empty",)) in view.calls


def test_add_team_presenter_editing_with_an_unchanged_form_writes_nothing() -> None:
    """Submitting an untouched form commits without an audit write."""
    roster = _draft_pooled_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(RecordingAddTeamView(), roster, editing=entry)
    before = roster.audit_log

    committed = presenter.on_submit(_new_form("Trail Blazers"))

    assert committed is True
    assert roster.audit_log == before


def test_add_team_presenter_editing_applies_a_picked_card_to_the_entry() -> None:
    """A card picked in edit mode is written back on Save."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    entry = _teams(roster)[0]
    presenter = AddTeamPresenter(view, roster, editing=entry)

    presenter.on_pick_card()
    presenter.on_submit(_new_form("Trail Blazers"))

    picked = view.logo["card"]
    assert picked != seeded_card_codes(_SEED)[0]
    assert entry.logo_card == picked


# ------------------------------------------------------- pick card
# A pick stages a *random* unused code, excluding the one already
# staged, so every click visibly changes the preview.


def test_add_team_presenter_pick_card_stages_an_unused_seeded_card() -> None:
    """Pick card in the dialog only stages; the roster stays put."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(view, roster)
    before = roster.audit_log

    presenter.on_pick_card()

    assert view.logo["card"] in seeded_card_codes(_SEED)
    assert view.logo["card"] != seeded_card_codes(_SEED)[0]  # claimed by a team
    assert roster.audit_log == before


def test_add_team_presenter_pick_card_clicks_never_repeat_the_staged_card() -> None:
    """Each click excludes the staged code, so the preview changes."""
    view = RecordingAddTeamView()
    roster = _draft_pooled_roster()
    presenter = AddTeamPresenter(view, roster)

    presenter.on_pick_card()
    first = view.logo["card"]
    presenter.on_pick_card()

    assert view.logo["card"] != first


def test_add_team_presenter_pick_card_on_an_unseeded_roster_says_no_deck_is_available() -> None:
    """No seed means no card deck exists to stage from."""
    view = RecordingAddTeamView()
    roster = _unseeded_mixed_roster()
    presenter = AddTeamPresenter(view, roster)

    presenter.on_pick_card()

    assert view.logo == {"card": None}
    assert ("show_validation", ("no card deck is available for this ride",)) in view.calls


def test_add_team_presenter_pick_card_when_the_deck_is_exhausted_says_every_card_is_in_use() -> (
    None
):
    """All 52 codes claimed: the in-use message, not the no-deck one."""
    view = RecordingAddTeamView()
    roster = _exhausted_deck_roster()
    presenter = AddTeamPresenter(view, roster)

    presenter.on_pick_card()

    assert view.logo == {"card": None}
    assert ("show_validation", ("every card logo is already in use by a team",)) in view.calls


# ---------------------------------------------------- random_team_card


def test_random_team_card_returns_an_unclaimed_seeded_code() -> None:
    """A random pick never repeats a code a team already shows."""
    roster = _draft_pooled_roster()

    code = roster.random_team_card()

    assert code in seeded_card_codes(_SEED)
    assert code != seeded_card_codes(_SEED)[0]


def test_random_team_card_never_returns_the_excluded_code() -> None:
    """The staged code is excluded, so a repeat click always changes."""
    roster = _draft_pooled_roster()
    staged = seeded_card_codes(_SEED)[5]

    code = roster.random_team_card(exclude=staged)

    assert code is not None
    assert code != staged


def test_random_team_card_given_no_seed_returns_none() -> None:
    """A roster with no team_logo_seed has no deck to draw from."""
    roster = _unseeded_mixed_roster()

    assert roster.random_team_card() is None


def test_random_team_card_with_every_code_claimed_returns_none() -> None:
    """An exhausted deck leaves nothing to draw."""
    roster = _exhausted_deck_roster()

    assert roster.random_team_card() is None


def test_random_team_card_given_the_last_free_code_excluded_returns_none() -> None:
    """Excluding the only free code empties the draw."""
    roster, free = _one_code_left_roster()

    assert roster.random_team_card(exclude=free) is None


@given(exclude=st.one_of(st.none(), st.sampled_from(seeded_card_codes(_SEED))))
def test_random_team_card_given_any_exclusion_never_returns_a_claimed_or_excluded_code(
    exclude: str | None,
) -> None:
    """Invariant: the draw is in-deck, unclaimed and not excluded."""
    roster = _draft_pooled_roster()

    code = roster.random_team_card(exclude=exclude)

    assert code is not None
    assert code in seeded_card_codes(_SEED)
    assert code != seeded_card_codes(_SEED)[0]  # claimed by Trail Blazers
    assert code != exclude


# ---------------------------------------------------- TeamsListModel


def test_teams_list_model_compare_sorts_team_names_case_folded() -> None:
    """Header sorting on Team compares case-folded names."""
    model = TeamsListModel(
        [
            TeamRow(name="Alpha", rider_count=1),
            TeamRow(name="beta", rider_count=1),
        ]
    )

    assert model.Compare(_item(1), _item(0), COL_NAME, True) > 0  # noqa: FBT003


def test_teams_list_model_given_no_rows_is_empty_with_two_columns() -> None:
    """An empty rebuild is a valid state (T-4's empty collection)."""
    model = TeamsListModel([])

    assert (model.GetCount(), model.GetColumnCount()) == (0, 2)


@given(name=st.text(), count=st.integers(0, 999))
def test_teams_list_model_get_value_by_row_preserves_the_row(name: str, count: int) -> None:
    """Every row renders its own name and count, in its own column."""
    model = TeamsListModel([TeamRow(name=name, rider_count=count)])

    assert model.GetValueByRow(0, COL_NAME) == name
    assert model.GetValueByRow(0, COL_RIDERS) == str(count)


def test_teams_list_model_compare_descending_reverses_the_name_order() -> None:
    """The arrow's descending order inverts the Team comparison."""
    model = TeamsListModel(
        [
            TeamRow(name="Alpha", rider_count=1),
            TeamRow(name="beta", rider_count=1),
        ]
    )

    assert model.Compare(_item(1), _item(0), COL_NAME, False) < 0  # noqa: FBT003


def test_teams_list_model_compare_given_equal_names_returns_zero() -> None:
    """Same team names compare equal."""
    model = TeamsListModel(
        [
            TeamRow(name="Trail Blazers", rider_count=1),
            TeamRow(name="trail blazers", rider_count=2),
        ]
    )

    assert model.Compare(_item(0), _item(1), COL_NAME, True) == 0  # noqa: FBT003


def test_teams_list_model_compare_sorts_rider_counts_numerically() -> None:
    """Riders sorts as a number: 2 before 10, not "10" before "2"."""
    model = TeamsListModel(
        [
            TeamRow(name="Ten", rider_count=10),
            TeamRow(name="Two", rider_count=2),
        ]
    )

    assert model.Compare(_item(1), _item(0), COL_RIDERS, True) < 0  # noqa: FBT003


def test_teams_list_model_compare_descending_reverses_the_rider_order() -> None:
    """Descending Riders puts the biggest team first."""
    model = TeamsListModel(
        [
            TeamRow(name="Ten", rider_count=10),
            TeamRow(name="Two", rider_count=2),
        ]
    )

    assert model.Compare(_item(0), _item(1), COL_RIDERS, False) < 0  # noqa: FBT003


def test_teams_list_model_compare_given_equal_rider_counts_ties_on_the_name() -> None:
    """Equal rider counts fall back to the case-folded name."""
    model = TeamsListModel(
        [
            TeamRow(name="Zebra", rider_count=2),
            TeamRow(name="Aardvark", rider_count=2),
        ]
    )

    assert model.Compare(_item(1), _item(0), COL_RIDERS, True) < 0  # noqa: FBT003


@given(
    name_a=st.text(),
    name_b=st.text(),
    count_a=st.integers(0, 12),
    count_b=st.integers(0, 12),
)
def test_teams_list_model_compare_is_antisymmetric(  # noqa: PLR0913, PLR0917 -- the row's four fields
    name_a: str,
    name_b: str,
    count_a: int,
    count_b: int,
) -> None:
    """Swapping the two items negates the comparison exactly."""
    model = TeamsListModel(
        [
            TeamRow(name=name_a, rider_count=count_a),
            TeamRow(name=name_b, rider_count=count_b),
        ]
    )

    forward = model.Compare(_item(0), _item(1), COL_NAME, True)  # noqa: FBT003
    backward = model.Compare(_item(1), _item(0), COL_NAME, True)  # noqa: FBT003

    assert forward == -backward


# --------------------------------------------------- logo sizing


@pytest.mark.parametrize(
    ("width", "height", "within", "upscale", "expected"),
    [
        pytest.param(400, 300, (128, 128), False, (128, 96), id="wide_shrinks_to_box"),
        pytest.param(100, 300, (128, 128), False, (43, 128), id="tall_shrinks_to_box"),
        pytest.param(64, 64, (128, 128), False, (64, 64), id="small_stays_natural"),
        pytest.param(96, 128, (128, 128), False, (96, 128), id="box_sized_stays"),
        pytest.param(24, 32, (96, 128), True, (96, 128), id="card_1x_upscales_to_card_box"),
        pytest.param(48, 64, (96, 128), True, (96, 128), id="card_2x_upscales_to_card_box"),
    ],
)
def test_logo_fit_size_scales_into_the_bounded_box_preserving_aspect(  # noqa: PLR0913, PLR0917 -- the parametrize row's five fields
    width: int,
    height: int,
    within: tuple[int, int],
    upscale: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected: tuple[int, int],
) -> None:
    """The pure fit rule: shrink (or grow) only as far as *within*."""
    assert logo_fit_size(width, height, within=within, upscale=upscale) == expected


@given(width=st.integers(1, 4000), height=st.integers(1, 4000))
def test_logo_fit_size_never_exceeds_the_box_and_keeps_aspect(
    width: int,
    height: int,
) -> None:
    """A fitted logo always fits *within* and never distorts."""
    fitted_w, fitted_h = logo_fit_size(width, height, within=(128, 128))

    assert (fitted_w, fitted_h) <= (128, 128)
    # Aspect preserved up to one rounding pixel per side.
    assert abs(fitted_w * height - fitted_h * width) <= max(width, height)
