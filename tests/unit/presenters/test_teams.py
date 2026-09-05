# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the teams presenter (Phase 4), tests-first.

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view shape ``RidersPresenter``/``rider_editor_dlg`` use. The
editor owns team *records* (display name, relay plate, notes, logo
card or image); membership is read-only here and stays with the Rider
Editor. ``RecordingTeamsView`` follows ``test_riders.py``'s
``RecordingRidersView`` pattern: a hand-written fake recording every
call -- no ``unittest.mock``, since this presenter touches no I/O
boundary (T-10).
"""

from rivercrossing.cards import seeded_card_codes
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.teams import TeamFormValues, TeamRow, TeamsPresenter

_SEED = 8843


class RecordingTeamsView:
    """A complete ``TeamsView`` spy recording each call, in order.

    ``new_team_name`` is the canned return for ``prompt_team_name`` --
    ``None`` reproduces the operator cancelling the native prompt.
    """

    def __init__(self) -> None:
        """Start with empty snapshots and a cancelled team prompt."""
        self.teams: list[TeamRow] = []
        self.form: dict[str, object] = {}
        self.relay_plate_visible: bool | None = None
        self.members: list[str] = []
        self.validation: list[str] = []
        self.new_team_name: str | None = None

    def show_teams(self, rows: list[TeamRow]) -> None:
        """Record the rendered teams_list rows."""
        self.teams = list(rows)

    def show_form(  # noqa: PLR0913 -- the test spy mirrors the view's five-field contract
        self,
        *,
        name: str,
        relay_plate: str,
        notes: str,
        logo_card: str | None,
        has_image: bool,
    ) -> None:
        """Record the filled form fields."""
        self.form = {
            "name": name,
            "relay_plate": relay_plate,
            "notes": notes,
            "logo_card": logo_card,
            "has_image": has_image,
        }

    def set_relay_plate_visible(self, *, visible: bool) -> None:
        """Record the Plate (relay) row's visibility."""
        self.relay_plate_visible = visible

    def show_members(self, names: list[str]) -> None:
        """Record the rendered members_list rows."""
        self.members = list(names)

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.validation.append(message)

    def prompt_team_name(self) -> str | None:
        """Return the canned ``new_team_name``."""
        return self.new_team_name


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


def _teams(roster: Roster) -> tuple[object, ...]:
    """Return every TEAM entry of *roster*, in list order."""
    return tuple(entry for entry in roster.entries if entry.type is EntryType.TEAM)


# ------------------------------------------------------ construction


def test_teams_presenter_loads_rows_from_a_pooled_mixed_roster() -> None:
    """Construction renders the TEAM entries, never the solo one."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    code0 = seeded_card_codes(_SEED)[0]

    TeamsPresenter(view, roster)

    assert view.teams == [TeamRow(name="Trail Blazers", logo_card=code0, has_image=False)]
    assert view.relay_plate_visible is False
    assert view.members == []


def test_teams_presenter_load_on_a_relay_ride_shows_the_relay_plate_row() -> None:
    """The relay plate row appears only on team_relay rides."""
    view = RecordingTeamsView()
    roster = _draft_relay_roster()

    TeamsPresenter(view, roster)

    assert view.relay_plate_visible is True


# ------------------------------------------------------------ selection


def test_teams_presenter_row_selection_fills_the_form_and_members() -> None:
    """Selecting a team shows its record and read-only members."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())
    code0 = seeded_card_codes(_SEED)[0]

    presenter.on_row_selected(0)

    assert view.form == {
        "name": "Trail Blazers",
        "relay_plate": "",
        "notes": "",
        "logo_card": code0,
        "has_image": False,
    }
    assert view.members == ["A. Roy", "K. Singh"]


def test_teams_presenter_row_selection_shows_the_relay_plate_of_a_relay_team() -> None:
    """A relay team's own plate fills the Plate (relay) input."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_relay_roster())

    presenter.on_row_selected(0)

    assert view.form["name"] == "Moss Ridge"
    assert view.form["relay_plate"] == "88"


# ---------------------------------------- single-member filter


def test_teams_presenter_renders_all_teams_by_default() -> None:
    """The filter starts off, so every TEAM row renders."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster_with_size_one_team()

    TeamsPresenter(view, roster)

    assert [row.name for row in view.teams] == ["Lone Wolf", "Trail Blazers"]


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

    assert [row.name for row in view.teams] == ["Lone Wolf", "Trail Blazers"]


def test_teams_presenter_row_selection_indexes_the_filtered_list() -> None:
    """Selection resolves against the filtered list, not the roster."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster_with_size_one_team())
    presenter.on_toggle_single_member(enabled=True)

    presenter.on_row_selected(0)

    assert view.form["name"] == "Lone Wolf"
    assert view.members == ["W. Reed"]


def test_teams_presenter_toggle_single_member_on_with_no_size_one_teams_renders_empty() -> None:
    """No one-rider teams means the filtered list is empty."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())

    presenter.on_toggle_single_member(enabled=True)

    assert view.teams == []


# ----------------------------------------------------------------- save


def test_teams_presenter_save_renames_the_selected_pooled_team() -> None:
    """Save applies the name through Roster.update_entry."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(TeamFormValues(name="Dirt Dynamos", relay_plate="", notes=""))

    team = _teams(roster)[0]
    assert team.display_name == "Dirt Dynamos"


def test_teams_presenter_save_ignores_the_relay_plate_on_a_pooled_ride() -> None:
    """A pooled team's plate is derived from its riders -- never set."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(TeamFormValues(name="Trail Blazers", relay_plate="99", notes=""))

    assert _teams(roster)[0].plate == "77"


