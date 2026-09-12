# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the rider-issues presenter, tests-first.

``RiderIssuesPresenter`` drives the "Check for Rider Issues..."
dialog from a real, in-memory
:class:`~rivercrossing.roster.Roster`: it reads the roster's defect
report (:func:`~rivercrossing.rider_issues.rider_issues`), renders
one row per defect plus a summary, and offers one corrective
action -- converting a pooled size-1 team's lone rider into their
own solo entry (``extract_rider_to_solo``). Like ``test_riders.py``'s
``RecordingRidersView``, ``RecordingRiderIssuesView`` is a
hand-written fake recording every call with its exact arguments; no
``unittest.mock`` is needed since the presenter touches no I/O
boundary (T-10).
"""

from __future__ import annotations

from rivercrossing.ride import RideStatus
from rivercrossing.roster import (
    Entry,
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
    RosterError,
)
from rivercrossing.ui.presenters.rider_issues import (
    RiderIssueRow,
    RiderIssuesPresenter,
    RiderIssuesView,
)


class RecordingRiderIssuesView:
    """A complete ``RiderIssuesView`` spy recording every call."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_issues(self, rows: list[RiderIssueRow]) -> None:
        """Record the rendered issue-list rows."""
        self.calls.append(("show_issues", (rows,)))

    def show_summary(self, text: str) -> None:
        """Record the rendered summary line."""
        self.calls.append(("show_summary", (text,)))

    def set_convert_solo_enabled(self, *, enabled: bool) -> None:
        """Record convert_solo_btn's enabled state."""
        self.calls.append(("set_convert_solo_enabled", (enabled,)))

    def set_assign_plate_enabled(self, *, enabled: bool) -> None:
        """Record assign_plate_btn's enabled state."""
        self.calls.append(("set_assign_plate_enabled", (enabled,)))

    def set_renumber_enabled(self, *, enabled: bool) -> None:
        """Record renumber_btn's enabled state."""
        self.calls.append(("set_renumber_enabled", (enabled,)))

    def show_validation(self, message: str) -> None:
        """Record a refused-operation message."""
        self.calls.append(("show_validation", (message,)))


def _team_of_one_roster() -> Roster:
    """Return a pooled mixed roster with one size-1 team."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )
    return roster


def _team_of_one_and_duplicate_roster() -> Roster:
    """Return a roster with a size-1 team and a dup name."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )
    roster.create_solo_entry(first_name="Mary Anne", last_name="Knibbe", plate="1")
    roster.create_solo_entry(first_name="Mary Anne", last_name="Knibbe", plate="2")
    return roster


def _missing_number_roster() -> Roster:
    """Return a pooled roster with one plateless solo rider."""
    roster = Roster(plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="",
                display_name="Alex Roy",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Alex", last_name="Roy", plate=None)],
            )
        ]
    )
    return roster


def _missing_number_team_roster() -> Roster:
    """Return a pooled mixed roster with one plateless team member."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="1",
                display_name="Trail Blazers",
                type=EntryType.TEAM,
                riders=[
                    Rider(first_name="Sam", last_name="Ellis", plate="1"),
                    Rider(first_name="Alex", last_name="Roy", plate=None),
                ],
            )
        ]
    )
    return roster


def _pooled_duplicate_number_roster() -> Roster:
    """Return a pooled roster whose two solo riders claim plate "7"."""
    roster = Roster(plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="7",
                display_name="Sam Ellis",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Sam", last_name="Ellis", plate="7")],
            ),
            Entry(
                plate="7",
                display_name="Alex Roy",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Alex", last_name="Roy", plate="7")],
            ),
        ]
    )
    return roster


def _relay_duplicate_number_roster() -> Roster:
    """Return a relay roster whose two entries claim plate "7"."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.load_entries(
        [
            Entry(
                plate="7",
                display_name="Sam Ellis",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Sam", last_name="Ellis", plate=None)],
            ),
            Entry(
                plate="7",
                display_name="Alex Roy",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Alex", last_name="Roy", plate=None)],
            ),
        ]
    )
    return roster


class _PlateRefusingRoster(Roster):
    """A real roster whose one shared plate write always refuses.

    Every real-roster state the presenter's fix gates allow reaches
    :meth:`Roster.change_plate` without raising, so the fix handlers'
    ``except RosterError`` arms are only reachable when the write itself
    fails defensively. This subclass stands in for that write failure.
    """

    def change_plate(self, entry: Entry, rider: Rider | None, *, plate: str) -> None:  # noqa: ARG002 -- override; the refusal never reads its arguments
        """Refuse the shared write: raise a RosterError instead."""
        raise RosterError("plate refused")


