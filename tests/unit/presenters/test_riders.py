# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the riders presenter (E3.2.1/E3.2.2), tests-first.

``RidersPresenter`` now drives ``rider_editor_dlg`` from a real,
in-memory :class:`~rivercrossing.roster.Roster` -- the ``(view,
data_source)`` no-op shape from E1.2.3 is gone for this presenter
(see ``test_protocols.py``'s own pins, updated alongside this file).
``RecordingRidersView`` follows ``test_protocols.py``'s
``RecordingConsoleView`` pattern: a hand-written fake recording
every call, in order, with its exact arguments -- no
``unittest.mock`` is needed since this presenter touches no I/O
boundary (T-10).

The 2026-08-09 follow-on decision lets Add/Save build teams one
rider at a time: "New team..." and joining an existing team both
compose ``Roster.create_team_entry_of_one`` -- not
``create_solo_entry`` + ``move_rider`` as first proposed, since
``move_rider`` rejects a solo entry on either side unconditionally
(see ``tests/unit/test_roster.py``'s
``test_move_rider_into_a_solo_entry_raises_invalid_move_error`` and
its two siblings) -- with ``Roster.move_rider`` to fold into an
existing team, rolling the transient team back on a refused move.
"""

from __future__ import annotations

import string
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.presenters.riders import (
    NEW_TEAM_CHOICE,
    SOLO_TEAM_CHOICE,
    CsvConflict,
    CsvPreview,
    RiderFormValues,
    RidersPresenter,
    _rider_rows,
    _team_choices,
)

# tests/unit/fixtures/csv/ is test_csvio.py's own fixture home (its
# module docstring); reused here rather than re-derived, per E3.4's
# own brief.
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "csv"

# ------------------------------------------------------------- fixtures


class RecordingRidersView:
    """A complete ``RidersView`` spy recording each call, in order.

    ``new_team_name`` is the canned return for ``prompt_new_team_name``
    -- ``None`` reproduces the operator cancelling the native prompt the
    view builds next session (R-20's "New team..." flow).
    """

    def __init__(self) -> None:
        """Start with an empty call log and a cancelled team prompt."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.new_team_name: str | None = None

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Record the rendered riders_list rows."""
        self.calls.append(("show_riders", (rows,)))

    def show_team_choices(self, names: list[str]) -> None:
        """Record the rendered team_choice content."""
        self.calls.append(("show_team_choices", (names,)))

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Record delete_btn's enabled state."""
        self.calls.append(("set_delete_enabled", (enabled,)))

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Record the rendered CSV preview (unused by this suite)."""
        self.calls.append(("show_csv_preview", (preview,)))

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Record wxID_OK's enabled state (unused by this suite)."""
        self.calls.append(("set_import_enabled", (enabled,)))

    def show_form(  # noqa: PLR0913 -- test spy mirrors the view's four-field contract
        self, *, plate: str, first_name: str, last_name: str, team: str
    ) -> None:
        """Record the filled form fields."""
        self.calls.append(("show_form", (plate, first_name, last_name, team)))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the Team column/team_choice visibility."""
        self.calls.append(("set_team_ui_visible", (visible,)))

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.calls.append(("show_validation", (message,)))

    def prompt_new_team_name(self) -> str | None:
        """Record the prompt and return the canned ``new_team_name``."""
        self.calls.append(("prompt_new_team_name", ()))
        return self.new_team_name


def _draft_solo_roster() -> Roster:
    """Return a DRAFT roster with one solo entry, plate 123."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    return roster


def _draft_mixed_roster() -> Roster:
    """Return a mixed, pooled DRAFT roster with one 2-rider team."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _draft_relay_roster() -> Roster:
    """Return a mixed, team_relay DRAFT roster with one 2-rider team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy"),
            Rider(first_name="K.", last_name="Singh"),
        ],
        plate="77",
    )
    return roster


# ------------------------------------------------------ construction


def test_riders_presenter_holds_the_view_and_roster_given() -> None:
    """The presenter stores the exact view and roster given (E3.2.1)."""
    view = RecordingRidersView()
    roster = Roster()

    presenter = RidersPresenter(view, roster)

    assert (presenter.view, presenter.roster) == (view, roster)