def test_teams_presenter_save_changes_a_relay_teams_plate_and_name() -> None:
    """Save replates a relay team via Roster.change_team_plate."""
    view = RecordingTeamsView()
    roster = _draft_relay_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(TeamFormValues(name="Moss Ridge Riders", relay_plate="99", notes=""))

    team = _teams(roster)[0]
    assert (team.display_name, team.plate) == ("Moss Ridge Riders", "99")


def test_teams_presenter_save_persists_the_teams_notes() -> None:
    """Save writes the Notes field through Roster.update_entry."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(TeamFormValues(name="Trail Blazers", relay_plate="", notes="cap 88"))

    assert _teams(roster)[0].notes == "cap 88"


def test_teams_presenter_save_with_no_selection_is_a_no_op() -> None:
    """Nothing selected, nothing saved -- the roster stays untouched."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = roster.audit_log

    presenter.on_save(TeamFormValues(name="Ghost", relay_plate="", notes=""))

    assert roster.audit_log == before


# ------------------------------------------------------------------ add


def test_teams_presenter_add_prompts_for_a_name_and_creates_the_team() -> None:
    """Add team: the prompted name becomes a new roster team."""
    view = RecordingTeamsView()
    view.new_team_name = "Dirt Dynamos"
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_add()

    names = [entry.display_name for entry in roster.entries]
    assert "Dirt Dynamos" in names
    assert [row.name for row in view.teams] == ["Trail Blazers", "Dirt Dynamos"]


def test_teams_presenter_add_given_a_cancelled_prompt_creates_nothing() -> None:
    """A cancelled name prompt is a no-op (R-20's own shape)."""
    view = RecordingTeamsView()
    view.new_team_name = None
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add()

    assert len(roster.entries) == before


def test_teams_presenter_add_after_start_refuses_via_validation() -> None:
    """Add is DRAFT-only: a started ride refuses on the info bar."""
    view = RecordingTeamsView()
    view.new_team_name = "Dirt Dynamos"
    roster = _draft_pooled_roster()
    roster.status = RideStatus.RUNNING
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add()

    assert len(roster.entries) == before
    assert any("cannot be started" in message for message in view.validation)


def test_teams_presenter_add_on_a_solo_only_ride_refuses_via_validation() -> None:
    """A solo-only roster cannot hold teams (R-11), and says so."""
    view = RecordingTeamsView()
    view.new_team_name = "Dirt Dynamos"
    roster = Roster(entry_mode=EntryMode.SOLO)
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add()

    assert len(roster.entries) == before
    assert any("solo-only" in message for message in view.validation)


# --------------------------------------------------------------- remove


def test_teams_presenter_remove_deletes_the_selected_draft_team() -> None:
    """Remove deletes the selected DRAFT team, form resets."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_remove()

    assert _teams(roster) == ()
    assert view.teams == []
    assert view.form["name"] == ""


def test_teams_presenter_remove_after_start_refuses_via_validation() -> None:
    """Remove is DRAFT-only: a started ride refuses on the info bar."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    roster.status = RideStatus.RUNNING
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_row_selected(0)
    presenter.on_remove()

    assert len(roster.entries) == before
    assert any("can no longer be deleted" in message for message in view.validation)


def test_teams_presenter_remove_with_no_selection_is_a_no_op() -> None:
    """Nothing selected, nothing removed."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_remove()

    assert len(roster.entries) == before


# ----------------------------------------------------------------- logo


def test_teams_presenter_pick_card_advances_to_the_next_unused_card() -> None:
    """Each Pick card click walks the seeded sequence."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    codes = seeded_card_codes(_SEED)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_card()

    assert team.logo_card == codes[1]
    assert view.form["logo_card"] == codes[1]


def test_teams_presenter_pick_card_after_an_image_makes_the_card_win() -> None:
    """Picking a card clears a previously chosen logo image."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_image(b"team-logo-png")
    presenter.on_pick_card()

    assert team.logo_card is not None
    assert team.logo_png is None
    assert view.form["has_image"] is False


def test_teams_presenter_pick_image_sets_the_bytes_and_image_wins() -> None:
    """Choosing an image replaces any card -- image wins."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_image(b"team-logo-png")

    assert team.logo_png == b"team-logo-png"
    assert team.logo_card is None
    assert view.form["has_image"] is True
    assert view.form["logo_card"] is None


def test_teams_presenter_logo_pick_with_no_selection_is_a_no_op() -> None:
    """Logo buttons act on the selected team only."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = roster.audit_log

    presenter.on_pick_card()
    presenter.on_pick_image(b"team-logo-png")

    assert roster.audit_log == before


def test_teams_presenter_pick_card_on_an_unseeded_roster_says_so() -> None:
    """No seed means no card to pick -- a message, no crash."""
    view = RecordingTeamsView()
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_pick_card()

    assert any("already in use" in message for message in view.validation)


def test_teams_presenter_save_refuses_a_relay_plate_change_after_start() -> None:
    """A relay plate is DRAFT-locked: Save shows a refusal."""
    view = RecordingTeamsView()
    roster = _draft_relay_roster()
    roster.status = RideStatus.RUNNING
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(TeamFormValues(name="Moss Ridge", relay_plate="99", notes=""))

    assert any("plates cannot be changed" in message for message in view.validation)
    assert _teams(roster)[0].plate == "88"


def test_teams_presenter_add_on_a_relay_ride_gives_the_team_a_plate() -> None:
    """A relay team is created with its own next free entry plate."""
    view = RecordingTeamsView()
    view.new_team_name = "Dirt Dynamos"
    roster = _draft_relay_roster()
    presenter = TeamsPresenter(view, roster)
    expected_plate = roster.next_free_plate()  # "124": one past 123/88

    presenter.on_add()

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.plate == expected_plate
    assert created.logo_card is not None