class _ExtractRefusingRoster(Roster):
    """A real roster whose one solo-extraction write always refuses.

    Every real-roster state that passes the presenter's convert gate
    reaches :meth:`Roster.extract_rider_to_solo` without raising, so
    the presenter's ``except RosterError`` arm is only reachable when
    the write itself fails defensively. This subclass stands in for
    that write failure -- reads (entries/plate_model/status) stay the
    real base roster's -- so the arm's refusal shape can be pinned.
    """

    def extract_rider_to_solo(self, rider: Rider) -> Entry:  # noqa: ARG002 -- override signature; the refusal never reads the rider
        """Refuse the extraction write: raise a RosterError instead."""
        raise RosterError("extract refused")


# ---------------------------------------------------- construction


def test_presenter_init_given_empty_roster_renders_empty_issues_and_zero_summary() -> None:
    """An empty roster renders no rows and a zero summary."""
    view = RecordingRiderIssuesView()

    RiderIssuesPresenter(view, Roster())

    # The view's own reconcile owns button enablement now (it reads the
    # list control's real selection), so construction loads the report
    # without gating any button itself.
    assert view.calls == [
        ("show_issues", ([],)),
        ("show_summary", ("0 rider issue(s)",)),
    ]


def test_presenter_init_given_team_of_one_and_duplicate_renders_canonical_rows() -> None:
    """Issues render in order: team-of-one, then duplicate-name."""
    view = RecordingRiderIssuesView()

    RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())

    assert (
        "show_issues",
        (
            [
                RiderIssueRow(
                    plate="7",
                    name="Lone Wolf",
                    message="team size must be at least 2, got 1",
                ),
                RiderIssueRow(
                    plate="2",
                    name="Mary Anne Knibbe",
                    message="duplicate rider name Mary Anne Knibbe",
                ),
            ],
        ),
    ) in view.calls
    assert ("show_summary", ("2 rider issue(s)",)) in view.calls


def test_presenter_init_given_empty_team_display_name_renders_rider_name_fallback() -> None:
    """A rider-scoped issue on an unnamed entry shows the rider's name.

    ``_row_name`` falls back to ``issue.rider.full_name`` when the
    entry's ``display_name`` is falsy: a team created with a blank
    display name whose own riders carry the duplicate-name defect
    renders each row under the rider's full name, never blank.
    """
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(
        display_name="",
        riders=[
            Rider(first_name="Sam", last_name="Ellis", plate="1"),
            Rider(first_name="Sam", last_name="Ellis", plate="2"),
        ],
    )
    view = RecordingRiderIssuesView()

    RiderIssuesPresenter(view, roster)

    assert (
        "show_issues",
        (
            [
                RiderIssueRow(
                    plate="1",
                    name="Sam Ellis",
                    message="duplicate rider name Sam Ellis",
                )
            ],
        ),
    ) in view.calls
    assert ("show_summary", ("1 rider issue(s)",)) in view.calls


def test_presenter_did_change_starts_false() -> None:
    """A freshly built presenter reports no change yet."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), Roster())

    assert presenter.did_change is False


def test_refresh_rerenders_issues_from_the_current_roster() -> None:
    """refresh() re-reads the roster after an external change."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    view.calls.clear()

    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )
    presenter.refresh()

    lone_row = RiderIssueRow(
        plate="7", name="Lone Wolf", message="team size must be at least 2, got 1"
    )
    assert ("show_issues", ([lone_row],)) in view.calls
    assert ("show_summary", ("1 rider issue(s)",)) in view.calls


# ------------------------------------------------- on_row_selected


def test_on_row_selected_given_pooled_draft_team_of_one_enables_convert() -> None:
    """A pooled DRAFT team-of-one enables the convert button."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_convert_solo_enabled", (True,)) in view.calls


def test_on_row_selected_given_duplicate_name_disables_convert() -> None:
    """A non-team-of-one issue never enables convert."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_row_selected(1)

    assert ("set_convert_solo_enabled", (False,)) in view.calls


