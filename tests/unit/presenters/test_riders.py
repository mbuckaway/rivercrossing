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

Joining an existing team calls ``Roster.add_rider_to_team`` directly,
so the rider attaches to the chosen team in place and a pooled ride's
RUNNING/REOPENED carve-out comes from ``can_move_rider``. The
"New team..." sentinel is retired on topic/ux-polish: team_choice
lists solo then every team, never a new-team entry, and this suite
pins that sentinel-less shape (``_team_choices`` below).

1.0.12 retires the editor's in-form Save entirely: the form is
display-only, so this suite pins the absence of the whole save path
(``RidersView`` members and ``RidersPresenter`` methods) and, in its
place, the two dialog presenters over add_rider_dlg --
``AddRiderPresenter``'s create path as before, and the new
``EditRiderPresenter``'s preload/write-back, which runs the shared
update mutators (team, plate, names) rather than the create
primitives. The delete confirm (``RidersView.confirm``), the
presenter's own row-index guard, the ``selected`` read the view opens
the Edit dialog from, and ``on_edit_committed``'s re-render are pinned
here too.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing import csvio
from rivercrossing.ride import RideStatus
from rivercrossing.roster import (
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
    TeamSizeError,
)
from rivercrossing.ui import rider_columns
from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.presenters.riders import (
    SOLO_TEAM_CHOICE,
    AddRiderPresenter,
    CsvConflict,
    CsvPreview,
    EditRiderPresenter,
    RiderFormValues,
    RidersPresenter,
    RidersView,
    _apply_team_change,
    _pair_rows,
    _rider_pairs,
    _rider_rows,
    _team_choices,
    _visible_pairs,
)
from rivercrossing.ui.rider_columns import plate_order_key

# tests/unit/fixtures/csv/ is test_csvio.py's own fixture home (its
# module docstring); reused here rather than re-derived, per E3.4's
# own brief.
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "csv"

# ------------------------------------------------------------- fixtures


