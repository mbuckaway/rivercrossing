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

Joining an existing team composes ``Roster.create_team_entry_of_one``
-- not ``create_solo_entry`` + ``move_rider`` as first proposed,
since ``move_rider`` rejects a solo entry on either side
unconditionally (see ``tests/unit/test_roster.py``'s
``test_move_rider_into_a_solo_entry_raises_invalid_move_error`` and
its two siblings) -- with ``Roster.move_rider`` to fold into the
target team, rolling the transient team back on a refused move. The
"New team..." sentinel is retired on topic/ux-polish: team_choice
lists solo then every team, never a new-team entry, and this suite
pins that sentinel-less shape (``_team_choices`` below).
"""

from __future__ import annotations

import string
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing import csvio
from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster
from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.presenters.riders import (
    SOLO_TEAM_CHOICE,
    AddRiderPresenter,
    CsvConflict,
    CsvPreview,
    RiderFormValues,
    RidersPresenter,
    _pair_rows,
    _plate_order_key,
    _rider_pairs,
    _rider_rows,
    _team_choices,
    _visible_pairs,
)

# tests/unit/fixtures/csv/ is test_csvio.py's own fixture home (its
# module docstring); reused here rather than re-derived, per E3.4's
# own brief.
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "csv"

# ------------------------------------------------------------- fixtures


class RecordingRidersView:
    """A complete ``RidersView`` spy recording each call, in order."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Record the rendered riders_list rows."""
        self.calls.append(("show_riders", (rows,)))

    def show_team_choices(self, names: list[str]) -> None:
        """Record the rendered team_choice content."""
        self.calls.append(("show_team_choices", (names,)))

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Record delete_btn's enabled state."""
        self.calls.append(("set_delete_enabled", (enabled,)))

    def set_save_enabled(self, *, enabled: bool) -> None:
        """Record save_btn's enabled state (W7 dirty gating)."""
        self.calls.append(("set_save_enabled", (enabled,)))

    def set_plate_enabled(self, *, enabled: bool) -> None:
        """Record plate_input's enabled state (W7 plate lock)."""
        self.calls.append(("set_plate_enabled", (enabled,)))

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
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers"],)),
        ("set_team_ui_visible", (True,)),
        ("show_form", ("79", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
        ("set_save_enabled", (False,)),
        ("set_plate_enabled", (True,)),
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


# ------------------------------------------------ on_add_committed


def test_on_add_committed_refreshes_rows_and_prefills_the_next_plate() -> None:
    """After the Add dialog commits, the editor re-renders + resets."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()
    roster.create_solo_entry(first_name="New", last_name="Rider", plate="124")

    presenter.on_add_committed()

    assert view.calls == [
        (
            "show_riders",
            (
                [
                    RiderRow(plate="123", name="Sam Ellis", team=None),
                    RiderRow(plate="124", name="New Rider", team=None),
                ],
            ),
        ),
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
        ("show_form", ("125", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
        ("set_save_enabled", (False,)),
    ]


# -------------------------------------------------- AddRiderPresenter


class RecordingAddRiderView:
    """A complete ``AddRiderView`` spy recording each call, in order."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_team_choices(self, names: list[str]) -> None:
        """Record the rendered team_choice content."""
        self.calls.append(("show_team_choices", (names,)))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the Team row's visibility."""
        self.calls.append(("set_team_ui_visible", (visible,)))

    def show_form(self, *, plate: str, team: str) -> None:
        """Record the prefilled plate/team fields."""
        self.calls.append(("show_form", (plate, team)))

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.calls.append(("show_validation", (message,)))


def test_add_rider_presenter_init_given_a_mixed_roster_renders_the_choices_and_form() -> None:
    """Construction renders team choices, Team UI, and the prefill."""
    view = RecordingAddRiderView()
    roster = _draft_mixed_roster()

    AddRiderPresenter(view, roster)

    assert view.calls == [
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers"],)),
        ("set_team_ui_visible", (True,)),
        ("show_form", ("79", SOLO_TEAM_CHOICE)),
    ]


def test_add_rider_presenter_init_given_a_solo_only_ride_hides_the_team_row() -> None:
    """R-11: a solo-only ride shows no Team choice in the dialog."""
    view = RecordingAddRiderView()

    AddRiderPresenter(view, Roster())

    assert view.calls == [
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
        ("set_team_ui_visible", (False,)),
        ("show_form", ("1", SOLO_TEAM_CHOICE)),
    ]


def test_add_rider_presenter_submit_given_a_solo_form_creates_the_entry_and_returns_true() -> None:
    """Add with team = solo creates a solo entry (R-20)."""
    roster = Roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    created = presenter.on_submit(
        RiderFormValues(plate="1", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert created is True
    assert [entry.display_name for entry in roster.entries] == ["Sam Ellis"]


def test_add_rider_presenter_submit_given_an_existing_team_name_joins_it() -> None:
    """Add onto an existing pooled team folds the rider in (E3.2)."""
    roster = _draft_mixed_roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    created = presenter.on_submit(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert created is True
    team = roster.entries[0]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "L. Marchetti"]


def test_add_rider_presenter_submit_given_a_team_join_leaves_no_stray_entry() -> None:
    """Joining an existing team leaves exactly one entry named it."""
    roster = _draft_mixed_roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    presenter.on_submit(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert [e.display_name for e in roster.entries] == ["Trail Blazers"]


def test_add_rider_presenter_submit_given_a_relay_team_join_lands_plateless() -> None:
    """Joining an existing relay team drops the new rider's plate."""
    roster = _draft_relay_roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    presenter.on_submit(
        RiderFormValues(plate="99", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [r.plate for r in team.riders] == [None, None, None]


def test_add_rider_presenter_submit_given_a_duplicate_plate_returns_false_and_shows_it() -> None:
    """A colliding plate refuses via show_validation, not a crash."""
    view = RecordingAddRiderView()
    presenter = AddRiderPresenter(view, _draft_solo_roster())
    view.calls.clear()

    created = presenter.on_submit(
        RiderFormValues(plate="123", first_name="Dupe", last_name="Rider", team=SOLO_TEAM_CHOICE)
    )

    assert created is False
    assert view.calls == [("show_validation", ("plate '123' is already in use",))]


def test_add_rider_presenter_submit_given_a_duplicate_plate_leaves_the_roster_unchanged() -> None:
    """A refused add creates no entry (a state, not a call, check)."""
    roster = _draft_solo_roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Dupe", last_name="Rider", team=SOLO_TEAM_CHOICE)
    )

    assert [entry.display_name for entry in roster.entries] == ["Sam Ellis"]


def test_add_rider_presenter_submit_given_a_blank_plate_returns_false_and_shows_it() -> None:
    """A blanked plate refuses via the roster's non-empty guard (W7)."""
    view = RecordingAddRiderView()
    presenter = AddRiderPresenter(view, Roster())
    view.calls.clear()

    created = presenter.on_submit(
        RiderFormValues(plate="", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert created is False
    assert view.calls == [("show_validation", ("plate '' must not be empty",))]
    assert presenter.roster.entries == ()


def test_add_rider_presenter_submit_given_an_existing_team_at_max_size_returns_false() -> None:
    """Joining a team already at max_team_size refuses (R-12)."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=2)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    view = RecordingAddRiderView()
    presenter = AddRiderPresenter(view, roster)
    view.calls.clear()

    created = presenter.on_submit(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert created is False
    assert view.calls == [
        ("show_validation", ("move would exceed the destination team's max size",))
    ]
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
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers"],)),
        # W7: the record's form is re-shown after the refresh so
        # team_choice's selection survives show_team_choices' Set().
        ("show_form", ("77", "Alex", "Roy", "Trail Blazers")),
        ("set_delete_enabled", (True,)),
        ("set_save_enabled", (False,)),
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
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
        # W7: the renamed record's form is re-shown, so the form and
        # save_btn always agree with the row just saved.
        ("show_form", ("123", "Samuel", "Ellis", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (True,)),
        ("set_save_enabled", (False,)),
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


# ----------------------------- W7 dirty gating / form-changed seam


def _solo_and_team_roster() -> Roster:
    """Return a pooled DRAFT roster with a solo and one 2-rider team."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _two_team_roster() -> Roster:
    """Return a pooled DRAFT roster with two 2-rider teams."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[
            Rider(first_name="Bo", last_name="Lindqvist", plate="80"),
            Rider(first_name="Cy", last_name="Nguyen", plate="81"),
        ],
    )
    return roster


def _relay_solo_and_team_roster() -> Roster:
    """Return a relay DRAFT roster with a solo and one 2-rider team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="9")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy"),
            Rider(first_name="K.", last_name="Singh"),
        ],
        plate="77",
    )
    return roster


def test_on_row_selected_given_a_selection_starts_with_save_disabled() -> None:
    """A freshly selected row's own values are never "dirty" (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert view.calls[-1] == ("set_save_enabled", (False,))


def test_on_form_changed_given_the_selected_records_own_values_disables_save() -> None:
    """A form equal to the selected record is clean (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (False,))]


def test_on_form_changed_given_an_edited_plate_enables_save() -> None:
    """Editing the plate away from the record dirties the form (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (True,))]


def test_on_form_changed_given_an_edited_first_name_enables_save() -> None:
    """Editing the first name away from the record dirties (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (True,))]


def test_on_form_changed_given_an_edited_last_name_enables_save() -> None:
    """Editing the last name away from the record dirties (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(
            plate="123", first_name="Sam", last_name="Ellis-Smith", team=SOLO_TEAM_CHOICE
        )
    )

    assert view.calls == [("set_save_enabled", (True,))]


def test_on_form_changed_given_an_edited_team_enables_save() -> None:
    """Changing team_choice away from the record dirties (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_mixed_roster())
    presenter.on_row_selected(0)  # A. Roy, on Trail Blazers
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (True,))]


def test_on_form_changed_given_nothing_selected_keeps_save_disabled() -> None:
    """The add form (no selection) never offers a save (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (False,))]


def test_on_form_changed_given_a_reverted_edit_disables_save_again() -> None:
    """Reverting a dirty form back to the record re-cleans it (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    presenter.on_form_changed(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )
    view.calls.clear()

    presenter.on_form_changed(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("set_save_enabled", (False,))]


# --------------------------------- W7 add dialog requires both names


@pytest.mark.parametrize(
    ("first_name", "last_name"),
    [
        ("", "Ellis"),
        ("Sam", ""),
        ("   ", "Ellis"),
        ("Sam", "   "),
    ],
    ids=["blank_first", "blank_last", "blankish_first", "blankish_last"],
)
def test_add_rider_presenter_submit_given_a_blank_name_returns_false_and_shows_it(
    first_name: str,
    last_name: str,
) -> None:
    """Add with a blank first or last name refuses up front (W7)."""
    view = RecordingAddRiderView()
    presenter = AddRiderPresenter(view, Roster())
    view.calls.clear()

    created = presenter.on_submit(
        RiderFormValues(
            plate="1", first_name=first_name, last_name=last_name, team=SOLO_TEAM_CHOICE
        )
    )

    assert created is False
    assert view.calls == [("show_validation", ("First name and last name are required",))]


@pytest.mark.parametrize(
    ("first_name", "last_name"),
    [("", ""), (" ", " ")],
    ids=["both_blank", "both_blankish"],
)
def test_add_rider_presenter_submit_given_blank_names_leaves_the_roster_unchanged(
    first_name: str, last_name: str
) -> None:
    """A refused add mutates nothing (a state, not a call, check)."""
    roster = Roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    presenter.on_submit(
        RiderFormValues(
            plate="1", first_name=first_name, last_name=last_name, team=SOLO_TEAM_CHOICE
        )
    )

    assert roster.entries == ()


# ------------------------------------------- W7 team assignment on save


def test_on_save_given_a_solo_selection_joins_the_chosen_team() -> None:
    """Save with a team chosen folds the solo rider onto that team."""
    roster = _solo_and_team_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # Sam Ellis, solo, plate 123

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [entry.type for entry in roster.entries] == [EntryType.TEAM]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "Sam Ellis"]
    assert team.riders[-1].plate == "123"
    assert team.plate == "77"


def test_on_save_given_a_solo_join_refused_by_a_full_team_leaves_the_roster_unchanged() -> None:
    """A full destination team refuses the join before any delete."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=2)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("team size must be at most 2, got 3",))]
    assert [e.type for e in roster.entries] == [EntryType.SOLO, EntryType.TEAM]


def test_on_save_given_a_solo_join_after_start_shows_validation_not_crash() -> None:
    """A post-start solo join refuses via the roster's delete gate."""
    roster = _solo_and_team_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    roster.status = RideStatus.RUNNING
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    assert view.calls == [
        (
            "show_validation",
            ("entries can no longer be deleted once the ride is running",),
        )
    ]
    assert roster.entries[0].type is EntryType.SOLO


def test_on_save_given_a_solo_selection_joining_a_relay_team_ignores_the_form_plate() -> None:
    """A relay join frees the solo plate; the team plate never moves."""
    roster = _relay_solo_and_team_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # Sam Ellis, solo entry plate 9

    presenter.on_save(
        RiderFormValues(plate="9", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [e.type for e in roster.entries] == [EntryType.TEAM]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "Sam Ellis"]
    assert [r.plate for r in team.riders] == [None, None, None]
    assert team.plate == "77"


def test_on_save_given_a_team_selection_moves_to_the_chosen_team() -> None:
    """Save with another team chosen moves the rider between teams."""
    roster = _two_team_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy, Trail Blazers, plate 77

    presenter.on_save(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team="Moss Ridge")
    )

    trail, moss = roster.entries
    assert [r.full_name for r in trail.riders] == ["K. Singh"]
    assert [r.full_name for r in moss.riders] == ["Bo Lindqvist", "Cy Nguyen", "A. Roy"]
    assert (trail.plate, moss.plate) == ("78", "77")


def test_on_save_given_a_team_selection_leaves_to_solo() -> None:
    """Save with the solo sentinel extracts a pooled member to solo."""
    roster = _draft_mixed_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy, plate 77

    presenter.on_save(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    team, solo = roster.entries
    assert [r.full_name for r in team.riders] == ["K. Singh"]
    assert team.plate == "78"
    assert (solo.display_name, solo.plate, solo.riders[0].plate) == ("A. Roy", "77", "77")


def test_on_save_given_a_relay_team_member_leaving_to_solo_shows_validation() -> None:
    """A relay member has no solo conversion; the roster refuses."""
    roster = _draft_relay_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        (
            "show_validation",
            ("extract_rider_to_solo requires a rider_pooled team member",),
        )
    ]
    assert len(roster.entries[0].riders) == 2


def test_on_save_given_a_pooled_member_leaving_to_solo_after_start_shows_validation() -> None:
    """extract_rider_to_solo is DRAFT-only; a running ride refuses."""
    roster = _draft_mixed_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    roster.status = RideStatus.RUNNING
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        (
            "show_validation",
            ("a rider cannot be extracted to solo once the ride is running",),
        )
    ]
    assert len(roster.entries[0].riders) == 2