# --------------------------------------------------- initial load


def test_riders_presenter_init_given_empty_roster_shows_no_rows() -> None:
    """An empty roster renders an empty riders_list (T-4: [])."""
    view = RecordingRidersView()

    RidersPresenter(view, Roster())

    assert ("show_riders", ([],)) in view.calls


def test_riders_presenter_init_given_mixed_roster_calls_view_in_order() -> None:
    """Construction renders rows, team UI, choices, and the form."""
    view = RecordingRidersView()
    roster = _draft_mixed_roster()

    RidersPresenter(view, roster)

    assert view.calls == [
        (
            "show_riders",
            (
                [
                    RiderRow(plate="77", name="A. Roy", team="Trail Blazers"),
                    RiderRow(plate="78", name="K. Singh", team="Trail Blazers"),
                ],
            ),
        ),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers", NEW_TEAM_CHOICE],)),
        ("set_team_ui_visible", (True,)),
        ("show_form", ("79", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
    ]


def test_riders_presenter_init_given_solo_only_mode_hides_team_ui() -> None:
    """A bare (solo-only) Roster hides the team UI (R-11)."""
    view = RecordingRidersView()

    RidersPresenter(view, Roster())

    assert ("set_team_ui_visible", (False,)) in view.calls


def test_riders_presenter_init_given_mixed_mode_shows_team_ui() -> None:
    """A mixed-mode Roster shows the team UI (R-11)."""
    view = RecordingRidersView()

    RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED))

    assert ("set_team_ui_visible", (True,)) in view.calls


def test_riders_presenter_init_given_relay_roster_maps_entry_plate_to_every_member() -> None:
    """team_relay: Plate column is the entry's plate, not a rider's."""
    view = RecordingRidersView()
    roster = _draft_relay_roster()

    RidersPresenter(view, roster)

    assert (
        "show_riders",
        (
            [
                RiderRow(plate="77", name="A. Roy", team="Trail Blazers"),
                RiderRow(plate="77", name="K. Singh", team="Trail Blazers"),
            ],
        ),
    ) in view.calls


def test_riders_presenter_init_given_solo_entry_maps_team_column_to_none() -> None:
    """A solo entry's row carries no Team value (R-20)."""
    view = RecordingRidersView()
    roster = _draft_solo_roster()

    RidersPresenter(view, roster)

    assert ("show_riders", ([RiderRow(plate="123", name="Sam Ellis", team=None)],)) in view.calls


# ------------------------------------------------- on_row_selected


def test_on_row_selected_given_a_solo_row_fills_the_form_with_the_solo_sentinel() -> None:
    """Selecting a solo row fills the form with team = solo (R-20)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("123", "Sam", "Ellis", SOLO_TEAM_CHOICE)) in view.calls


def test_on_row_selected_given_a_team_row_fills_the_form_with_the_team_name() -> None:
    """Selecting a team member's row fills the form with their team."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_mixed_roster())
    view.calls.clear()

    presenter.on_row_selected(1)

    assert ("show_form", ("78", "K.", "Singh", "Trail Blazers")) in view.calls


def test_on_row_selected_given_draft_entry_without_data_enables_delete() -> None:
    """A DRAFT entry with no recorded data may be deleted (R-15)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_delete_enabled", (True,)) in view.calls


def test_on_row_selected_given_entry_with_data_disables_delete() -> None:
    """An entry carrying recorded data may never be deleted (R-15)."""
    roster = _draft_solo_roster()
    roster.mark_has_data(roster.entries[0])
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_delete_enabled", (False,)) in view.calls


# -------------------------------------------------------- on_add


def test_on_add_given_a_solo_form_creates_the_entry() -> None:
    """Add with team = solo creates a solo entry (R-20)."""
    roster = Roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="1", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert [entry.display_name for entry in roster.entries] == ["Sam Ellis"]


def test_on_add_given_a_solo_form_refreshes_rows_and_prefills_the_next_plate() -> None:
    """Add re-renders the rows and prefills the new next-free plate."""
    roster = Roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_add(
        RiderFormValues(plate="1", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        ("show_riders", ([RiderRow(plate="1", name="Sam Ellis", team=None)],)),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, NEW_TEAM_CHOICE],)),
        ("show_form", ("2", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
    ]


def test_on_add_given_a_duplicate_plate_shows_validation_and_does_not_crash() -> None:
    """A colliding plate refuses via show_validation, not a crash."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_add(
        RiderFormValues(plate="123", first_name="Dupe", last_name="", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("plate '123' is already in use",))]