class RecordingRidersView:
    """A complete ``RidersView`` spy recording each call, in order.

    1.0.12 (B1): the editor's form is display-only, so this view
    carries no save gating at all -- neither ``set_save_enabled`` nor
    ``set_plate_enabled`` is a ``RidersView`` member any more. The
    delete confirm (B3) is canned ``True`` unless a test flips
    :attr:`confirm_result`, so every refusal test still reaches the
    roster's own gate.
    """

    def __init__(self) -> None:
        """Start with an empty call log and an accepted confirm."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.confirm_result = True

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Record the rendered riders_list rows."""
        self.calls.append(("show_riders", (rows,)))

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Record delete_btn's enabled state."""
        self.calls.append(("set_delete_enabled", (enabled,)))

    def confirm(  # noqa: PLR0913 -- test spy mirrors the view's confirm contract
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
    ) -> bool:
        """Record the confirm and return its canned verdict."""
        self.calls.append(("confirm", (title, message, ok_label, cancel_label)))
        return self.confirm_result

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
    """Construction renders rows, the form, and the delete gate."""
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
        ("show_form", ("125", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
    ]


# -------------------------------------------------- AddRiderPresenter


class RecordingAddRiderView:
    """A complete ``AddRiderView`` spy recording each call, in order.

    add_rider_dlg and its Edit Rider… mode share this one surface
    (1.0.12 B2): the same window, one protocol. ``show_form`` fills
    all five fields -- the edit mode preloads the record's names and
    sex, the add mode passes the names blank and the sex unset.
    """

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_team_choices(self, names: list[str]) -> None:
        """Record the rendered team_choice content."""
        self.calls.append(("show_team_choices", (names,)))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the Team row's visibility."""
        self.calls.append(("set_team_ui_visible", (visible,)))

    def set_plate_enabled(self, *, enabled: bool) -> None:
        """Record plate_input's enabled state (1.0.12 plate lock)."""
        self.calls.append(("set_plate_enabled", (enabled,)))

    def show_form(  # noqa: PLR0913 -- test spy mirrors the view's five-field contract
        self,
        *,
        plate: str,
        first_name: str,
        last_name: str,
        team: str,
        sex: str | None,
    ) -> None:
        """Record the prefilled fields, sex included (Phase 3)."""
        self.calls.append(("show_form", (plate, first_name, last_name, team, sex)))

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.calls.append(("show_validation", (message,)))


def test_add_rider_presenter_init_given_a_mixed_roster_renders_the_choices_and_form() -> None:
    """Construction renders choices, Team UI and the locked form."""
    view = RecordingAddRiderView()
    roster = _draft_mixed_roster()

    AddRiderPresenter(view, roster)

    assert view.calls == [
        ("show_team_choices", ([SOLO_TEAM_CHOICE, "Trail Blazers"],)),
        ("set_team_ui_visible", (True,)),
        ("set_plate_enabled", (True,)),
        ("show_form", ("79", "", "", SOLO_TEAM_CHOICE, None)),
    ]


def test_add_rider_presenter_init_given_a_solo_only_ride_hides_the_team_row() -> None:
    """R-11: a solo-only ride shows no Team choice in the dialog."""
    view = RecordingAddRiderView()

    AddRiderPresenter(view, Roster())

    assert view.calls == [
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
        ("set_team_ui_visible", (False,)),
        ("set_plate_enabled", (True,)),
        ("show_form", ("1", "", "", SOLO_TEAM_CHOICE, None)),
    ]


def test_add_rider_presenter_init_given_a_blank_form_leaves_the_sex_unset() -> None:
    """Phase 3: the Add dialog opens on the blank Sex item."""
    view = RecordingAddRiderView()

    AddRiderPresenter(view, Roster())

    assert ("show_form", ("1", "", "", SOLO_TEAM_CHOICE, None)) in view.calls


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


# ------------------------------------------- the Sex field (Phase 3)
#
# Rider.sex is "M"/"F" or None for unknown. The dialog's blank choice
# is read as None by the view before it ever reaches the presenter
# (views/rider_editor.sex_from_choice), so these rows are what the
# presenter actually receives.


@pytest.mark.parametrize(
    ("sex", "expected"),
    [("M", "M"), ("F", "F"), (None, None)],
    ids=["male", "female", "unknown"],
)
def test_add_rider_presenter_submit_given_a_sex_persists_it_on_the_rider(
    sex: str | None,
    expected: str | None,
) -> None:
    """T-4 nullable: M, F and unknown all land on the rider."""
    roster = Roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    created = presenter.on_submit(
        RiderFormValues(
            plate="1", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE, sex=sex
        )
    )

    assert created is True
    assert roster.entries[0].riders[0].sex == expected


def test_add_rider_presenter_submit_given_a_sex_and_a_team_join_persists_it() -> None:
    """The join path carries the sex onto the folded-in rider too."""
    roster = _draft_mixed_roster()
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    presenter.on_submit(
        RiderFormValues(
            plate="79",
            first_name="L.",
            last_name="Marchetti",
            team="Trail Blazers",
            sex="F",
        )
    )

    assert roster.entries[0].riders[-1].sex == "F"


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
    assert view.calls == [("show_validation", ("team size must be at most 2, got 3",))]
    assert [e.display_name for e in roster.entries] == ["Trail Blazers"]


def test_add_rider_presenter_submit_given_a_started_pooled_ride_joins_the_existing_team() -> None:
    """A rider joins an existing team once the ride starts."""
    roster = _draft_mixed_roster()
    roster.status = RideStatus.RUNNING
    presenter = AddRiderPresenter(RecordingAddRiderView(), roster)

    created = presenter.on_submit(
        RiderFormValues(plate="79", first_name="L.", last_name="Marchetti", team="Trail Blazers")
    )

    assert created is True
    assert [r.full_name for r in roster.entries[0].riders] == [
        "A. Roy",
        "K. Singh",
        "L. Marchetti",
    ]


# ----------------------------------------------- EditRiderPresenter
#
# 1.0.12 (B1) retires the editor's in-form Save. add_rider_dlg opens
# in an "Edit Rider…" mode instead, and its own EditRiderPresenter
# writes the form back through the shared update mutators -- never
# the Add dialog's create path.


def _edit_presenter(
    roster: Roster, *, entry_index: int = 0, rider_index: int = 0
) -> tuple[EditRiderPresenter, RecordingAddRiderView]:
    """Return an edit-dialog presenter over *roster*'s record."""
    entry = roster.entries[entry_index]
    view = RecordingAddRiderView()
    presenter = EditRiderPresenter(view, roster, entry=entry, rider=entry.riders[rider_index])
    view.calls.clear()
    return presenter, view


def test_edit_rider_presenter_init_given_a_solo_record_preloads_the_form() -> None:
    """Opening Edit Rider… pre-fills every field from the record."""
    roster = _draft_solo_roster()
    view = RecordingAddRiderView()

    EditRiderPresenter(view, roster, entry=roster.entries[0], rider=roster.entries[0].riders[0])

    assert view.calls == [
        ("show_team_choices", ([SOLO_TEAM_CHOICE],)),
        ("set_team_ui_visible", (False,)),
        ("set_plate_enabled", (True,)),
        ("show_form", ("123", "Sam", "Ellis", SOLO_TEAM_CHOICE, None)),
    ]


def test_edit_rider_presenter_init_given_a_sexed_rider_preloads_the_sex() -> None:
    """Phase 3: the Edit dialog opens on the record's own sex."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123", sex="F")
    view = RecordingAddRiderView()

    EditRiderPresenter(view, roster, entry=roster.entries[0], rider=roster.entries[0].riders[0])

    assert ("show_form", ("123", "Sam", "Ellis", SOLO_TEAM_CHOICE, "F")) in view.calls


def test_edit_rider_presenter_init_given_a_relay_member_preloads_the_entry_plate() -> None:
    """A relay member's plate is the entry's; the preload shows it."""
    roster = _draft_relay_roster()
    view = RecordingAddRiderView()

    EditRiderPresenter(view, roster, entry=roster.entries[0], rider=roster.entries[0].riders[0])

    assert ("show_form", ("77", "A.", "Roy", "Trail Blazers", None)) in view.calls


def test_edit_rider_presenter_init_given_a_pooled_member_preloads_their_own_plate() -> None:
    """A pooled member's own plate is what the form shows (R-20)."""
    roster = _draft_mixed_roster()
    view = RecordingAddRiderView()

    EditRiderPresenter(view, roster, entry=roster.entries[0], rider=roster.entries[0].riders[0])

    assert ("show_form", ("77", "A.", "Roy", "Trail Blazers", None)) in view.calls


def test_edit_rider_presenter_init_given_a_started_ride_locks_the_plate_field() -> None:
    """The edit dialog reproduces W7's plate lock (B2)."""
    roster = _draft_solo_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingAddRiderView()

    EditRiderPresenter(view, roster, entry=roster.entries[0], rider=roster.entries[0].riders[0])

    assert ("set_plate_enabled", (False,)) in view.calls


def test_edit_rider_presenter_submit_given_a_solo_record_renames_the_rider_and_entry() -> None:
    """A committed edit renames the rider and its solo entry (R-20)."""
    roster = _draft_solo_roster()
    presenter, _view = _edit_presenter(roster)

    committed = presenter.on_submit(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert committed is True
    assert (entry.display_name, entry.riders[0].full_name) == ("Samuel Ellis", "Samuel Ellis")


def test_edit_rider_presenter_submit_given_a_team_member_renames_only_the_rider() -> None:
    """Editing a team member renames the rider, never the team (B2)."""
    roster = _draft_mixed_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="Alex", last_name="Roy", team="Trail Blazers")
    )

    entry = roster.entries[0]
    assert (entry.display_name, entry.riders[0].full_name) == ("Trail Blazers", "Alex Roy")