def test_on_save_given_a_relay_member_moving_teams_ignores_the_form_plate() -> None:
    """A relay move never rewrites the destination team's plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy"),
            Rider(first_name="K.", last_name="Singh"),
        ],
        plate="77",
    )
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[
            Rider(first_name="Bo", last_name="Lindqvist"),
            Rider(first_name="Cy", last_name="Nguyen"),
        ],
        plate="80",
    )
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)  # A. Roy on Trail Blazers (plate 77 shown)

    presenter.on_save(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team="Moss Ridge")
    )

    trail, moss = roster.entries
    assert [r.full_name for r in trail.riders] == ["K. Singh"]
    assert "A. Roy" in [r.full_name for r in moss.riders]
    assert (trail.plate, moss.plate) == ("77", "80")


def test_on_save_given_a_blank_plate_shows_validation_not_crash() -> None:
    """A blanked plate refuses via the roster's non-empty guard (W7)."""
    roster = _draft_relay_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="   ", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '   ' must not be empty",))]
    assert roster.entries[0].plate == "77"


def test_on_save_given_a_blank_solo_plate_shows_validation_not_crash() -> None:
    """A blanked solo plate refuses too (both plate models) (W7)."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_save(
        RiderFormValues(plate="", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("plate '' must not be empty",))]
    assert roster.entries[0].plate == "123"


# --------------------------- W7 search + sort (riders_list narrowing)


def _three_solo_roster() -> Roster:
    """Return three DRAFT solos in a shuffled plate order."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Bo", last_name="Lindqvist", plate="2")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    return roster


