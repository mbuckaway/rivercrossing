# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.roster (E3.1.1).

Spec section 1 and section 2's ``entry``/``rider`` columns are this
task's specification, narrowed by task-briefs.md's own named cases:
plate unique per ride (one shared namespace, R-16); team size
2..max_team_size(<=10, R-12); solo-only is the Roster default
(R-11); pooled vs relay plate shapes, including a pooled team's
lowest-numbered-rider-plate adoption; and the three named negatives
-- an 11-rider team, a duplicate plate, and a team entry on a
solo-only ride -- all raise.

Written FIRST, against a module that does not exist yet: this file
is red until rivercrossing/roster.py lands.
"""

import re
from typing import TYPE_CHECKING

import pytest

from rivercrossing.roster import (
    DEFAULT_MAX_TEAM_SIZE,
    MAX_TEAM_SIZE_LIMIT,
    MIN_TEAM_SIZE,
    AuditEvent,
    DuplicatePlateError,
    Entry,
    EntryMode,
    EntryNotFoundError,
    EntryStatus,
    EntryType,
    InvalidMoveError,
    PlateModel,
    PlateShapeError,
    Rider,
    RiderNotFoundError,
    Roster,
    SoloOnlyRideError,
    TeamSizeError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# ----------------------------------------------------------- defaults


def test_roster_bare_construction_defaults_to_solo_only() -> None:
    """A bare Roster() is solo-only (R-11)."""
    roster = Roster()

    assert roster.entry_mode == EntryMode.SOLO


def test_roster_bare_construction_defaults_max_team_size_to_four() -> None:
    """A bare Roster() defaults max_team_size to 4 (R-12)."""
    roster = Roster()

    assert roster.max_team_size == DEFAULT_MAX_TEAM_SIZE


def test_roster_bare_construction_defaults_plate_model_to_rider_pooled() -> None:
    """rider_pooled is the default plate_model (spec S1)."""
    roster = Roster()

    assert roster.plate_model == PlateModel.RIDER_POOLED


def test_roster_bare_construction_starts_with_no_entries_or_audit_events() -> None:
    """A fresh Roster starts empty: no entries, no audit log."""
    roster = Roster()

    assert (roster.entries, roster.audit_log) == ((), ())


# ------------------------------------------------ max_team_size bound


@pytest.mark.parametrize("max_team_size", [1, 11])  # min - 1, max + 1
def test_roster_construction_max_team_size_out_of_range_raises(max_team_size: int) -> None:
    """max_team_size outside 2..10 raises at construction."""
    with pytest.raises(TeamSizeError, match=re.escape("max_team_size")):
        Roster(max_team_size=max_team_size)


@pytest.mark.parametrize("max_team_size", [2, 3, 9, 10])  # min, min+1, max-1, max
def test_roster_construction_max_team_size_in_range_is_accepted(max_team_size: int) -> None:
    """max_team_size within 2..10 is accepted as given."""
    roster = Roster(max_team_size=max_team_size)

    assert roster.max_team_size == max_team_size


def test_roster_construction_max_team_size_limit_matches_hard_ceiling() -> None:
    """The module's hard ceiling constant is exactly 10 (R-12)."""
    assert (MIN_TEAM_SIZE, MAX_TEAM_SIZE_LIMIT) == (2, 10)


# ---------------------------------------------------- create_solo_entry


def test_create_solo_entry_relay_stores_plate_on_entry_not_rider() -> None:
    """team_relay: the entry carries the plate; rider carries none."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)

    entry = roster.create_solo_entry(name="Alex", plate="12")

    assert (entry.plate, entry.riders[0].plate) == ("12", None)


def test_create_solo_entry_pooled_derives_entry_plate_from_its_rider() -> None:
    """rider_pooled: a solo entry's plate is its rider's plate."""
    roster = Roster(plate_model=PlateModel.RIDER_POOLED)

    entry = roster.create_solo_entry(name="Alex", plate="12")

    assert (entry.plate, entry.riders[0].plate) == ("12", "12")


def test_create_solo_entry_sets_display_name_type_and_default_status() -> None:
    """A new solo entry's display_name/type/status/notes match input."""
    roster = Roster()

    entry = roster.create_solo_entry(name="Alex", plate="12")

    assert (entry.display_name, entry.type, entry.status, entry.notes) == (
        "Alex",
        EntryType.SOLO,
        EntryStatus.ACTIVE,
        "",
    )