def test_edit_rider_presenter_submit_given_a_chosen_sex_persists_it() -> None:
    """Phase 3: a committed edit stores the form's sex on the rider."""
    roster = _draft_solo_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(
            plate="123", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE, sex="M"
        )
    )

    assert roster.entries[0].riders[0].sex == "M"


def test_edit_rider_presenter_submit_given_a_cleared_sex_unsets_it() -> None:
    """Clearing the dropdown stores None (unknown), never "" (T-4)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123", sex="F")
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(
            plate="123", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE, sex=None
        )
    )

    assert roster.entries[0].riders[0].sex is None


def test_edit_rider_presenter_submit_given_a_sex_and_a_team_move_keeps_both() -> None:
    """The team move and the sex write-back both apply (B2)."""
    roster = _solo_and_team_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(
            plate="123",
            first_name="Sam",
            last_name="Ellis",
            team="Trail Blazers",
            sex="F",
        )
    )

    team = roster.entries[0]
    assert team.riders[-1].sex == "F"


def test_edit_rider_presenter_submit_given_a_new_plate_changes_the_solo_entry() -> None:
    """A solo plate edit lands on the entry and its rider (R-20)."""
    roster = _draft_solo_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    entry = roster.entries[0]
    assert (entry.plate, entry.riders[0].plate) == ("200", "200")


def test_edit_rider_presenter_submit_given_a_duplicate_plate_shows_validation_not_crash() -> None:
    """A colliding plate refuses via show_validation (E3.2)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("plate '77' is already in use",))]


def test_edit_rider_presenter_submit_given_a_duplicate_plate_leaves_it_unchanged() -> None:
    """A refused plate edit leaves the entry's plate as it was."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert roster.entries[0].plate == "123"


def test_edit_rider_presenter_submit_given_a_post_start_plate_edit_shows_validation() -> None:
    """A plate edit after the start refuses via show_validation."""
    roster = _draft_solo_roster()
    presenter, view = _edit_presenter(roster)
    roster.status = RideStatus.RUNNING

    presenter.on_submit(
        RiderFormValues(plate="200", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        ("show_validation", ("plates cannot be changed once the ride is running",))
    ]


def test_edit_rider_presenter_submit_given_a_refusal_leaves_the_name_unchanged_too() -> None:
    """A refused edit is atomic: the name also stays as it was."""
    roster = _draft_solo_roster()
    presenter, _view = _edit_presenter(roster)
    roster.status = RideStatus.RUNNING

    presenter.on_submit(
        RiderFormValues(plate="200", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert roster.entries[0].riders[0].full_name == "Sam Ellis"


def test_edit_rider_presenter_submit_given_a_pooled_member_changes_their_own_plate() -> None:
    """A pooled member's plate edit lands on their own plate (R-20)."""
    roster = _draft_mixed_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="90", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert [r.plate for r in roster.entries[0].riders] == ["90", "78"]


def test_edit_rider_presenter_submit_given_a_pooled_member_recomputes_the_team_plate() -> None:
    """Editing the lowest-plate member recomputes the team's plate."""
    roster = _draft_mixed_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="90", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert roster.entries[0].plate == "78"


def test_edit_rider_presenter_submit_given_a_duplicate_pooled_plate_shows_validation() -> None:
    """A colliding plate on a pooled member also refuses (E3.2)."""
    roster = _draft_mixed_roster()
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="78", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '78' is already in use",))]


def test_edit_rider_presenter_submit_given_a_relay_member_changes_the_team_plate() -> None:
    """A relay member's plate edit updates the team's shared plate."""
    roster = _draft_relay_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="99", first_name="Alex", last_name="Roy", team="Trail Blazers")
    )

    entry = roster.entries[0]
    assert (entry.plate, [r.plate for r in entry.riders]) == ("99", [None, None])


def test_edit_rider_presenter_submit_given_a_duplicate_relay_plate_shows_validation() -> None:
    """A colliding plate on a relay team also refuses (E3.2)."""
    roster = _draft_relay_roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '123' is already in use",))]


def test_edit_rider_presenter_submit_given_a_duplicate_relay_plate_leaves_it_unchanged() -> None:
    """A refused relay plate edit leaves the plate as it was."""
    roster = _draft_relay_roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert roster.entries[0].plate == "77"