def test_on_add_given_a_duplicate_plate_leaves_the_roster_unchanged() -> None:
    """A refused add creates no entry (a state, not a call, check)."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="123", first_name="Dupe", last_name="", team=SOLO_TEAM_CHOICE)
    )

    assert [entry.display_name for entry in roster.entries] == ["Sam Ellis"]


def test_on_add_given_new_team_choice_cancelled_is_a_no_op() -> None:
    """Cancelling "New team..." performs no mutation (R-20)."""
    view = RecordingRidersView()
    view.new_team_name = None
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED))
    view.calls.clear()

    presenter.on_add(
        RiderFormValues(plate="1", first_name="A.", last_name="Roy", team=NEW_TEAM_CHOICE)
    )

    assert view.calls == [("prompt_new_team_name", ())]


def test_on_add_given_new_team_choice_cancelled_creates_no_entry() -> None:
    """A cancelled "New team..." prompt leaves the roster empty."""
    view = RecordingRidersView()
    view.new_team_name = None
    roster = Roster(entry_mode=EntryMode.MIXED)
    presenter = RidersPresenter(view, roster)

    presenter.on_add(
        RiderFormValues(plate="1", first_name="A.", last_name="Roy", team=NEW_TEAM_CHOICE)
    )

    assert roster.entries == ()


def test_on_add_given_new_team_choice_with_a_name_creates_a_size_one_team() -> None:
    """A new team may start at size one now (E3.2, R-12 deferred)."""
    view = RecordingRidersView()
    view.new_team_name = "Wolf Pack"
    roster = Roster(entry_mode=EntryMode.MIXED)
    presenter = RidersPresenter(view, roster)

    presenter.on_add(
        RiderFormValues(plate="1", first_name="A.", last_name="Roy", team=NEW_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert (entry.display_name, [r.full_name for r in entry.riders]) == ("Wolf Pack", ["A. Roy"])


def test_on_add_given_new_team_choice_in_relay_uses_the_form_plate_as_entry_plate() -> None:
    """A new team_relay team's plate is the form's plate (E3.2)."""
    view = RecordingRidersView()
    view.new_team_name = "Wolf Pack"
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    presenter = RidersPresenter(view, roster)

    presenter.on_add(
        RiderFormValues(plate="5", first_name="A.", last_name="Roy", team=NEW_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert (entry.plate, entry.riders[0].plate) == ("5", None)


def test_on_add_given_new_team_choice_with_a_name_refreshes_and_prefills() -> None:
    """A successful new-team add re-renders rows and resets the form."""
    view = RecordingRidersView()
    view.new_team_name = "Wolf Pack"
    roster = Roster(entry_mode=EntryMode.MIXED)
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_add(
        RiderFormValues(plate="1", first_name="A.", last_name="Roy", team=NEW_TEAM_CHOICE)
    )

    assert view.calls == [
        ("prompt_new_team_name", ()),
        ("show_riders", ([RiderRow(plate="1", name="A. Roy", team="Wolf Pack")],)),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Wolf Pack", NEW_TEAM_CHOICE],)),
        ("show_form", ("2", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
    ]


def test_on_add_given_an_existing_pooled_team_name_joins_the_team() -> None:
    """Add onto an existing pooled team folds the rider in (E3.2)."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "L. Marchetti"]


def test_on_add_given_an_existing_pooled_team_name_leaves_no_stray_entry() -> None:
    """Joining an existing team leaves exactly one entry named it."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert [e.display_name for e in roster.entries] == ["Trail Blazers"]


def test_on_add_given_an_existing_relay_team_name_joins_it_plateless() -> None:
    """Joining an existing relay team drops the new rider's plate."""
    roster = _draft_relay_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="99", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [r.plate for r in team.riders] == [None, None, None]