def test_create_solo_entry_team_size_is_always_one() -> None:
    """A solo entry always reports team_size == 1."""
    roster = Roster()

    entry = roster.create_solo_entry(name="Alex", plate="12")

    assert entry.team_size == 1


def test_create_solo_entry_appends_entry_to_roster_entries() -> None:
    """A created solo entry is a member of roster.entries."""
    roster = Roster()

    entry = roster.create_solo_entry(name="Alex", plate="12")

    assert roster.entries == (entry,)


def test_create_solo_entry_appends_one_audit_event_with_action_and_plate() -> None:
    """create_solo_entry logs one event naming action and plate."""
    roster = Roster()

    roster.create_solo_entry(name="Alex", plate="12")

    assert roster.audit_log == (
        AuditEvent(action="create_solo_entry", payload={"plate": "12", "name": "Alex"}),
    )


# ---------------------------------------------------- create_team_entry


def test_create_team_entry_relay_carries_plate_on_entry_and_clears_riders() -> None:
    """team_relay: a team's plate lives on the entry, not riders."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    riders = [Rider(name="Alex"), Rider(name="Bo")]

    entry = roster.create_team_entry(display_name="Team A", riders=riders, plate="7")

    assert (entry.plate, [r.plate for r in entry.riders]) == ("7", [None, None])


def test_create_team_entry_pooled_adopts_lowest_numbered_rider_plate() -> None:
    """rider_pooled: a team's plate is its lowest rider's plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    riders = [Rider(name="Alex", plate="45"), Rider(name="Bo", plate="9")]

    entry = roster.create_team_entry(display_name="Team A", riders=riders)

    assert entry.plate == "9"


def test_create_team_entry_sets_display_name_type_and_default_status() -> None:
    """A new team entry's display_name/type/status/notes match input."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    riders = [Rider(name="Alex", plate="1"), Rider(name="Bo", plate="2")]

    entry = roster.create_team_entry(display_name="Team A", riders=riders)

    assert (entry.display_name, entry.type, entry.status, entry.notes) == (
        "Team A",
        EntryType.TEAM,
        EntryStatus.ACTIVE,
        "",
    )


def test_create_team_entry_appends_one_audit_event_with_team_size() -> None:
    """create_team_entry logs one event naming plate/name/size."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    riders = [Rider(name="Alex", plate="1"), Rider(name="Bo", plate="2")]

    entry = roster.create_team_entry(display_name="Team A", riders=riders)

    assert roster.audit_log == (
        AuditEvent(
            action="create_team_entry",
            payload={"plate": entry.plate, "display_name": "Team A", "team_size": 2},
        ),
    )


@pytest.mark.parametrize("rider_count", [1, 5])  # min - 1, max + 1 (max_team_size=4 default)
def test_create_team_entry_team_size_out_of_range_raises(rider_count: int) -> None:
    """Team size outside 2..max_team_size(4) raises TeamSizeError."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    riders = [Rider(name=f"r{i}", plate=str(i)) for i in range(rider_count)]

    with pytest.raises(TeamSizeError, match=re.escape("team size")):
        roster.create_team_entry(display_name="Team A", riders=riders)


@pytest.mark.parametrize("rider_count", [2, 3, 4])  # min, min+1==max-1, max
def test_create_team_entry_team_size_in_range_is_accepted(rider_count: int) -> None:
    """Team size within 2..max_team_size(4) is accepted as given."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    riders = [Rider(name=f"r{i}", plate=str(i)) for i in range(rider_count)]

    entry = roster.create_team_entry(display_name="Team A", riders=riders)

    assert entry.team_size == rider_count


