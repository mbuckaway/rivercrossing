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
from rivercrossing.roster import EntryMode, EntryType, PlateModel, Rider, Roster
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


# ---------------------------------------------------- construction


def test_presenter_init_given_empty_roster_renders_empty_issues_and_zero_summary() -> None:
    """An empty roster renders no rows and a zero summary."""
    view = RecordingRiderIssuesView()

    RiderIssuesPresenter(view, Roster())

    assert view.calls == [
        ("show_issues", ([],)),
        ("show_summary", ("0 rider issue(s)",)),
        ("set_convert_solo_enabled", (False,)),
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


def test_presenter_did_change_starts_false() -> None:
    """A freshly built presenter reports no change yet."""
    presenter = RiderIssuesPresenter(RecordingRiderIssuesView(), Roster())

    assert presenter.did_change is False


def test_refresh_rerenders_issues_from_the_current_roster() -> None:
    """refresh() re-reads the roster after a change this presenter didn't make."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    view = RecordingRiderIssuesView()
    presenter = RiderIssuesPresenter(view, roster)
    view.calls.clear()

    roster.create_team_entry_of_one(
        display_name="Lone Wolf",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )
    presenter.refresh()

    assert (
        "show_issues",
        ([RiderIssueRow(plate="7", name="Lone Wolf", message="team size must be at least 2, got 1")],),
    ) in view.calls
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


# ------------------------------------------------------- protocol


def test_recording_view_satisfies_the_rider_issues_view_protocol() -> None:
    """The fake implements every RiderIssuesView member."""
    assert isinstance(RecordingRiderIssuesView(), RiderIssuesView)