def test_edit_rider_presenter_submit_given_a_post_start_relay_plate_shows_validation() -> None:
    """A relay plate edit after the start refuses via validation."""
    roster = _draft_relay_roster()
    presenter, view = _edit_presenter(roster)
    roster.status = RideStatus.RUNNING

    presenter.on_submit(
        RiderFormValues(plate="99", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [
        ("show_validation", ("plates cannot be changed once the ride is running",))
    ]


def test_edit_rider_presenter_submit_given_the_records_own_values_is_a_clean_no_op() -> None:
    """Resubmitting a record unchanged commits True and touches nothing.

    The dialog's own ShowModal already ran; the presenter makes no
    further view call on a commit -- the editor re-renders afterwards
    through RidersPresenter.on_edit_committed.
    """
    roster = _draft_relay_roster()
    presenter, view = _edit_presenter(roster)

    committed = presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert committed is True
    assert view.calls == []


def test_edit_rider_presenter_submit_given_a_renamed_rider_leaves_the_view_silent() -> None:
    """A committed edit makes no further view call of its own (B2)."""
    roster = _draft_solo_roster()
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == []


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
def test_edit_rider_presenter_submit_given_a_blank_name_refuses(
    first_name: str,
    last_name: str,
) -> None:
    """An edit with a blank first or last name refuses up front (B2)."""
    view = RecordingAddRiderView()
    roster = _draft_solo_roster()
    entry = roster.entries[0]
    presenter = EditRiderPresenter(view, roster, entry=entry, rider=entry.riders[0])
    view.calls.clear()

    committed = presenter.on_submit(
        RiderFormValues(
            plate="123", first_name=first_name, last_name=last_name, team=SOLO_TEAM_CHOICE
        )
    )

    assert committed is False
    assert view.calls == [("show_validation", ("First name and last name are required",))]


def test_edit_rider_presenter_submit_given_a_blank_name_leaves_the_roster_unchanged() -> None:
    """A refused edit mutates nothing (a state, not a call, check)."""
    roster = _draft_solo_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="", last_name="", team=SOLO_TEAM_CHOICE)
    )

    assert [entry.riders[0].full_name for entry in roster.entries] == ["Sam Ellis"]


def test_edit_rider_presenter_submit_given_a_removed_entry_shows_validation_not_crash() -> None:
    """A stale record (entry removed meanwhile) refuses cleanly."""
    roster = _draft_solo_roster()
    entry = roster.entries[0]
    view = RecordingAddRiderView()
    presenter = EditRiderPresenter(view, roster, entry=entry, rider=entry.riders[0])
    roster.delete_entry(entry)
    view.calls.clear()

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("entry is not a member of this roster",))]


# ----------------------------------------------------- on_delete


def test_on_delete_given_nothing_selected_is_a_no_op() -> None:
    """Delete with no prior selection makes no view call at all.

    Nothing selected means nothing to confirm either -- the confirm
    dialog must not appear over an empty form (B3).
    """
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == []


def test_on_delete_given_a_draft_selection_asks_a_confirm_naming_the_rider() -> None:
    """The destructive confirm names the rider it would remove (B3)."""
    view = RecordingRidersView()
    view.confirm_result = False
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == [
        ("confirm", ("Delete rider?", 'Delete "Sam Ellis" from this ride?', "Delete", "Cancel"))
    ]


def test_on_delete_given_a_team_member_asks_a_confirm_naming_the_rider() -> None:
    """A team-member row confirms the rider, never the team."""
    view = RecordingRidersView()
    view.confirm_result = False
    presenter = RidersPresenter(view, _draft_mixed_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert view.calls == [
        ("confirm", ("Delete rider?", 'Delete "A. Roy" from this ride?', "Delete", "Cancel"))
    ]


def test_on_delete_given_a_team_member_removes_only_that_rider() -> None:
    """OK on a team-member row removes only that rider."""
    roster = _draft_mixed_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)

    presenter.on_delete()

    assert [
        (entry.display_name, [rider.full_name for rider in entry.riders])
        for entry in roster.entries
    ] == [("Trail Blazers", ["K. Singh"])]


def test_on_delete_given_a_declined_confirm_is_a_no_op() -> None:
    """Cancel on the confirm leaves the roster untouched (B3)."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    view.confirm_result = False
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)

    presenter.on_delete()

    assert [entry.display_name for entry in roster.entries] == ["Sam Ellis"]


def test_on_delete_given_an_accepted_confirm_deletes_the_entry() -> None:
    """Delete on a DRAFT entry with no data removes it (R-15)."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)

    presenter.on_delete()

    assert view.confirm_result is True
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

    assert view.calls[-1] == (
        "show_validation",
        ("entry has recorded data; DNF or void it instead of deleting",),
    )


def test_on_delete_given_a_post_start_ride_shows_a_status_message() -> None:
    """A refusal from a started ride names the current status (R-15)."""
    roster = _draft_solo_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert view.calls[-1] == (
        "show_validation",
        ("entries can no longer be deleted once the ride is running",),
    )


def test_on_delete_prefills_the_next_free_plate_after_deleting() -> None:
    """After deleting the only entry, the form prefills plate 1."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_delete()

    assert ("show_form", ("1", "", "", SOLO_TEAM_CHOICE)) in view.calls


# ------------------------- read-only form + selection guard (B1/B3)


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


def test_riders_view_protocol_carries_no_save_gating_member() -> None:
    """The editor's form is display-only: no save gate left (B1)."""
    assert {"set_save_enabled", "set_plate_enabled"}.isdisjoint(RidersView.__dict__)


def test_riders_presenter_carries_no_in_place_save_path() -> None:
    """The in-form Save is retired; the Edit dialog owns the write."""
    assert {"on_save", "on_form_changed", "_is_dirty"}.isdisjoint(RidersPresenter.__dict__)


def test_riders_presenter_selected_given_no_selection_is_none() -> None:
    """Nothing selected means nothing for edit_btn to act on (B3)."""
    presenter = RidersPresenter(RecordingRidersView(), _draft_solo_roster())

    assert presenter.selected is None