def test_create_team_entry_eleven_riders_at_the_hard_ceiling_raises() -> None:
    """An 11-rider team exceeds max_team_size and the hard cap."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=MAX_TEAM_SIZE_LIMIT)
    riders = [Rider(name=f"r{i}", plate=str(i)) for i in range(11)]

    with pytest.raises(TeamSizeError, match=re.escape("team size")):
        roster.create_team_entry(display_name="Team A", riders=riders)


# ------------------------------------------------------- solo-only ride


def test_create_team_entry_on_solo_only_ride_raises_solo_only_ride_error() -> None:
    """A team entry on a solo-only ride raises SoloOnlyRideError."""
    roster = Roster(entry_mode=EntryMode.SOLO)
    riders = [Rider(name="Alex", plate="1"), Rider(name="Bo", plate="2")]

    with pytest.raises(SoloOnlyRideError, match=re.escape("solo-only")):
        roster.create_team_entry(display_name="Team A", riders=riders)


def test_create_solo_entry_on_solo_only_ride_succeeds() -> None:
    """A solo entry on a solo-only ride is unaffected by entry_mode."""
    roster = Roster(entry_mode=EntryMode.SOLO)

    entry = roster.create_solo_entry(name="Alex", plate="1")

    assert entry.type == EntryType.SOLO


# ------------------------------------------------------- plate shapes


def test_create_team_entry_relay_without_a_plate_raises_plate_shape_error() -> None:
    """team_relay requires an explicit entry-level plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    riders = [Rider(name="Alex"), Rider(name="Bo")]

    with pytest.raises(PlateShapeError, match=re.escape("team_relay")):
        roster.create_team_entry(display_name="Team A", riders=riders)


def test_create_team_entry_pooled_with_a_riderless_plate_raises_plate_shape_error() -> None:
    """rider_pooled requires every team member to carry a plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    riders = [Rider(name="Alex", plate="1"), Rider(name="Bo")]

    with pytest.raises(PlateShapeError, match=re.escape("rider_pooled")):
        roster.create_team_entry(display_name="Team A", riders=riders)


# --------------------------------------------------- duplicate plates


def test_create_solo_entry_duplicate_of_existing_entry_plate_raises() -> None:
    """A new solo plate matching an existing entry's plate raises."""
    roster = Roster()
    roster.create_solo_entry(name="Alex", plate="12")

    with pytest.raises(DuplicatePlateError, match=re.escape("12")):
        roster.create_solo_entry(name="Bo", plate="12")


def test_create_team_entry_rider_plate_colliding_with_existing_rider_raises() -> None:
    """A new team's rider plate matching an existing rider raises."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_solo_entry(name="Alex", plate="12")
    riders = [Rider(name="Bo", plate="12"), Rider(name="Cy", plate="13")]

    with pytest.raises(DuplicatePlateError, match=re.escape("12")):
        roster.create_team_entry(display_name="Team A", riders=riders)


def test_create_team_entry_rider_plate_colliding_with_another_teams_rider_raises() -> None:
    """A new team's rider matching a different team's rider raises."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Team A", riders=[Rider(name="Alex", plate="1"), Rider(name="Bo", plate="2")]
    )

    with pytest.raises(DuplicatePlateError, match=re.escape("2")):
        roster.create_team_entry(
            display_name="Team B",
            riders=[Rider(name="Cy", plate="2"), Rider(name="Do", plate="9")],
        )


def test_create_team_entry_two_riders_sharing_a_plate_within_one_call_raises() -> None:
    """Two riders in one new team sharing a plate raises."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    riders = [Rider(name="Alex", plate="5"), Rider(name="Bo", plate="5")]

    with pytest.raises(DuplicatePlateError, match=re.escape("5")):
        roster.create_team_entry(display_name="Team A", riders=riders)


def test_create_solo_entry_plate_matching_an_existing_pooled_teams_own_plate_raises() -> None:
    """A solo plate matching a pooled team's derived plate raises."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    riders = [Rider(name="Alex", plate="3"), Rider(name="Bo", plate="9")]
    roster.create_team_entry(display_name="Team A", riders=riders)

    with pytest.raises(DuplicatePlateError, match=re.escape("3")):
        roster.create_solo_entry(name="Cy", plate="3")


# ---------------------------------------------------- next_free_plate


def _register_plates(roster: Roster, plates: Sequence[str]) -> None:
    """Create one relay-or-pooled solo entry per plate in *plates*."""
    for plate in plates:
        roster.create_solo_entry(name="rider", plate=plate)


def test_next_free_plate_on_an_empty_roster_returns_one() -> None:
    """An empty roster's next free plate is "1" (R-20)."""
    roster = Roster()

    assert roster.next_free_plate() == "1"


def test_next_free_plate_is_one_past_the_highest_numeric_plate_in_use() -> None:
    """next_free_plate is (highest numeric plate in use) + 1."""
    roster = Roster()
    _register_plates(roster, ("77", "78", "123", "212"))

    assert roster.next_free_plate() == "213"