def _searched_rows(view: RecordingRidersView) -> list[RiderRow]:
    """Return the rows of *view*'s last show_riders call."""
    return next(args for name, args in view.calls if name == "show_riders")[0]


def test_on_search_text_given_a_matching_name_keeps_only_those_rows() -> None:
    """Search matches the rider's name, case-insensitively (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_search_text("ALEX")

    assert _searched_rows(view) == [RiderRow(plate="77", name="Alex Roy", team=None)]


def test_on_search_text_given_a_matching_plate_keeps_only_those_rows() -> None:
    """Search also matches the row's plate text (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_search_text("77")

    assert _searched_rows(view) == [RiderRow(plate="77", name="Alex Roy", team=None)]


def test_on_search_text_given_no_match_shows_an_empty_list() -> None:
    """A miss filters every row out; the editor does not crash (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_search_text("nobody-by-this-name")

    assert view.calls == [
        ("show_riders", ([],)),
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
    ]


def test_on_search_text_given_a_blank_text_restores_every_row() -> None:
    """Clearing the search restores the full roster (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_search_text("sam")
    view.calls.clear()

    presenter.on_search_text("   ")

    assert _searched_rows(view) == [
        RiderRow(plate="123", name="Sam Ellis", team=None),
        RiderRow(plate="2", name="Bo Lindqvist", team=None),
        RiderRow(plate="77", name="Alex Roy", team=None),
    ]


def test_on_row_selected_given_a_search_uses_the_filtered_row_order() -> None:
    """A visible row index maps to the filtered list, not the roster."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_search_text("indqvist")  # only Bo survives the filter
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("2", "Bo", "Lindqvist", SOLO_TEAM_CHOICE)) in view.calls


def test_on_sort_by_column_given_the_plate_column_sorts_numerically() -> None:
    """Plate sort is numeric-aware: 2, 77, 123 -- not lexicographic."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_sort_by_column(0)

    assert [row.plate for row in _searched_rows(view)] == ["2", "77", "123"]


def test_on_sort_by_column_given_a_second_click_toggles_descending() -> None:
    """Re-clicking the active column reverses the order (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_sort_by_column(0)
    view.calls.clear()

    presenter.on_sort_by_column(0)

    assert [row.plate for row in _searched_rows(view)] == ["123", "77", "2"]


def test_on_sort_by_column_given_the_name_column_sorts_casefolded() -> None:
    """Name sort is text order, case-insensitive (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_sort_by_column(1)

    assert [row.name for row in _searched_rows(view)] == [
        "Alex Roy",
        "Bo Lindqvist",
        "Sam Ellis",
    ]


def test_on_sort_by_column_given_the_team_column_groups_solos_first() -> None:
    """Team sort puts solo rows (no team) first, then team names."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Zebras",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    roster.create_team_entry(
        display_name="Alpha",
        riders=[
            Rider(first_name="Bo", last_name="Lindqvist", plate="2"),
            Rider(first_name="Cy", last_name="Nguyen", plate="3"),
        ],
    )
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_sort_by_column(2)

    assert _searched_rows(view) == [
        RiderRow(plate="123", name="Sam Ellis", team=None),
        RiderRow(plate="2", name="Bo Lindqvist", team="Alpha"),
        RiderRow(plate="3", name="Cy Nguyen", team="Alpha"),
        RiderRow(plate="77", name="A. Roy", team="Zebras"),
        RiderRow(plate="78", name="K. Singh", team="Zebras"),
    ]


def test_on_sort_by_column_given_relay_plates_sorts_digits_then_strings() -> None:
    """Non-numeric relay plates sort after every digit plate (W7)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    for plate in ("10", "2", "K1", "9"):
        roster.create_solo_entry(first_name=plate, last_name="Rider", plate=plate)
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_sort_by_column(0)

    assert [row.plate for row in _searched_rows(view)] == ["2", "9", "10", "K1"]


def test_on_row_selected_given_a_sort_uses_the_sorted_row_order() -> None:
    """After a plate sort, row 0 is the lowest plate's rider."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_sort_by_column(0)
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("2", "Bo", "Lindqvist", SOLO_TEAM_CHOICE)) in view.calls


def test_on_sort_by_column_given_a_search_applies_both_narrowings() -> None:
    """Search filters first; the active sort orders the survivors."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_sort_by_column(0)
    view.calls.clear()

    presenter.on_search_text("a")  # Sam and Alex carry an "a"; Bo does not

    assert [row.plate for row in _searched_rows(view)] == ["77", "123"]


def test_plate_order_key_given_mixed_plates_groups_digits_before_strings() -> None:
    """The pure key orders every digit plate before any string plate."""
    assert sorted(
        ["9", "K1", "2", "A", "10"],
        key=_plate_order_key,
    ) == ["2", "9", "10", "A", "K1"]


def test_pair_rows_given_a_pair_list_builds_rows_in_that_order() -> None:
    """_pair_rows renders exactly the pairs it is given, in order."""
    roster = _three_solo_roster()

    rows = _pair_rows(roster, list(reversed(_rider_pairs(roster))))

    assert [row.plate for row in rows] == ["77", "2", "123"]


def test_visible_pairs_given_no_filters_preserves_the_roster_order() -> None:
    """No search and no sort column keep the roster's own order."""
    roster = _three_solo_roster()

    visible = _visible_pairs(
        roster, _rider_pairs(roster), search_text="", column=None, ascending=True
    )

    assert [pair[1].plate for pair in visible] == ["123", "2", "77"]


# -------------------------------- W7 plate lock + change tracking


def test_riders_presenter_init_given_a_draft_ride_enables_the_plate_field() -> None:
    """DRAFT leaves plate_input editable (spec S3:46)."""
    view = RecordingRidersView()
    RidersPresenter(view, _draft_solo_roster())

    assert ("set_plate_enabled", (True,)) in view.calls


def test_riders_presenter_init_given_a_started_ride_disables_the_plate_field() -> None:
    """Once the ride has left DRAFT, plate_input is locked (W7)."""
    roster = _draft_solo_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRidersView()

    RidersPresenter(view, roster)

    assert ("set_plate_enabled", (False,)) in view.calls


def test_riders_presenter_roster_changed_starts_false() -> None:
    """A fresh presenter has nothing to persist yet (W7)."""
    presenter = RidersPresenter(RecordingRidersView(), _draft_solo_roster())

    assert presenter.roster_changed is False


def test_on_save_given_a_commit_marks_the_roster_changed() -> None:
    """A successful save flags this session's roster for persist."""
    presenter = RidersPresenter(RecordingRidersView(), _draft_solo_roster())
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert presenter.roster_changed is True


def test_on_save_given_a_refusal_leaves_the_roster_unchanged_flag_alone() -> None:
    """A refused save must not flag a persist (W7)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_save(
        RiderFormValues(plate="77", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert presenter.roster_changed is False


def test_on_delete_given_a_commit_marks_the_roster_changed() -> None:
    """A successful delete flags this session's roster for persist."""
    presenter = RidersPresenter(RecordingRidersView(), _draft_solo_roster())
    presenter.on_row_selected(0)

    presenter.on_delete()

    assert presenter.roster_changed is True


def test_on_add_committed_marks_the_roster_changed() -> None:
    """An Add-dialog commit flags this session's roster for persist."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    roster.create_solo_entry(first_name="New", last_name="Rider", plate="124")

    presenter.on_add_committed()

    assert presenter.roster_changed is True


# -------------------------------------------------- property test T-7


@given(
    team_names=st.lists(
        st.text(alphabet=string.ascii_letters, min_size=1, max_size=8),
        min_size=0,
        max_size=4,
        unique=True,
    )
)
def test_team_choices_given_n_teams_is_solo_then_every_team_in_order(
    team_names: list[str],
) -> None:
    """_team_choices is exactly [solo, *teams] -- no sentinel (T-7)."""
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

    assert choices == [SOLO_TEAM_CHOICE, *team_names]


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


def test_on_pick_csv_import_given_a_draft_under_min_team_warns_and_enables_import() -> None:
    """A DRAFT size-1 team is one warning, zero conflicts (R-21)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "team_under_min_pooled.csv")

    assert (
        "show_csv_preview",
        (
            CsvPreview(
                summary=(
                    "team_under_min_pooled.csv → 4 riders · 2 teams · 0 conflicts · 1 warnings"
                ),
                conflicts=(),
                warnings=(
                    CsvConflict(
                        row=5,
                        problem="team of 1 rider is below the minimum of 2 (team-under-min)",
                    ),
                ),
            ),
        ),
    ) in view.calls
    assert ("set_import_enabled", (True,)) in view.calls


def test_on_pick_csv_import_given_a_clean_file_carries_no_warnings() -> None:
    """A zero-warning file keeps the old summary and an empty tuple."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "clean_pooled.csv")

    preview = view.calls[0][1][0]
    assert preview.summary == "clean_pooled.csv → 9 riders · 2 teams · 0 conflicts"
    assert preview.conflicts == ()
    assert preview.warnings == ()


def test_on_pick_csv_import_given_an_unreadable_file_shows_validation_and_disables_import(
    tmp_path: Path,
) -> None:
    """A missing file surfaces through show_validation, never raises.

    The picker's must-exist check cannot catch a file that vanishes
    between the pick and the read (or one that is unreadable for
    another reason), so ``csvio.preview``'s ``OSError`` must reach the
    operator -- wx swallows an exception that escapes the presenter's
    caller (EPIC3-SESSION-SUMMARY.md's measured note), leaving the
    dialog open with nothing happening.
    """
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    missing = tmp_path / "missing.csv"

    presenter.on_pick_csv_import(missing)

    validation = next(args for name, args in view.calls if name == "show_validation")
    assert validation[0].startswith(f"Could not read {missing.name}: ")
    assert view.calls[-1] == ("set_import_enabled", (False,))
    assert presenter.on_confirm_csv_import() is False


def test_on_pick_csv_import_given_a_preview_value_error_shows_validation_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview ``ValueError`` is surfaced, never raised.

    ``csvio.preview`` reports content problems as conflicts, but a
    decode/parse failure that still escapes it raises ``ValueError``;
    the handler surfaces it through ``show_validation`` with Import
    disabled, keeping the dialog open (wx swallows an exception that
    escapes the presenter's caller -- EPIC3-SESSION-SUMMARY.md's
    measured note).
    """

    def _preview_that_raises(_path: object, _ride: object) -> object:
        raise ValueError("simulated parse failure")

    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    monkeypatch.setattr(csvio, "preview", _preview_that_raises)
    picked = tmp_path / "riders.csv"

    presenter.on_pick_csv_import(picked)

    validation = next(args for name, args in view.calls if name == "show_validation")
    assert validation[0] == f"Could not read {picked.name}: simulated parse failure"
    assert view.calls[-1] == ("set_import_enabled", (False,))
    assert presenter.on_confirm_csv_import() is False


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


def test_on_confirm_csv_import_given_warnings_only_commits_and_keeps_the_small_team() -> None:
    """A warnings-only preview commits; the size-1 team stays (R-21)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    presenter = RidersPresenter(RecordingRidersView(), roster, load=False)
    presenter.on_pick_csv_import(_FIXTURES / "team_under_min_pooled.csv")

    result = presenter.on_confirm_csv_import()

    assert result is True
    teams = [entry for entry in roster.entries if entry.type is EntryType.TEAM]
    assert [(entry.display_name, len(entry.riders)) for entry in teams] == [
        ("wolves", 2),
        ("solo team", 1),
    ]


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