def test_riders_presenter_selected_given_a_row_selected_returns_its_record() -> None:
    """The view reads the selection to open the Edit dialog (B3)."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)

    presenter.on_row_selected(0)

    assert presenter.selected == (roster.entries[0], roster.entries[0].riders[0])


@pytest.mark.parametrize("index", [-1, 1, 2, 99], ids=["neg", "len", "len_plus_1", "far"])
def test_on_row_selected_given_an_out_of_range_index_is_a_no_op(index: int) -> None:
    """A stale event past the visible rows fills nothing (B3, T-4)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_row_selected(index)

    assert view.calls == []


def test_on_row_selected_given_the_last_visible_row_fills_its_form() -> None:
    """The in-range boundary still selects (T-4: len - 1)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _draft_solo_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("123", "Sam", "Ellis", SOLO_TEAM_CHOICE)) in view.calls


# ----------------------------------------------- on_edit_committed


def test_on_edit_committed_refreshes_and_re_shows_the_record() -> None:
    """A committed edit re-renders the rows and re-shows the record."""
    roster = _draft_solo_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    entry = roster.entries[0]
    rider = entry.riders[0]
    EditRiderPresenter(RecordingAddRiderView(), roster, entry=entry, rider=rider).on_submit(
        RiderFormValues(plate="123", first_name="Samuel", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )
    view.calls.clear()

    presenter.on_edit_committed(rider)

    assert view.calls == [
        ("show_riders", ([RiderRow(plate="123", name="Samuel Ellis", team=None)],)),
        ("show_form", ("123", "Samuel", "Ellis", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (True,)),
    ]


def test_on_edit_committed_given_a_team_move_re_shows_the_new_entry() -> None:
    """After a team move the form shows the rider's new entry (B3)."""
    roster = _solo_and_team_roster()
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    presenter.on_row_selected(0)
    rider = roster.entries[0].riders[0]
    EditRiderPresenter(
        RecordingAddRiderView(), roster, entry=roster.entries[0], rider=rider
    ).on_submit(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )
    view.calls.clear()

    presenter.on_edit_committed(rider)

    assert ("show_form", ("123", "Sam", "Ellis", "Trail Blazers")) in view.calls


def test_on_edit_committed_marks_the_roster_changed() -> None:
    """An edit-dialog commit flags this session's roster for persist."""
    roster = _draft_solo_roster()
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)

    presenter.on_edit_committed(roster.entries[0].riders[0])

    assert presenter.roster_changed is True


def test_on_edit_committed_given_a_vanished_rider_resets_to_the_add_form() -> None:
    """A rider deleted meanwhile cannot be re-shown; reset instead."""
    roster = _draft_solo_roster()
    rider = roster.entries[0].riders[0]
    roster.delete_entry(roster.entries[0])
    view = RecordingRidersView()
    presenter = RidersPresenter(view, roster)
    view.calls.clear()

    presenter.on_edit_committed(rider)

    assert view.calls == [
        ("show_riders", ([],)),
        ("show_form", ("1", "", "", SOLO_TEAM_CHOICE)),
        ("set_delete_enabled", (False,)),
    ]


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


# ------------------------------------ team assignment on edit (B2)


def test_edit_rider_presenter_submit_given_a_solo_record_joins_the_chosen_team() -> None:
    """A team chosen folds the solo rider onto that team (B2)."""
    roster = _solo_and_team_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [entry.type for entry in roster.entries] == [EntryType.TEAM]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "Sam Ellis"]
    assert team.riders[-1].plate == "123"
    assert team.plate == "77"


def test_edit_rider_presenter_submit_given_a_full_destination_team_refuses() -> None:
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
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("team size must be at most 2, got 3",))]
    assert [e.type for e in roster.entries] == [EntryType.SOLO, EntryType.TEAM]


def test_edit_rider_presenter_submit_given_a_post_start_join_shows_validation() -> None:
    """A post-start solo join refuses via the roster's delete gate."""
    roster = _solo_and_team_roster()
    presenter, view = _edit_presenter(roster)
    roster.status = RideStatus.RUNNING

    presenter.on_submit(
        RiderFormValues(plate="123", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    assert view.calls == [
        (
            "show_validation",
            ("entries can no longer be deleted once the ride is running",),
        )
    ]
    assert roster.entries[0].type is EntryType.SOLO


def test_edit_rider_presenter_submit_given_a_relay_join_ignores_the_form_plate() -> None:
    """A relay join frees the solo plate; the team plate never moves."""
    roster = _relay_solo_and_team_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="9", first_name="Sam", last_name="Ellis", team="Trail Blazers")
    )

    team = roster.entries[0]
    assert [e.type for e in roster.entries] == [EntryType.TEAM]
    assert [r.full_name for r in team.riders] == ["A. Roy", "K. Singh", "Sam Ellis"]
    assert [r.plate for r in team.riders] == [None, None, None]
    assert team.plate == "77"


def test_edit_rider_presenter_submit_given_a_team_member_moves_between_teams() -> None:
    """A team chosen moves the rider between teams (R-17)."""
    roster = _two_team_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team="Moss Ridge")
    )

    trail, moss = roster.entries
    assert [r.full_name for r in trail.riders] == ["K. Singh"]
    assert [r.full_name for r in moss.riders] == ["Bo Lindqvist", "Cy Nguyen", "A. Roy"]
    assert (trail.plate, moss.plate) == ("78", "77")