def test_next_free_plate_ignores_non_numeric_plates() -> None:
    """A non-numeric plate never influences next_free_plate's result."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)
    roster.create_solo_entry(name="rider", plate="paceCAR")

    assert roster.next_free_plate() == "1"


def test_next_free_plate_considers_both_entry_and_rider_plate_namespaces() -> None:
    """next_free_plate spans both the entry and rider plate spaces."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    riders = [Rider(name="Alex", plate="3"), Rider(name="Bo", plate="50")]
    roster.create_team_entry(display_name="Team A", riders=riders)

    assert roster.next_free_plate() == "51"


# ------------------------------------------------------- update_entry


def test_update_entry_display_name_only_renames_and_leaves_notes_unchanged() -> None:
    """update_entry(display_name=...) renames without touching notes."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")

    roster.update_entry(entry, display_name="Alexandra")

    assert (entry.display_name, entry.notes) == ("Alexandra", "")


def test_update_entry_notes_only_updates_notes_and_leaves_name_unchanged() -> None:
    """update_entry(notes=...) edits notes, name unchanged."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")

    roster.update_entry(entry, notes="late scratch")

    assert (entry.display_name, entry.notes) == ("Alex", "late scratch")


def test_update_entry_appends_an_audit_event_with_the_changed_fields() -> None:
    """update_entry logs one event carrying the changed fields."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")

    roster.update_entry(entry, display_name="Alexandra", notes="late scratch")

    assert roster.audit_log[-1] == AuditEvent(
        action="update_entry",
        payload={"plate": "1", "display_name": "Alexandra", "notes": "late scratch"},
    )


def test_update_entry_unknown_entry_raises_entry_not_found_error() -> None:
    """update_entry on an entry foreign to this roster raises."""
    roster = Roster()
    foreign = Entry(plate="999", display_name="Ghost", type=EntryType.SOLO)

    with pytest.raises(EntryNotFoundError, match=re.escape("not a member")):
        roster.update_entry(foreign, display_name="Nope")


# ------------------------------------------------------- delete_entry


def test_delete_entry_removes_it_from_roster_entries() -> None:
    """delete_entry removes the entry from roster.entries."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")

    roster.delete_entry(entry)

    assert roster.entries == ()


def test_delete_entry_appends_an_audit_event() -> None:
    """delete_entry logs one event naming plate and display_name."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")

    roster.delete_entry(entry)

    assert roster.audit_log[-1] == AuditEvent(
        action="delete_entry", payload={"plate": "1", "display_name": "Alex"}
    )


def test_delete_entry_frees_its_plate_for_reuse() -> None:
    """Deleting an entry lets a later entry reuse its exact plate."""
    roster = Roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    roster.delete_entry(entry)

    reused = roster.create_solo_entry(name="Bo", plate="1")

    assert reused.plate == "1"


def test_delete_entry_unknown_entry_raises_entry_not_found_error() -> None:
    """delete_entry on an entry foreign to this roster raises."""
    roster = Roster()
    foreign = Entry(plate="999", display_name="Ghost", type=EntryType.SOLO)

    with pytest.raises(EntryNotFoundError, match=re.escape("not a member")):
        roster.delete_entry(foreign)


# --------------------------------------------------------- move_rider


def test_move_rider_relocates_rider_between_two_team_entries() -> None:
    """move_rider moves a rider out of one team and into another."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY, max_team_size=3)
    alex = Rider(name="Alex")
    team_a = roster.create_team_entry(
        display_name="Team A", riders=[alex, Rider(name="Bo"), Rider(name="El")], plate="1"
    )
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy"), Rider(name="Do")], plate="2"
    )

    roster.move_rider(alex, to_entry=team_b)

    assert (alex in team_a.riders, alex in team_b.riders) == (False, True)


def test_move_rider_pooled_carries_the_riders_own_plate_along() -> None:
    """A moved rider's own plate travels with them (R-17), unchanged."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Team A",
        riders=[alex, Rider(name="Bo", plate="2"), Rider(name="El", plate="3")],
    )
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="10"), Rider(name="Do", plate="11")]
    )

    roster.move_rider(alex, to_entry=team_b)

    assert alex.plate == "1"


def test_move_rider_pooled_recomputes_both_teams_derived_plate() -> None:
    """A pooled move re-derives both teams' lowest rider plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    alex = Rider(name="Alex", plate="1")
    team_a = roster.create_team_entry(
        display_name="Team A",
        riders=[alex, Rider(name="Bo", plate="2"), Rider(name="El", plate="3")],
    )
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="10"), Rider(name="Do", plate="11")]
    )

    roster.move_rider(alex, to_entry=team_b)

    assert (team_a.plate, team_b.plate) == ("2", "1")