def test_on_add_given_an_existing_team_at_max_size_shows_validation() -> None:
    """Joining a team already at max_team_size refuses (R-12)."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=2)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_add(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert (
        "show_validation",
        ("move would exceed the destination team's max size",),
    ) in view.calls


def test_on_add_given_an_existing_team_at_max_size_rolls_back_the_transient() -> None:
    """A refused join leaves no stray transient team behind (E3.2)."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=2)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_add(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert [e.display_name for e in roster.entries] == ["Trail Blazers"]


# ------------------------------------------------------- on_save


def test_on_save_given_nothing_selected_is_a_no_op() -> None:
    """Save with no prior selection makes no view call at all."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Renamed", last_name="", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == []


def test_on_save_given_a_solo_selection_renames_the_rider_and_entry() -> None:
    """Save on a solo row renames the rider and its entry (R-20)."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert (entry.display_name, entry.riders[0].full_name) == ("Samuel Ellis", "Samuel Ellis")


def test_on_save_given_a_team_member_selection_renames_only_the_rider() -> None:
    """Save on a team member's row renames the rider, not the team."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="77", first_name="Alex", last_name="Roy", team="Trail Blazers")
    )

    entry = roster.entries[0]
    assert (entry.display_name, entry.riders[0].full_name) == ("Trail Blazers", "Alex Roy")


def test_on_save_given_a_solo_selection_changes_the_plate() -> None:
    """Save with a new plate updates a solo entry's plate (R-20)."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert (entry.plate, entry.riders[0].plate) == ("200", "200")


def test_on_save_given_a_duplicate_solo_plate_shows_validation_not_crash() -> None:
    """A colliding plate on Save refuses via show_validation (E3.2)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="77", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("plate '77' is already in use",))]


def test_on_save_given_a_duplicate_solo_plate_leaves_the_plate_unchanged() -> None:
    """A refused plate change leaves the entry's plate as it was."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="77", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert roster.entries[0].plate == "123"


def test_on_save_given_a_post_start_plate_change_shows_validation_not_crash() -> None:
    """A plate change after start refuses via show_validation (R-15)."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    roster.status = RideStatus.RUNNING
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        ("show_validation", ("plates cannot be changed once the ride is running",))
    ]


def test_on_save_given_a_post_start_refusal_leaves_the_name_unchanged_too() -> None:
    """A refused save is atomic: the name also stays as it was."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)
    roster.status = RideStatus.RUNNING

    presenter.on_save(
        RiderFormValues(plate="200", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert roster.entries[0].riders[0].full_name == "Sam Ellis"


def test_on_save_given_a_pooled_team_member_changes_their_own_plate() -> None:
    """Save on a pooled team member updates just their own plate."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy, plate 77

    presenter.on_save(
        RiderFormValues(plate="90", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    entry = roster.entries[0]
    assert [r.plate for r in entry.riders] == ["90", "78"]


def test_on_save_given_a_pooled_team_member_recomputes_the_teams_plate() -> None:
    """Changing the lowest-plate member recomputes the team's plate."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy, currently the lowest at 77

    presenter.on_save(
        RiderFormValues(plate="90", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert roster.entries[0].plate == "78"


def test_on_save_given_a_duplicate_pooled_plate_shows_validation_not_crash() -> None:
    """A colliding plate on a pooled team member also refuses (E3.2)."""
    roster = _draft_mixed_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)  # A. Roy, plate 77
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="78", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '78' is already in use",))]


def test_on_save_given_a_relay_team_member_changes_the_teams_plate() -> None:
    """Save on a relay team member updates the team's shared plate."""
    roster = _draft_relay_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy

    presenter.on_save(
        RiderFormValues(plate="99", first_name="Alex", last_name="Roy", team="Trail Blazers")
    )

    entry = roster.entries[0]
    assert (entry.plate, [r.plate for r in entry.riders]) == ("99", [None, None])


def test_on_save_given_a_duplicate_relay_plate_shows_validation_not_crash() -> None:
    """A colliding plate on a relay team also refuses (E3.2)."""
    roster = _draft_relay_roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)  # A. Roy, on the relay team (plate 77)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '123' is already in use",))]


def test_on_save_given_a_duplicate_relay_plate_leaves_the_plate_unchanged() -> None:
    """A refused relay plate change leaves the plate as it was."""
    roster = _draft_relay_roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="123", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert roster.entries[0].plate == "77"


def test_on_save_given_a_post_start_relay_plate_change_shows_validation() -> None:
    """A relay plate change after start refuses via show_validation."""
    roster = _draft_relay_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    roster.status = RideStatus.RUNNING
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="99", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [
        ("show_validation", ("plates cannot be changed once the ride is running",))
    ]


def test_on_save_given_the_same_relay_plate_stays_a_silent_no_op() -> None:
    """Resubmitting a relay team's own plate saves as a normal no-op."""
    roster = _draft_relay_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="77", first_name="Alex", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [
        (
            "show_riders",
            (
                [
                    RiderRow(plate="77", name="Alex Roy", team="Trail Blazers"),
                    RiderRow(plate="77", name="K. Singh", team="Trail Blazers"),
                ],
            ),
        ),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers", NEW_TEAM_CHOICE],)),
    ]


def test_on_save_refreshes_the_rows_after_renaming() -> None:
    """A successful rename re-renders riders_list and team_choice."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        ("show_riders", ([RiderRow(plate="123", name="Samuel Ellis", team=None)],)),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, NEW_TEAM_CHOICE],)),
    ]


def test_on_save_given_a_removed_entry_shows_validation_not_crash() -> None:
    """A stale selection (entry removed meanwhile) refuses cleanly."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    roster.delete_entry(roster.entries[0])
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("entry is not a member of this roster",))]