def test_on_row_selected_given_relay_team_of_one_disables_convert() -> None:
    """A team_relay team-of-one disables convert."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis"),
        plate="7",
    )
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_convert_solo_enabled", (False,)) in view.calls


def test_on_row_selected_given_team_of_one_after_start_disables_convert() -> None:
    """A team-of-one disables convert once the ride left DRAFT."""
    roster = _team_of_one_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_convert_solo_enabled", (False,)) in view.calls


def test_on_row_selected_given_a_negative_index_clears_and_disables_every_button() -> None:
    """An out-of-range row is a stale event, not an IndexError (T-4)."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_row_selected(-1)

    assert (presenter._selected, view.calls) == (
        None,
        [
            ("set_convert_solo_enabled", (False,)),
            ("set_assign_plate_enabled", (False,)),
            ("set_renumber_enabled", (False,)),
        ],
    )


def test_on_row_selected_given_a_row_past_the_end_clears_and_disables_every_button() -> None:
    """One past the last row is a stale event too (T-4: max + 1)."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_row_selected(2)

    assert (presenter._selected, view.calls) == (
        None,
        [
            ("set_convert_solo_enabled", (False,)),
            ("set_assign_plate_enabled", (False,)),
            ("set_renumber_enabled", (False,)),
        ],
    )


def test_on_row_selected_given_the_last_row_accepts_it() -> None:
    """The last legal row is accepted (T-4: max)."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_row_selected(1)

    assert ("set_convert_solo_enabled", (False,)) in view.calls
    assert presenter._selected is not None


def test_on_nothing_selected_clears_the_selection_and_disables_every_button() -> None:
    """The reconcile's no-selection arm owns every disable."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    view.calls.clear()

    presenter.on_nothing_selected()

    assert (presenter._selected, view.calls) == (
        None,
        [
            ("set_convert_solo_enabled", (False,)),
            ("set_assign_plate_enabled", (False,)),
            ("set_renumber_enabled", (False,)),
        ],
    )


def test_on_row_selected_given_missing_number_enables_assign_only() -> None:
    """A missing-number row enables Assign Plate only."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _missing_number_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert view.calls == [
        ("set_convert_solo_enabled", (False,)),
        ("set_assign_plate_enabled", (True,)),
        ("set_renumber_enabled", (False,)),
    ]


def test_on_row_selected_given_duplicate_number_enables_renumber_only() -> None:
    """A duplicate-number row enables Renumber; the others stay off."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _pooled_duplicate_number_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert view.calls == [
        ("set_convert_solo_enabled", (False,)),
        ("set_assign_plate_enabled", (False,)),
        ("set_renumber_enabled", (True,)),
    ]


def test_on_row_selected_given_missing_number_after_start_disables_assign() -> None:
    """A started ride locks the missing-number fix."""
    roster = _missing_number_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_assign_plate_enabled", (False,)) in view.calls


# --------------------------------------------------- on_open_editor


def test_on_open_editor_given_team_of_one_returns_team() -> None:
    """A team-of-one issue opens the team editor."""
    presenter = RiderIssuesPresenter(
        RecordingRiderIssuesView(), _team_of_one_and_duplicate_roster()
    )
    presenter.on_row_selected(0)

    assert presenter.on_open_editor() == "team"


def test_on_open_editor_given_duplicate_name_returns_rider() -> None:
    """A rider-scoped issue opens the rider editor."""
    presenter = RiderIssuesPresenter(
        RecordingRiderIssuesView(), _team_of_one_and_duplicate_roster()
    )
    presenter.on_row_selected(1)

    assert presenter.on_open_editor() == "rider"


def test_on_open_editor_given_nothing_selected_returns_empty_string() -> None:
    """With no selection there is nothing to open."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), Roster())

    assert presenter.on_open_editor() == ""


# -------------------------------------------------- on_convert_solo


def test_on_convert_solo_given_convertible_team_of_one_returns_true_and_extracts() -> None:
    """A convertible team-of-one converts and flags did_change."""
    roster = _team_of_one_and_duplicate_roster()
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), roster)
    presenter.on_row_selected(0)

    converted = presenter.on_convert_solo()

    assert converted is True
    assert presenter.did_change is True
    assert [entry.display_name for entry in roster.entries] == [
        "Mary Anne Knibbe",
        "Mary Anne Knibbe",
        "Sam Ellis",
    ]
    assert all(entry.type is EntryType.SOLO for entry in roster.entries)


def test_on_convert_solo_rerenders_one_remaining_issue() -> None:
    """After converting, the report re-renders one issue."""
    roster = _team_of_one_and_duplicate_roster()
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    presenter.on_convert_solo()

    assert ("show_summary", ("1 rider issue(s)",)) in view.calls