def test_edit_rider_presenter_submit_given_a_pooled_member_leaves_to_solo() -> None:
    """The solo sentinel extracts a pooled member to solo (R-17)."""
    roster = _draft_mixed_roster()
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    team, solo = roster.entries
    assert [r.full_name for r in team.riders] == ["K. Singh"]
    assert team.plate == "78"
    assert (solo.display_name, solo.plate, solo.riders[0].plate) == ("A. Roy", "77", "77")


def test_edit_rider_presenter_submit_given_a_relay_member_leaving_shows_validation() -> None:
    """A relay member has no solo conversion; the roster refuses."""
    roster = _draft_relay_roster()
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        (
            "show_validation",
            ("extract_rider_to_solo requires a rider_pooled team member",),
        )
    ]
    assert len(roster.entries[0].riders) == 2


def test_edit_rider_presenter_submit_given_a_post_start_leave_shows_validation() -> None:
    """extract_rider_to_solo is DRAFT-only; a running ride refuses."""
    roster = _draft_mixed_roster()
    presenter, view = _edit_presenter(roster)
    roster.status = RideStatus.RUNNING

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [
        (
            "show_validation",
            ("a rider cannot be extracted to solo once the ride is running",),
        )
    ]
    assert len(roster.entries[0].riders) == 2


def test_edit_rider_presenter_submit_given_a_relay_move_ignores_the_form_plate() -> None:
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
    presenter, _view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="77", first_name="A.", last_name="Roy", team="Moss Ridge")
    )

    trail, moss = roster.entries
    assert [r.full_name for r in trail.riders] == ["K. Singh"]
    assert "A. Roy" in [r.full_name for r in moss.riders]
    assert (trail.plate, moss.plate) == ("77", "80")


def test_edit_rider_presenter_submit_given_a_blank_relay_plate_shows_validation() -> None:
    """A blanked relay plate refuses via the non-empty guard (W7)."""
    roster = _draft_relay_roster()
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="   ", first_name="A.", last_name="Roy", team="Trail Blazers")
    )

    assert view.calls == [("show_validation", ("plate '   ' must not be empty",))]
    assert roster.entries[0].plate == "77"


def test_edit_rider_presenter_submit_given_a_blank_solo_plate_shows_validation() -> None:
    """A blanked solo plate refuses too (both plate models) (W7)."""
    roster = _draft_solo_roster()
    presenter, view = _edit_presenter(roster)

    presenter.on_submit(
        RiderFormValues(plate="", first_name="Sam", last_name="Ellis", team=SOLO_TEAM_CHOICE)
    )

    assert view.calls == [("show_validation", ("plate '' must not be empty",))]
    assert roster.entries[0].plate == "123"


def test_apply_team_change_given_the_records_own_team_moves_nothing() -> None:
    """Choosing the team a rider is already on is a silent no-op (B2).

    The shared mutator's own guard: the dialog only reaches it when
    the chosen team differs, so this direction is pinned directly.
    """
    roster = _draft_mixed_roster()
    entry = roster.entries[0]

    result = _apply_team_change(roster, entry, entry.riders[0], "Trail Blazers")

    assert result is entry


def test_apply_team_change_given_solo_choosing_solo_moves_nothing() -> None:
    """Choosing solo for a solo rider is a silent no-op (B2)."""
    roster = _draft_solo_roster()
    entry = roster.entries[0]

    result = _apply_team_change(roster, entry, entry.riders[0], SOLO_TEAM_CHOICE)

    assert result is entry


def test_apply_team_change_given_a_full_destination_team_raises() -> None:
    """T-5: the mutator's own raise, pinned by type and text (R-12)."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=2)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    entry = roster.entries[0]

    with pytest.raises(TeamSizeError, match=re.escape("team size must be at most 2, got 3")):
        _apply_team_change(roster, entry, entry.riders[0], "Trail Blazers")


# --------------------------- W7 search + sort (riders_list narrowing)


def _three_solo_roster() -> Roster:
    """Return three DRAFT solos in a shuffled plate order."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Bo", last_name="Lindqvist", plate="2")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    return roster


def _search_roster() -> Roster:
    """Return two DRAFT solos plus one two-rider team (mixed mode)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Bo", last_name="Lindqvist", plate="2")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
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


@pytest.mark.parametrize(
    ("needle", "expected_plates"),
    [
        ("Sam", ["123"]),  # first name
        ("LINDQVIST", ["2"]),  # last name, case-insensitive
        ("77", ["77"]),  # plate number
        ("blazers", ["77", "78"]),  # team name, case-insensitive
        ("solo", ["123", "2"]),  # the Team cell's literal solo text
        ("   ", ["123", "2", "77", "78"]),  # blank search keeps every row
    ],
)
def test_on_search_text_given_a_needle_keeps_the_matching_rows(
    *, needle: str, expected_plates: list[str]
) -> None:
    """Search matches name, plate or Team cell; blank keeps all (W7)."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _search_roster())
    view.calls.clear()

    presenter.on_search_text(needle)

    assert [row.plate for row in _searched_rows(view)] == expected_plates


def test_on_search_text_given_a_team_name_keeps_the_roster_order() -> None:
    """The presenter filters only; the list owns row order (W7).

    The rows arrive in the roster's own order whatever the operator's
    chosen header arrow says; the native control re-orders what it
    displays from the model's ``Compare`` (Phase C).
    """
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _search_roster())
    view.calls.clear()

    presenter.on_search_text("blazers")

    assert [row.plate for row in _searched_rows(view)] == ["77", "78"]