# ----------------------------------------------------- on_delete


def test_on_delete_given_nothing_selected_is_a_no_op() -> None:
    """Delete with no prior selection makes no view call at all."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == []


def test_on_delete_given_a_draft_entry_without_data_deletes_it() -> None:
    """Delete on a DRAFT entry with no data removes it (R-15)."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_delete()

    assert roster.entries == ()


def test_on_delete_given_an_entry_with_data_shows_a_dnf_or_void_message() -> None:
    """A refusal from recorded data names the DNF/void path (R-15)."""
    roster = _draft_solo_roster()
    roster.mark_has_data(roster.entries[0])
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == [
        ("show_validation", ("entry has recorded data; DNF or void it instead of deleting",))
    ]


def test_on_delete_given_a_post_start_ride_shows_a_status_message() -> None:
    """A refusal from a started ride names the current status (R-15)."""
    roster = _draft_solo_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == [
        ("show_validation", ("entries can no longer be deleted once the ride is running",))
    ]


def test_on_delete_prefills_the_next_free_plate_after_deleting() -> None:
    """After deleting the only entry, the form prefills plate 1."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert ("show_form", ("1", "", "", SOLO_TEAM_CHOICE)) in view.calls


# -------------------------------------------------- property test T-7


@given(
    team_names=st.lists(
        st.text(alphabet=string.ascii_letters, min_size=1, max_size=8),
        min_size=0,
        max_size=4,
        unique=True,
    )
)
def test_team_choices_given_n_teams_always_wraps_them_in_the_two_sentinels(
    team_names: list[str],
) -> None:
    """_team_choices always wraps solo/new-team, for any N (T-7)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    # logic-coverage-exempt: T-8 -- this loop is pure Arrange (building
    # a Hypothesis-sized roster fixture), not decision logic; the one
    # Act/Assert below runs exactly once, after the loop completes.
    for index, name in enumerate(team_names):
        roster.create_team_entry(
            display_name=name,
            riders=[
                Rider(first_name="A", last_name="", plate=str(index * 2 + 1)),
                Rider(first_name="B", last_name="", plate=str(index * 2 + 2)),
            ],
        )

    choices = _team_choices(roster)

    assert (choices[0], choices[-1]) == (SOLO_TEAM_CHOICE, NEW_TEAM_CHOICE)