def test_on_convert_solo_given_duplicate_name_returns_false_and_validates() -> None:
    """A non-team issue refuses via show_validation."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _team_of_one_and_duplicate_roster())
    presenter.on_row_selected(1)
    view.calls.clear()

    converted = presenter.on_convert_solo()

    assert converted is False
    assert presenter.did_change is False
    assert view.calls == [("show_validation", ("only a team-of-one can be converted to solo",))]


def test_on_convert_solo_given_nothing_selected_returns_false_and_validates() -> None:
    """With no selection the conversion refuses cleanly."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, Roster())
    view.calls.clear()

    converted = presenter.on_convert_solo()

    assert converted is False
    assert view.calls == [("show_validation", ("select a team-of-one to convert",))]


def test_on_convert_solo_given_team_of_one_after_start_returns_false_and_validates() -> None:
    """A post-DRAFT team-of-one refuses, naming DRAFT."""
    roster = _team_of_one_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    converted = presenter.on_convert_solo()

    assert converted is False
    assert view.calls == [
        ("show_validation", ("a team-of-one can only be converted while the ride is draft",))
    ]


def test_on_convert_solo_given_relay_team_of_one_returns_false_and_validates() -> None:
    """A team_relay team-of-one refuses, naming pooled."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis"),
        plate="7",
    )
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    converted = presenter.on_convert_solo()

    assert converted is False
    assert view.calls == [("show_validation", ("convert to solo requires a rider-pooled ride",))]


def test_on_convert_solo_given_roster_error_returns_false_and_validates() -> None:
    """A RosterError from the write surfaces via show_validation."""
    roster = _ExtractRefusingRoster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    converted = presenter.on_convert_solo()

    assert converted is False
    assert presenter.did_change is False
    assert view.calls == [("show_validation", ("extract refused",))]


# --------------------------------------------------- on_assign_plate


def test_on_assign_plate_given_pooled_solo_missing_number_assigns_the_next_plate() -> None:
    """A plateless pooled solo rider gets the next free plate."""
    roster = _missing_number_roster()
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), roster)
    presenter.on_row_selected(0)

    assigned = presenter.on_assign_plate()

    assert assigned is True
    assert presenter.did_change is True
    assert (roster.entries[0].plate, roster.entries[0].riders[0].plate) == ("1", "1")


def test_on_assign_plate_given_pooled_team_member_assigns_the_members_own_plate() -> None:
    """A pooled member plate goes through the member primitive."""
    roster = _missing_number_team_roster()
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), roster)
    presenter.on_row_selected(0)

    assigned = presenter.on_assign_plate()

    assert assigned is True
    assert roster.entries[0].riders[1].plate == "2"
    # The team's derived plate stays the lowest member's.
    assert roster.entries[0].plate == "1"


def test_on_assign_plate_given_nothing_selected_returns_false_and_validates() -> None:
    """With no selection the assign fix refuses cleanly."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _missing_number_roster())
    view.calls.clear()

    assigned = presenter.on_assign_plate()

    assert assigned is False
    assert view.calls == [
        ("show_validation", ("select a missing-number issue to assign a plate",))
    ]


def test_on_assign_plate_given_duplicate_number_returns_false_and_validates() -> None:
    """The assign fix refuses a row that is not a missing-number."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _pooled_duplicate_number_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    assigned = presenter.on_assign_plate()

    assert assigned is False
    assert presenter.did_change is False
    assert view.calls == [
        ("show_validation", ("select a missing-number issue to assign a plate",))
    ]


def test_on_assign_plate_given_a_started_ride_returns_false_and_validates() -> None:
    """A post-DRAFT missing-number refuses, naming DRAFT."""
    roster = _missing_number_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    assigned = presenter.on_assign_plate()

    assert assigned is False
    assert view.calls == [
        ("show_validation", ("plates can only be assigned while the ride is draft",))
    ]


def test_on_assign_plate_given_a_roster_error_returns_false_and_validates() -> None:
    """A RosterError from the shared write surfaces cleanly."""
    roster = _PlateRefusingRoster(plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="",
                display_name="Alex Roy",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Alex", last_name="Roy", plate=None)],
            )
        ]
    )
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    assigned = presenter.on_assign_plate()

    assert assigned is False
    assert presenter.did_change is False
    assert view.calls == [("show_validation", ("plate refused",))]


# ------------------------------------------------------- on_renumber


def test_on_renumber_given_pooled_duplicate_number_renumbers_the_later_claimant() -> None:
    """The later solo claimant moves to the next free plate."""
    roster = _pooled_duplicate_number_roster()
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), roster)
    presenter.on_row_selected(0)

    renumbered = presenter.on_renumber()

    assert renumbered is True
    assert presenter.did_change is True
    assert (roster.entries[0].plate, roster.entries[1].plate) == ("7", "8")
    assert roster.entries[1].riders[0].plate == "8"


def test_on_renumber_given_relay_duplicate_number_uses_change_team_plate() -> None:
    """A relay duplicate renumbers the entry's own plate."""
    roster = _relay_duplicate_number_roster()
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), roster)
    presenter.on_row_selected(0)

    renumbered = presenter.on_renumber()

    assert renumbered is True
    assert (roster.entries[0].plate, roster.entries[1].plate) == ("7", "8")
    assert roster.entries[1].riders[0].plate is None