def test_move_rider_appends_an_audit_event_naming_both_entries() -> None:
    """move_rider logs one event naming the rider and both plates."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Team A",
        riders=[alex, Rider(name="Bo", plate="2"), Rider(name="El", plate="3")],
    )
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="10"), Rider(name="Do", plate="11")]
    )

    roster.move_rider(alex, to_entry=team_b)

    assert roster.audit_log[-1] == AuditEvent(
        action="move_rider",
        payload={"rider_name": "Alex", "from_plate": "2", "to_plate": "1"},
    )


def test_move_rider_dropping_source_team_below_minimum_raises_invalid_move_error() -> None:
    """Moving the 2nd rider out of a 2-rider team raises."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(display_name="Team A", riders=[alex, Rider(name="Bo", plate="2")])
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="3"), Rider(name="Do", plate="4")]
    )

    with pytest.raises(InvalidMoveError, match=re.escape("minimum size")):
        roster.move_rider(alex, to_entry=team_b)


def test_move_rider_exceeding_destination_team_max_raises_invalid_move_error() -> None:
    """Moving a rider onto a team already at max_team_size raises."""
    roster = Roster(entry_mode=EntryMode.MIXED, max_team_size=4)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Team A",
        riders=[alex, Rider(name="Bo", plate="2"), Rider(name="El", plate="3")],
    )
    team_b = roster.create_team_entry(
        display_name="Team B",
        riders=[
            Rider(name="Cy", plate="4"),
            Rider(name="Do", plate="5"),
            Rider(name="Fy", plate="6"),
            Rider(name="Gy", plate="7"),
        ],
    )

    with pytest.raises(InvalidMoveError, match=re.escape("max size")):
        roster.move_rider(alex, to_entry=team_b)


def test_move_rider_unknown_rider_raises_rider_not_found_error() -> None:
    """move_rider on a rider foreign to this roster raises."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="3"), Rider(name="Do", plate="4")]
    )
    ghost = Rider(name="Ghost", plate="999")

    with pytest.raises(RiderNotFoundError, match=re.escape("not on any entry")):
        roster.move_rider(ghost, to_entry=team_b)


def test_move_rider_unknown_destination_entry_raises_entry_not_found_error() -> None:
    """move_rider(to_entry=foreign) raises EntryNotFoundError."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(display_name="Team A", riders=[alex, Rider(name="Bo", plate="2")])
    foreign = Entry(plate="999", display_name="Ghost", type=EntryType.TEAM)

    with pytest.raises(EntryNotFoundError, match=re.escape("not a member")):
        roster.move_rider(alex, to_entry=foreign)


def test_move_rider_out_of_a_solo_entry_raises_invalid_move_error() -> None:
    """move_rider on a solo rider raises (solo is 1 rider)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    solo = roster.create_solo_entry(name="Alex", plate="1")
    team_b = roster.create_team_entry(
        display_name="Team B", riders=[Rider(name="Cy", plate="3"), Rider(name="Do", plate="4")]
    )

    with pytest.raises(InvalidMoveError, match=re.escape("team entries")):
        roster.move_rider(solo.riders[0], to_entry=team_b)


def test_move_rider_into_a_solo_entry_raises_invalid_move_error() -> None:
    """move_rider(to_entry=<solo>) raises (solo is 1 rider)."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    alex = Rider(name="Alex", plate="1")
    roster.create_team_entry(display_name="Team A", riders=[alex, Rider(name="Bo", plate="2")])
    solo = roster.create_solo_entry(name="Cy", plate="3")

    with pytest.raises(InvalidMoveError, match=re.escape("team entries")):
        roster.move_rider(alex, to_entry=solo)


# ---------------------------------------------------------- audit_log


def test_audit_log_records_events_in_the_order_the_mutations_happened() -> None:
    """audit_log is append-only, oldest first."""
    roster = Roster()
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_solo_entry(name="Bo", plate="2")

    actions = [event.action for event in roster.audit_log]

    assert actions == ["create_solo_entry", "create_solo_entry"]