def test_on_row_selected_given_a_search_uses_the_filtered_row_order() -> None:
    """A visible row index maps to the filtered list, not the roster."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_search_text("indqvist")  # only Bo survives the filter
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("2", "Bo", "Lindqvist", SOLO_TEAM_CHOICE)) in view.calls


def test_on_search_text_given_a_solo_needle_keeps_the_roster_order() -> None:
    """Filtering never re-orders: survivors keep roster order."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    view.calls.clear()

    presenter.on_search_text("a")  # Sam and Alex carry an "a"; Bo does not

    assert [row.plate for row in _searched_rows(view)] == ["123", "77"]


def test_on_row_selected_given_no_sort_uses_the_filtered_row_order() -> None:
    """A filtered row index maps through the visible list."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, _three_solo_roster())
    presenter.on_search_text("alex")
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("show_form", ("77", "Alex", "Roy", SOLO_TEAM_CHOICE)) in view.calls


def test_riders_presenter_carries_no_sort_state() -> None:
    """The list's own native sort owns row order now (Phase C)."""
    assert {"on_sort_by_column", "_sort_column", "_sort_ascending"}.isdisjoint(
        RidersPresenter.__dict__
    )


def test_riders_view_protocol_carries_no_sort_indicator_member() -> None:
    """The ▲/▼ marker is retired; the platform draws the arrow."""
    assert "set_sort_indicator" not in RidersView.__dict__


def test_rider_columns_carries_no_click_to_sort_rule() -> None:
    """The presenter no longer toggles direction; wx's arrows own it."""
    assert not hasattr(rider_columns, "toggle_sort")


def test_plate_order_key_given_mixed_plates_groups_digits_before_strings() -> None:
    """The shared key orders every digit plate before strings."""
    assert sorted(
        ["9", "K1", "2", "A", "10"],
        key=plate_order_key,
    ) == ["2", "9", "10", "A", "K1"]


def test_pair_rows_given_a_pair_list_builds_rows_in_that_order() -> None:
    """_pair_rows renders exactly the pairs it is given, in order."""
    roster = _three_solo_roster()

    rows = _pair_rows(roster, list(reversed(_rider_pairs(roster))))

    assert [row.plate for row in rows] == ["77", "2", "123"]


def test_pair_rows_given_a_riders_own_fields_carries_sex_into_the_row() -> None:
    """Phase 3: the Sex column reads the rider's own sex (T-4)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123", sex="F")
    roster.create_solo_entry(first_name="Bo", last_name="Roy", plate="2")

    rows = _pair_rows(roster, _rider_pairs(roster))

    assert rows == [
        RiderRow(plate="123", name="Sam Ellis", team=None, sex="F"),
        RiderRow(plate="2", name="Bo Roy", team=None),
    ]


def test_visible_pairs_given_no_search_preserves_the_roster_order() -> None:
    """No search keeps the roster order; the list sorts natively."""
    roster = _three_solo_roster()

    visible = _visible_pairs(roster, _rider_pairs(roster), search_text="")

    assert [pair[1].plate for pair in visible] == ["123", "2", "77"]


def test_visible_pairs_given_a_search_keeps_only_the_matching_pairs() -> None:
    """The one narrowing left in the presenter: the search filter."""
    roster = _three_solo_roster()

    visible = _visible_pairs(roster, _rider_pairs(roster), search_text="sam")

    assert [pair[1].plate for pair in visible] == ["123"]


# ------------------------- the dialogs' plate lock + change tracking


def test_add_rider_presenter_init_given_a_started_ride_locks_the_plate_field() -> None:
    """Once the ride leaves DRAFT, the Add dialog's plate locks."""
    roster = _draft_solo_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingAddRiderView()

    AddRiderPresenter(view, roster)

    assert ("set_plate_enabled", (False,)) in view.calls


def test_add_rider_presenter_init_given_a_draft_ride_keeps_the_plate_editable() -> None:
    """DRAFT leaves the Add dialog's plate editable (spec S3:46)."""
    view = RecordingAddRiderView()

    AddRiderPresenter(view, _draft_solo_roster())

    assert ("set_plate_enabled", (True,)) in view.calls


def test_riders_presenter_roster_changed_starts_false() -> None:
    """A fresh presenter has nothing to persist yet (W7)."""
    presenter = RidersPresenter(RecordingRidersView(), _draft_solo_roster())

    assert presenter.roster_changed is False


def test_edit_rider_presenter_submit_given_a_refusal_leaves_the_editors_flag_alone() -> None:
    """A refused edit flags no persist; the editor never hears."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_solo_entry(first_name="Alex", last_name="Roy", plate="77")
    presenter = RidersPresenter(RecordingRidersView(), roster)
    presenter.on_row_selected(0)
    entry = roster.entries[0]

    EditRiderPresenter(
        RecordingAddRiderView(), roster, entry=entry, rider=entry.riders[0]
    ).on_submit(
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
    plates=st.lists(
        st.text(alphabet=string.digits, min_size=1, max_size=6),
        min_size=1,
        max_size=8,
    )
)
def test_plate_order_key_given_digit_plates_matches_integer_order(
    plates: list[str],
) -> None:
    """The numeric-aware key is monotonic in the integer value (T-7)."""
    ordered = sorted(set(plates), key=plate_order_key)

    assert ordered == sorted(set(plates), key=int)


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

    Its view never implements show_riders/set_team_ui_visible/
    show_form/set_delete_enabled for real (E3.4's own
    NotImplementedError stubs, the mirror image of RiderEditor's), so
    ``_load()`` must never call them.
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
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)

    presenter.on_pick_csv_import(_FIXTURES / "clean_pooled.csv")

    assert ("set_import_enabled", (True,)) in view.calls


def test_on_pick_csv_import_given_clean_pooled_fixture_shows_its_known_counts() -> None:
    """clean_pooled.csv: 4 solo + Falcons(3) + Hawks(2) = 9 riders."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)

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