def test_on_renumber_given_nothing_selected_returns_false_and_validates() -> None:
    """With no selection the renumber fix refuses cleanly."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _pooled_duplicate_number_roster())
    view.calls.clear()

    renumbered = presenter.on_renumber()

    assert renumbered is False
    assert view.calls == [("show_validation", ("select a duplicate-number issue to renumber",))]


def test_on_renumber_given_missing_number_returns_false_and_validates() -> None:
    """The renumber fix refuses a row that is not a duplicate-number."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _missing_number_roster())
    presenter.on_row_selected(0)
    view.calls.clear()

    renumbered = presenter.on_renumber()

    assert renumbered is False
    assert presenter.did_change is False
    assert view.calls == [("show_validation", ("select a duplicate-number issue to renumber",))]


def test_on_renumber_given_a_started_ride_returns_false_and_validates() -> None:
    """A post-DRAFT duplicate-number refuses, naming DRAFT."""
    roster = _pooled_duplicate_number_roster()
    roster.status = RideStatus.RUNNING
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()

    renumbered = presenter.on_renumber()

    assert renumbered is False
    assert view.calls == [
        ("show_validation", ("plates can only be changed while the ride is draft",))
    ]


# ------------------------------------------------------- preselect


def test_selected_plate_given_a_rider_scoped_issue_returns_the_riders_plate() -> None:
    """The rider editor preselects on the issue's own rider plate."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), _pooled_duplicate_number_roster())
    presenter.on_row_selected(0)

    assert presenter.selected_plate() == "7"


def test_selected_plate_given_a_relay_duplicate_returns_the_entries_plate() -> None:
    """A relay issue preselects on the entry's plate."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), _relay_duplicate_number_roster())
    presenter.on_row_selected(0)

    assert presenter.selected_plate() == "7"


def test_selected_team_name_given_nothing_selected_returns_empty_string() -> None:
    """With no selection there is no team to preselect."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), Roster())

    assert presenter.selected_team_name() == ""


# ------------------------------------------------------- protocol


def test_recording_view_satisfies_the_rider_issues_view_protocol() -> None:
    """The fake implements every RiderIssuesView member."""
    assert isinstance(RecordingRiderIssuesView(), RiderIssuesView)


# ============================================================ W8
# Zero-rider TEAM entries (W8): a W8 empty team is a "team-of-one"
# issue with size 0, is never convertible (there is no rider to
# extract), and Convert says why instead of raising.


def _zero_rider_team_roster() -> Roster:
    """Return a pooled mixed roster with one empty team."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_empty_team(display_name="Trail Blazers")
    return roster


def test_presenter_init_given_a_zero_rider_team_reports_team_of_one_with_size_zero() -> None:
    """An empty team is reported as a team-of-one at size 0."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _zero_rider_team_roster())

    (issue,) = presenter._issues
    assert issue.kind == "team-of-one"
    assert issue.message == "team size must be at least 2, got 0"
    assert ("show_summary", ("1 rider issue(s)",)) in view.calls


def test_on_row_selected_given_a_zero_rider_team_disables_convert() -> None:
    """An empty team has no rider to extract, so Convert stays off."""
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, _zero_rider_team_roster())
    view.calls.clear()

    presenter.on_row_selected(0)

    assert ("set_convert_solo_enabled", (False,)) in view.calls


def test_on_convert_solo_given_a_zero_rider_team_returns_false_and_validates() -> None:
    """Convert on an empty team refuses, naming why, changing none."""
    view = RecordingRiderIssuesView()
    roster = _zero_rider_team_roster()
    presenter = RiderIssuesPresenter(view, roster)
    presenter.on_row_selected(0)
    view.calls.clear()
    before = roster.audit_log

    converted = presenter.on_convert_solo()

    assert converted is False
    assert ("show_validation", ("an empty team has no rider to convert to solo",)) in view.calls
    assert roster.audit_log == before