@given(
    team_names=st.lists(
        st.text(alphabet=string.ascii_letters, min_size=1, max_size=8),
        min_size=0,
        max_size=4,
        unique=True,
    )
)
def test_rider_rows_given_n_teams_returns_one_row_per_rider(team_names: list[str]) -> None:
    """_rider_rows is length-preserving: one row per rider (T-7)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    # logic-coverage-exempt: T-8 -- this loop is pure Arrange (building
    # a Hypothesis-sized roster fixture), not decision logic; the one
    # Act/Assert below runs exactly once, after the loop completes.
    for index, name in enumerate(team_names):
        roster.create_team_entry(
            display_name=name,
            riders=[
                Rider(first_name="A", last_name="", plate=str(index * 2 + 1)),
                Rider(first_name="B", last_name="", plate=str(index * 2 + 2)),
            ],
        )

    rows = _rider_rows(roster)

    assert len(rows) == sum(len(entry.riders) for entry in roster.entries)


# ------------------------------------------------- skipping the render


def test_riders_presenter_given_load_false_skips_the_initial_render() -> None:
    """csv_preview_dlg's own pairing skips rider_editor's own render.

    Its view never implements show_riders/show_team_choices/
    set_team_ui_visible/show_form/set_delete_enabled for real (E3.4's
    own NotImplementedError stubs, the mirror image of
    RiderEditor's), so ``_load()`` must never call them.
    """
    view = RecordingRidersView()

    RidersPresenter(view, Roster(), load=False)

    assert view.calls == []


# --------------------------------------------------- picking a csv file


def _write_pooled_csv(directory: Path, rows: str) -> Path:
    """Write a minimal unified-format CSV fixture; return its path."""
    path = directory / "riders.csv"
    path.write_text(f"firstname,lastname,type,teamname,number,notes\n{rows}", encoding="utf-8")
    return path


def test_on_pick_csv_import_given_a_clean_file_shows_the_exact_summary(
    tmp_path: Path,
) -> None:
    """R-21: "<name> -> N riders x M teams x K conflicts" (E3.4)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    path = _write_pooled_csv(tmp_path, "Alex,Ferreira,solo,,1,\nBo,Lindqvist,solo,,2,\n")

    presenter.on_pick_csv_import(path)

    assert (
        "show_csv_preview",
        (CsvPreview(summary="riders.csv → 2 riders · 0 teams · 0 conflicts", conflicts=()),),
    ) in view.calls


def test_on_pick_csv_import_given_a_clean_file_enables_import() -> None:
    """wxID_OK gates on conflicts == 0, per the E1 view-model (R-21)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "clean_pooled.csv")

    assert ("set_import_enabled", (True,)) in view.calls


def test_on_pick_csv_import_given_clean_pooled_fixture_shows_its_known_counts() -> None:
    """clean_pooled.csv: 4 solo + Falcons(3) + Hawks(2) = 9 riders."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "clean_pooled.csv")

    assert (
        "show_csv_preview",
        (CsvPreview(summary="clean_pooled.csv → 9 riders · 2 teams · 0 conflicts", conflicts=()),),
    ) in view.calls


def test_on_pick_csv_import_given_a_conflicted_file_shows_the_conflict_row() -> None:
    """dup_plate.csv: one duplicate-plate conflict at its second row."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(plate_model=PlateModel.TEAM_RELAY), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "dup_plate.csv")

    assert (
        "show_csv_preview",
        (
            CsvPreview(
                summary="dup_plate.csv → 2 riders · 0 teams · 1 conflicts",
                conflicts=(CsvConflict(row=3, problem="duplicate plate 1"),),
            ),
        ),
    ) in view.calls


def test_on_pick_csv_import_given_a_conflicted_file_disables_import() -> None:
    """wxID_OK stays disabled while any conflict remains (R-21)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(plate_model=PlateModel.TEAM_RELAY), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "dup_plate.csv")

    assert ("set_import_enabled", (False,)) in view.calls


def test_on_pick_csv_import_given_a_header_only_file_shows_zero_of_everything(
    tmp_path: Path,
) -> None:
    """T-4 collection boundary: a header-only file previews clean."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    path = _write_pooled_csv(tmp_path, rows="")

    presenter.on_pick_csv_import(path)

    assert (
        "show_csv_preview",
        (CsvPreview(summary="riders.csv → 0 riders · 0 teams · 0 conflicts", conflicts=()),),
    ) in view.calls


# -------------------------------------------- confirming a csv import


def test_on_confirm_csv_import_given_no_prior_preview_is_a_safe_no_op() -> None:
    """T-3: on_confirm_csv_import's own guard, never crashing (E3.4)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    result = presenter.on_confirm_csv_import()

    assert result is False


