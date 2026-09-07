# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the teams presenter (Phase 4 rework), tests-first.

``TeamsPresenter`` drives ``team_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` -- the same presenter-inside-
the-view shape ``RidersPresenter``/``rider_editor_dlg`` use. The
editor owns team *records* (display name, relay plate, notes, logo
card or image); membership is read-only here and stays with the Rider
Editor. The rework adds the ``Riders`` column (``TeamRow.rider_count``
from ``entry.team_size``), a real bitmap logo preview
(``TeamsView.show_logo``), and a form-driven Add: ``on_add`` reads
the form's name/notes plus a pending staged logo (never the native
name prompt) and validates name non-blank and not a duplicate
(trimmed, case-insensitive), on rename-on-save too.

``RecordingTeamsView`` follows ``test_riders.py``'s
``RecordingRidersView`` pattern: a hand-written fake recording every
call -- no ``unittest.mock``, since this presenter touches no I/O
boundary (T-10). The view module import at the bottom of the header
only pulls ``format_logo`` (the pure Logo-cell renderer); importing
``ui.views.team_editor`` does pull ``wx``, which is inert without a
display -- the ``test_list_columns.py`` precedent imports
``ui.views.results_win`` headless the same way.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import seeded_card_codes
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.teams import TeamFormValues, TeamRow, TeamsPresenter
from rivercrossing.ui.views.team_editor import CARD_TEXT, IMAGE_TEXT, format_logo

_SEED = 8843


class RecordingTeamsView:
    """A complete ``TeamsView`` spy recording each call, in order.

    ``logo`` snapshots the last ``show_logo`` call as a
    ``{"card": ..., "image": ...}`` dict; ``form`` the last
    ``show_form`` call's three text fields. Nothing else is canned:
    the presenter drives every state change through these calls.
    """

    def __init__(self) -> None:
        """Start with empty snapshots and a blank logo state."""
        self.teams: list[TeamRow] = []
        self.form: dict[str, object] = {}
        self.relay_plate_visible: bool | None = None
        self.members: list[str] = []
        self.validation: list[str] = []
        self.logo: dict[str, object] = {"card": None, "image": None}

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

    def show_logo(self, *, card: str | None, image: bytes | None) -> None:
        """Record the logo preview the view should render."""
        self.logo = {"card": card, "image": image}

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.validation.append(message)


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


def _teams(roster: Roster) -> tuple[object, ...]:
    """Return every TEAM entry of *roster*, in list order."""
    return tuple(entry for entry in roster.entries if entry.type is EntryType.TEAM)


def _new_form(name: str, notes: str = "") -> TeamFormValues:
    """Return an Add/Save form for a pooled ride (no relay row)."""
    return TeamFormValues(name=name, relay_plate="", notes=notes)


# ------------------------------------------------------ construction


def test_teams_presenter_loads_rows_with_rider_counts_from_a_pooled_mixed_roster() -> None:
    """Construction renders the TEAM entries with their rider counts."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    code0 = seeded_card_codes(_SEED)[0]

    TeamsPresenter(view, roster)

    assert view.teams == [
        TeamRow(name="Trail Blazers", rider_count=2, logo_card=code0, has_image=False)
    ]
    assert view.relay_plate_visible is False
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.logo == {"card": None, "image": None}
    assert view.members == []


def test_teams_presenter_load_on_a_relay_ride_shows_the_relay_plate_row() -> None:
    """The relay plate row appears only on team_relay rides."""
    view = RecordingTeamsView()
    roster = _draft_relay_roster()

    TeamsPresenter(view, roster)

    assert view.relay_plate_visible is True


# ------------------------------------------------------------ selection


def test_teams_presenter_row_selection_fills_the_form_members_and_logo() -> None:
    """Selecting a team shows its record, members and card preview."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_pooled_roster())
    code0 = seeded_card_codes(_SEED)[0]

    presenter.on_row_selected(0)

    assert view.form == {
        "name": "Trail Blazers",
        "relay_plate": "",
        "notes": "",
    }
    assert view.logo == {"card": code0, "image": None}
    assert view.members == ["A. Roy", "K. Singh"]


def test_teams_presenter_row_selection_shows_the_relay_plate_of_a_relay_team() -> None:
    """A relay team's own plate fills the Plate (relay) input."""
    view = RecordingTeamsView()
    presenter = TeamsPresenter(view, _draft_relay_roster())

    presenter.on_row_selected(0)

    assert view.form["name"] == "Moss Ridge"
    assert view.form["relay_plate"] == "88"
    assert view.logo == {
        "card": seeded_card_codes(_SEED)[0],
        "image": None,
    }


# ---------------------------------------- single-member filter


def test_teams_presenter_renders_all_teams_by_default() -> None:
    """The filter starts off, so every TEAM row renders."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster_with_size_one_team()

    TeamsPresenter(view, roster)

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
    presenter.on_save(_new_form("Dirt Dynamos"))

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
    presenter.on_save(_new_form("Trail Blazers", notes="cap 88"))

    assert _teams(roster)[0].notes == "cap 88"


def test_teams_presenter_save_with_no_selection_is_a_no_op() -> None:
    """Nothing selected, nothing saved -- the roster stays untouched."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = roster.audit_log

    presenter.on_save(_new_form("Ghost"))

    assert roster.audit_log == before


def test_teams_presenter_save_refuses_a_duplicate_rename_via_validation() -> None:
    """Renaming onto another team's name (folded case) is refused."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster_with_size_one_team()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(1)
    presenter.on_save(_new_form("LONE WOLF"))

    team = _teams(roster)[1]
    assert team.display_name == "Trail Blazers"
    assert view.validation == ['a team named "LONE WOLF" already exists']


def test_teams_presenter_save_allows_renaming_to_the_same_name_in_another_case() -> None:
    """A team may rename to its own name in any case (no self-dup)."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_save(_new_form("TRAIL BLAZERS"))

    assert _teams(roster)[0].display_name == "TRAIL BLAZERS"


# ------------------------------------------------------------------ add


def test_teams_presenter_add_creates_a_team_from_the_form_name() -> None:
    """Add team reads the form: the typed name becomes a roster team."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_card == seeded_card_codes(_SEED)[1]
    assert [row.name for row in view.teams] == ["Trail Blazers", "Dirt Dynamos"]
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.logo == {"card": None, "image": None}
    assert view.members == []


def test_teams_presenter_add_given_a_blank_name_refuses_via_validation() -> None:
    """A whitespace-only name creates nothing and says so."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add(_new_form("   "))

    assert len(roster.entries) == before
    assert view.validation == ["enter a team name"]


def test_teams_presenter_add_given_a_duplicate_name_refuses_via_validation() -> None:
    """A name matching an existing team (folded, trimmed) is refused."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add(_new_form("  trail blazers  "))

    assert len(roster.entries) == before
    assert view.validation == ['a team named "trail blazers" already exists']


def test_teams_presenter_add_persists_the_form_notes_on_the_new_team() -> None:
    """Notes typed into the Add form land on the created team."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_add(_new_form("Dirt Dynamos", notes="cap 88"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.notes == "cap 88"


def test_teams_presenter_add_with_a_staged_card_uses_that_card() -> None:
    """A card picked before Add becomes the new team's logo card."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_pick_card()
    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_card == seeded_card_codes(_SEED)[1]
    assert created.logo_png is None
    assert view.logo == {"card": None, "image": None}


def test_teams_presenter_add_with_a_staged_image_sets_the_image() -> None:
    """An image picked before Add becomes the new team's logo image."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_pick_image(b"team-logo-png")
    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_png == b"team-logo-png"
    assert created.logo_card is None
    assert view.logo == {"card": None, "image": None}


def test_teams_presenter_add_after_start_refuses_via_validation() -> None:
    """Add is DRAFT-only: a started ride refuses on the info bar."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    roster.status = RideStatus.RUNNING
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add(_new_form("Dirt Dynamos"))

    assert len(roster.entries) == before
    assert any("cannot be started" in message for message in view.validation)


def test_teams_presenter_add_on_a_solo_only_ride_refuses_via_validation() -> None:
    """A solo-only roster cannot hold teams (R-11), and says so."""
    view = RecordingTeamsView()
    roster = Roster(entry_mode=EntryMode.SOLO)
    presenter = TeamsPresenter(view, roster)
    before = len(roster.entries)

    presenter.on_add(_new_form("Dirt Dynamos"))

    assert len(roster.entries) == before
    assert any("solo-only" in message for message in view.validation)


def test_teams_presenter_add_on_a_relay_ride_gives_the_team_a_plate() -> None:
    """A relay team is created with its own next free entry plate."""
    view = RecordingTeamsView()
    roster = _draft_relay_roster()
    presenter = TeamsPresenter(view, roster)
    expected_plate = roster.next_free_plate()  # "124": one past 123/88

    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.plate == expected_plate
    assert created.logo_card == seeded_card_codes(_SEED)[1]


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
    assert view.form == {"name": "", "relay_plate": "", "notes": ""}
    assert view.logo == {"card": None, "image": None}


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


# --------------------------------------------------------- logo picks


def test_teams_presenter_pick_card_advances_to_the_next_unused_card() -> None:
    """Each Pick card click on a selected team walks the seeded deck."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    codes = seeded_card_codes(_SEED)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_card()

    assert team.logo_card == codes[1]
    assert view.logo == {"card": codes[1], "image": None}


def test_teams_presenter_pick_card_after_an_image_makes_the_card_win() -> None:
    """Picking a card on a selected team clears its logo image."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_image(b"team-logo-png")
    presenter.on_pick_card()

    # The image cleared the team's card, so the pick resumes from the
    # deck's first unused code -- the team's own original one.
    assert team.logo_card == seeded_card_codes(_SEED)[0]
    assert team.logo_png is None
    assert view.logo["image"] is None


def test_teams_presenter_pick_image_sets_the_bytes_and_image_wins() -> None:
    """Choosing an image on a selected team replaces any card."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    team = _teams(roster)[0]

    presenter.on_row_selected(0)
    presenter.on_pick_image(b"team-logo-png")

    assert team.logo_png == b"team-logo-png"
    assert team.logo_card is None
    assert view.logo == {"card": None, "image": b"team-logo-png"}


# --------------------------- staging (Add mode, no selection)


def test_teams_presenter_pick_card_with_no_selection_stages_the_next_unused_card() -> None:
    """With nothing selected, Pick card only stages a pending logo."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = roster.audit_log

    presenter.on_pick_card()

    assert view.logo == {"card": seeded_card_codes(_SEED)[1], "image": None}
    assert roster.audit_log == before


def test_teams_presenter_pick_image_with_no_selection_stages_the_image() -> None:
    """With nothing selected, Image… only stages the picked bytes."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    before = roster.audit_log

    presenter.on_pick_image(b"team-logo-png")

    assert view.logo == {"card": None, "image": b"team-logo-png"}
    assert roster.audit_log == before


def test_teams_presenter_staged_image_wins_over_a_previously_staged_card() -> None:
    """An image picked while a card is staged swaps the pending logo."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_pick_card()
    presenter.on_pick_image(b"team-logo-png")
    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_png == b"team-logo-png"
    assert created.logo_card is None


def test_teams_presenter_staged_card_wins_over_a_previously_staged_image() -> None:
    """A card picked while an image is staged swaps the pending logo."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_pick_image(b"team-logo-png")
    presenter.on_pick_card()
    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_card == seeded_card_codes(_SEED)[1]
    assert created.logo_png is None


def test_teams_presenter_repeated_pick_card_clicks_cycle_a_staged_card() -> None:
    """Each staged Pick card click walks to the next unused code."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    codes = seeded_card_codes(_SEED)
    before = roster.audit_log

    presenter.on_pick_card()
    presenter.on_pick_card()

    assert view.logo == {"card": codes[2], "image": None}
    assert roster.audit_log == before


def test_teams_presenter_pick_card_on_an_unseeded_roster_says_so() -> None:
    """No seed means no card to pick for a selected team."""
    view = RecordingTeamsView()
    roster = _unseeded_mixed_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_row_selected(0)
    presenter.on_pick_card()

    assert any("already in use" in message for message in view.validation)


def test_teams_presenter_pick_card_with_no_selection_on_an_unseeded_roster_says_so() -> None:
    """No seed means no card to stage for the Add form either."""
    view = RecordingTeamsView()
    roster = _unseeded_mixed_roster()
    presenter = TeamsPresenter(view, roster)

    presenter.on_pick_card()

    assert any("already in use" in message for message in view.validation)
    assert view.logo == {"card": None, "image": None}


def test_teams_presenter_row_selection_discards_a_staged_logo() -> None:
    """Selecting a team abandons the Add form's pending logo."""
    view = RecordingTeamsView()
    roster = _draft_pooled_roster()
    presenter = TeamsPresenter(view, roster)
    codes = seeded_card_codes(_SEED)

    presenter.on_pick_card()  # stages codes[1]
    presenter.on_pick_card()  # stages codes[2]
    presenter.on_row_selected(0)
    presenter.on_add(_new_form("Dirt Dynamos"))

    created = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    assert created.logo_card == codes[1]  # the auto code, not the stale staged codes[2]


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


# ------------------------------------------------------- format_logo


@pytest.mark.parametrize(
    ("logo_card", "has_image", "expected"),
    [
        pytest.param(None, False, "", id="blank"),
        pytest.param("AS", False, CARD_TEXT, id="card"),
        pytest.param(None, True, IMAGE_TEXT, id="image_over_blank"),
        pytest.param("AS", True, IMAGE_TEXT, id="image_over_card"),
    ],
)
def test_format_logo_returns_card_image_or_blank_cell(
    logo_card: str | None,
    has_image: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected: str,
) -> None:
    """The Logo cell shows the logo's kind, never the card code."""
    assert format_logo(logo_card, has_image=has_image) == expected


@given(card=st.one_of(st.none(), st.text()), has_image=st.booleans())
def test_format_logo_given_any_logo_state_returns_only_the_three_kind_texts(
    card: str | None,
    has_image: bool,  # noqa: FBT001 -- a Hypothesis draw, not a call-site bool
) -> None:
    """format_logo's output alphabet is exactly Card/Image/blank."""
    text = format_logo(card, has_image=has_image)

    assert text in (CARD_TEXT, IMAGE_TEXT, "")
    assert (text == IMAGE_TEXT) == has_image
    assert (text == CARD_TEXT) == (card is not None and not has_image)