def test_on_pick_csv_import_given_solo_only_ride_team_file_refuses_cleanly(
    tmp_path: Path,
) -> None:
    """B5: a solo-only ride flags team rows and refuses, no crash."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    path = _write_pooled_csv(
        tmp_path, "Alex,Roy,team,Trail Blazers,77,\nKai,Singh,team,Trail Blazers,78,\n"
    )

    presenter.on_pick_csv_import(path)

    assert presenter.on_confirm_csv_import() is False
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
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)

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
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)

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

    def _preview_that_raises(_path: object, _ride: object, **_kwargs: object) -> object:
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


# ------------------------------- mapping unknown sex to Male (Phase 3)


def _write_sex_csv(directory: Path, rows: str) -> Path:
    """Write a unified CSV carrying a SEX column; return its path."""
    path = directory / "riders.csv"
    path.write_text(f"firstname,lastname,type,teamname,number,sex\n{rows}", encoding="utf-8")
    return path


def test_on_toggle_map_unknown_sex_true_repreviews_the_picked_file_as_clean(
    tmp_path: Path,
) -> None:
    """Checking the box re-previews the picked file cleanly."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    path = _write_sex_csv(tmp_path, "Alex,Ferreira,solo,,1,X\nBo,Lindqvist,solo,,2,\n")
    presenter.on_pick_csv_import(path)
    first = view.calls[0][1][0]
    assert first.conflicts == (CsvConflict(row=2, problem="invalid sex 'X'"),)
    view.calls.clear()

    presenter.on_toggle_map_unknown_sex(enabled=True)

    second = view.calls[0][1][0]
    assert second.conflicts == ()
    assert second.summary == "riders.csv → 2 riders · 0 teams · 0 conflicts"
    assert view.calls[-1] == ("set_import_enabled", (True,))


def test_on_toggle_map_unknown_sex_false_repreviews_with_the_conflict_restored(
    tmp_path: Path,
) -> None:
    """Unchecking restores the unrecognized-sex conflict."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)
    path = _write_sex_csv(tmp_path, "Alex,Ferreira,solo,,1,X\n")
    presenter.on_pick_csv_import(path)
    presenter.on_toggle_map_unknown_sex(enabled=True)
    view.calls.clear()

    presenter.on_toggle_map_unknown_sex(enabled=False)

    preview = view.calls[0][1][0]
    assert preview.conflicts == (CsvConflict(row=2, problem="invalid sex 'X'"),)
    assert view.calls[-1] == ("set_import_enabled", (False,))


def test_on_toggle_map_unknown_sex_before_any_pick_is_a_no_op() -> None:
    """T-3: the toggle's own guard -- no preview yet renders nothing."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_toggle_map_unknown_sex(enabled=True)

    assert view.calls == []


# ---------------------------- converting teams of one to solo (Phase E)


def _write_one_rider_team_csv(directory: Path) -> Path:
    """Write a one-lone-team unified CSV; return its path."""
    path = directory / "riders.csv"
    path.write_text(
        "firstname,lastname,type,teamname,number,notes\nAlex,Ellis,team,Solo Team,4,\n",
        encoding="utf-8",
    )
    return path


def test_on_toggle_convert_teams_of_one_repreviews_the_picked_file_as_solo(
    tmp_path: Path,
) -> None:
    """Checking the box re-previews the lone team as a solo entry."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)
    presenter.on_pick_csv_import(_write_one_rider_team_csv(tmp_path))
    view.calls.clear()

    presenter.on_toggle_convert_teams_of_one(enabled=True)

    preview = view.calls[0][1][0]
    assert (preview.summary, preview.conflicts, preview.warnings) == (
        "riders.csv → 1 riders · 0 teams · 0 conflicts",
        (),
        (),
    )
    assert view.calls[-1] == ("set_import_enabled", (True,))


def test_on_toggle_convert_teams_of_one_false_restores_the_team(
    tmp_path: Path,
) -> None:
    """Unchecking restores the team entry and its under-min warning."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(entry_mode=EntryMode.MIXED), load=False)
    presenter.on_pick_csv_import(_write_one_rider_team_csv(tmp_path))
    presenter.on_toggle_convert_teams_of_one(enabled=True)
    view.calls.clear()

    presenter.on_toggle_convert_teams_of_one(enabled=False)

    preview = view.calls[0][1][0]
    assert (preview.summary, preview.warnings) == (
        "riders.csv → 1 riders · 1 teams · 0 conflicts · 1 warnings",
        (
            CsvConflict(
                row=2, problem="team of 1 rider is below the minimum of 2 (team-under-min)"
            ),
        ),
    )


def test_on_toggle_convert_teams_of_one_before_any_pick_is_a_no_op() -> None:
    """T-3: the toggle's own guard -- no preview yet renders nothing."""
    view = RecordingRidersView()
    presenter = RidersPresenter(view, Roster(), load=False)

    presenter.on_toggle_convert_teams_of_one(enabled=True)

    assert view.calls == []


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
    ``show_riders`` (module docstring's own mirror-image split), so
    this handler must not call it: a live
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
        == "FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES,SEX"
    )