def test_on_confirm_csv_import_given_a_clean_preview_applies_it_to_the_roster(
    tmp_path: Path,
) -> None:
    """A clean commit inserts the file's riders into the roster."""
    roster = Roster()
    presenter = RidersPresenter(RecordingRidersView(), roster, load=False)
    path = _write_pooled_csv(tmp_path, "Alex,Ferreira,solo,,1,\nBo,Lindqvist,solo,,2,\n")
    presenter.on_pick_csv_import(path)

    result = presenter.on_confirm_csv_import()

    assert result is True
    assert [entry.display_name for entry in roster.entries] == ["Alex Ferreira", "Bo Lindqvist"]


def test_on_confirm_csv_import_given_a_clean_preview_makes_no_further_view_call(
    tmp_path: Path,
) -> None:
    """A successful commit calls no RidersView member at all (E3.4).

    ``CsvPreviewDialog`` -- the only real caller -- never implements
    ``show_riders``/``show_team_choices`` (module docstring's own
    mirror-image split), so this handler must not call them: a live
    ``RiderEditor`` sees the imported roster next time it is
    (re)opened, ``RidersPresenter.__init__`` reading it fresh.
    """
    roster = Roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster, load=False)
    path = _write_pooled_csv(tmp_path, "Alex,Ferreira,solo,,1,\n")
    presenter.on_pick_csv_import(path)
    view.calls.clear()

    presenter.on_confirm_csv_import()

    assert view.calls == []


def test_on_confirm_csv_import_given_conflicts_present_shows_validation_not_crash(
    tmp_path: Path,
) -> None:
    """A refused commit (conflicts present) shows the reason (E3.4)."""
    roster = Roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster, load=False)
    path = _write_pooled_csv(tmp_path, "Alex,Ferreira,solo,,1,\nBo,Lindqvist,solo,,1,\n")
    presenter.on_pick_csv_import(path)
    view.calls.clear()

    result = presenter.on_confirm_csv_import()

    assert result is False
    assert view.calls == [
        ("show_validation", ("1 conflict(s) must be resolved before importing",))
    ]


def test_on_confirm_csv_import_given_conflicts_present_leaves_the_roster_unchanged(
    tmp_path: Path,
) -> None:
    """A refused commit mutates nothing (a state, not a call, check)."""
    roster = Roster()
    presenter = RidersPresenter(RecordingRidersView(), roster, load=False)
    path = _write_pooled_csv(tmp_path, "Alex,Ferreira,solo,,1,\nBo,Lindqvist,solo,,1,\n")
    presenter.on_pick_csv_import(path)

    presenter.on_confirm_csv_import()

    assert roster.entries == ()


# -------------------------------------------------------- on_export_csv


def test_on_export_csv_writes_the_rosters_own_header(tmp_path: Path) -> None:
    """on_export_csv delegates straight to csvio.export (R-21)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Alex", last_name="Ferreira", plate="1")
    presenter = RidersPresenter(RecordingRidersView(), roster, load=False)
    path = tmp_path / "export.csv"

    presenter.on_export_csv(path)

    assert (
        path.read_text(encoding="utf-8").splitlines()[0]
        == "FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES"
    )


# ------------------------------------------------------------ refresh


def test_riders_presenter_refresh_re_renders_rows_and_team_choices() -> None:
    """RiderEditor's own import_btn calls this after a commit (E3.4).

    A public counterpart to the private ``_refresh_rows()`` every
    other handler already calls -- the one entry point a caller
    outside this presenter (``RiderEditor``'s own click handler,
    after a *different* ``RidersPresenter`` instance committed a CSV
    import through ``csv_preview_dlg``) can use to catch this
    editor's own view up with the roster it never itself wrote to.
    """
    roster = Roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    roster.create_solo_entry(first_name="Alex", last_name="Ferreira", plate="1")
    view.calls.clear()

    presenter.refresh()

    assert view.calls == [
        ("show_riders", ([RiderRow(plate="1", name="Alex Ferreira", team=None)],)),
        ("show_team_choices", ([SOLO_TEAM_CHOICE, NEW_TEAM_CHOICE],)),
    ]
