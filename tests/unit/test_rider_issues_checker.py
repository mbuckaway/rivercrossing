# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.rider_issues.

``rider_issues`` is the roster's defect report: the stable, machine-
sortable list of every rider issue a roster still carries before a
ride starts. The five kinds and their report order are the contract:

    1. team-of-one      (TEAM entry below MIN_TEAM_SIZE riders)
    2. missing-name     (rider with an empty full_name)
    3. missing-number   (rider_pooled rider with a blank/None plate)
    4. duplicate-name   (case/whitespace-duplicate rider name)
    5. duplicate-number (one plate value claimed twice)

Written FIRST, against a module that does not exist yet: this file is
red until rivercrossing/rider_issues.py lands.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.rider_issues import RiderIssue, rider_issues
from rivercrossing.roster import Entry, EntryMode, EntryType, PlateModel, Rider, Roster

_CANONICAL_KINDS = (
    "team-of-one",
    "missing-name",
    "missing-number",
    "duplicate-name",
    "duplicate-number",
)


def _solo_entry(plate: str | None, *, first_name: str, last_name: str) -> Entry:
    """Return one solo entry whose rider carries *plate* and name."""
    rider = Rider(first_name=first_name, last_name=last_name, plate=plate)
    return Entry(
        plate=plate if plate is not None else "",
        display_name=rider.full_name,
        type=EntryType.SOLO,
        riders=[rider],
    )


def _team_riders(count: int) -> list[Rider]:
    """Return *count* distinct, fully-formed riders (plates "1"..)."""
    return [Rider(first_name=f"Rider {i}", last_name="", plate=str(i + 1)) for i in range(count)]


# ------------------------------ empty roster


def test_rider_issues_empty_roster_returns_empty_tuple() -> None:
    """A fresh Roster carries no issues at all."""
    roster = Roster()

    assert rider_issues(roster) == ()


# ------------------------------ team-of-one


def test_rider_issues_size_one_team_reports_team_of_one() -> None:
    """A transient size-1 team is one team-of-one issue, rider=None."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    entry = roster.create_team_entry_of_one(
        display_name="Trail Blazers",
        rider=Rider(first_name="Sam", last_name="Ellis", plate="7"),
    )

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(
            entry=entry,
            rider=None,
            kind="team-of-one",
            message="team size must be at least 2, got 1",
        ),
    )


@pytest.mark.parametrize("team_size", [2, 3, 4])  # min, min+1, default max
def test_rider_issues_team_at_or_above_min_reports_no_team_of_one(team_size: int) -> None:
    """A team with MIN_TEAM_SIZE..max riders clears the floor."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    roster.create_team_entry(display_name="Trail Blazers", riders=_team_riders(team_size))

    assert rider_issues(roster) == ()


# ------------------------------ missing-name


@pytest.mark.parametrize(
    ("first_name", "last_name"),
    [
        ("", ""),
        ("   ", ""),
        ("", "\t"),
        ("  ", "   "),
    ],
)
def test_rider_issues_blank_name_rider_reports_missing_name(
    first_name: str, last_name: str
) -> None:
    """A rider whose full_name is empty is one missing-name issue."""
    roster = Roster()
    entry = roster.create_solo_entry(first_name=first_name, last_name=last_name, plate="1")
    rider = entry.riders[0]

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(entry=entry, rider=rider, kind="missing-name", message="missing name"),
    )


# ------------------------------ missing-number


@pytest.mark.parametrize("plate", [None, "", "   "])
def test_rider_issues_pooled_rider_with_blank_plate_reports_missing_number(
    plate: str | None,
) -> None:
    """A pooled rider with a blank plate is missing-number."""
    roster = Roster(plate_model=PlateModel.RIDER_POOLED)
    entry = Entry(
        plate=plate if plate is not None else "",
        display_name="Alex Roy",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Alex", last_name="Roy", plate=plate)],
    )
    roster.load_entries([entry])
    rider = entry.riders[0]

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(entry=entry, rider=rider, kind="missing-number", message="missing number"),
    )


def test_rider_issues_relay_rider_with_none_plate_is_not_flagged() -> None:
    """A relay rider is legitimately plateless: no missing-number."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)
    entry = Entry(
        plate="7",
        display_name="Alex Roy",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Alex", last_name="Roy", plate=None)],
    )
    roster.load_entries([entry])

    assert rider_issues(roster) == ()


# ------------------------------ duplicate-name


def test_rider_issues_case_whitespace_duplicate_name_reports_one_duplicate() -> None:
    """Two case/whitespace-duplicate names yield one issue."""
    roster = Roster()
    first = _solo_entry("1", first_name="Mary Anne", last_name="Knibbe")
    dupe = _solo_entry("2", first_name="  mary   anne ", last_name=" KNIBBE ")
    roster.load_entries([first, dupe])
    dupe_rider = dupe.riders[0]

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(
            entry=dupe,
            rider=dupe_rider,
            kind="duplicate-name",
            message=f"duplicate rider name {dupe_rider.full_name}",
        ),
    )


# ------------------------------ duplicate-number


def test_rider_issues_two_pooled_entries_sharing_plate_report_duplicate_number() -> None:
    """Two pooled riders claiming one plate: one duplicate-number."""
    roster = Roster(plate_model=PlateModel.RIDER_POOLED)
    first = _solo_entry("7", first_name="Sam", last_name="Ellis")
    dupe = _solo_entry("7", first_name="Alex", last_name="Roy")
    roster.load_entries([first, dupe])
    dupe_rider = dupe.riders[0]

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(
            entry=dupe,
            rider=dupe_rider,
            kind="duplicate-number",
            message="duplicate number 7",
        ),
    )


def test_rider_issues_two_relay_entries_sharing_plate_report_duplicate_number() -> None:
    """Two relay entries claiming one plate: one duplicate-number."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)
    first = Entry(
        plate="7",
        display_name="Sam Ellis",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Sam", last_name="Ellis", plate=None)],
    )
    dupe = Entry(
        plate="7",
        display_name="Alex Roy",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Alex", last_name="Roy", plate=None)],
    )
    roster.load_entries([first, dupe])

    issues = rider_issues(roster)

    assert issues == (
        RiderIssue(entry=dupe, rider=None, kind="duplicate-number", message="duplicate number 7"),
    )


def test_rider_issues_relay_blank_entry_plate_is_not_flagged() -> None:
    """A blank relay entry plate is not a duplicate-number candidate."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)
    entry = Entry(
        plate="   ",
        display_name="Sam Ellis",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Sam", last_name="Ellis", plate=None)],
    )
    roster.load_entries([entry])

    assert rider_issues(roster) == ()


# ------------------------------ stable ordering


def test_rider_issues_reports_kinds_in_canonical_order() -> None:
    """Every kind reports, in team-of-one … duplicate-number order."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    team_of_one = Entry(
        plate="1",
        display_name="Lone Wolf",
        type=EntryType.TEAM,
        riders=[Rider(first_name="Sam", last_name="Ellis", plate="1")],
    )
    missing_name = Entry(
        plate="2",
        display_name="",
        type=EntryType.SOLO,
        riders=[Rider(first_name="", last_name="", plate="2")],
    )
    missing_number = Entry(
        plate="",
        display_name="No Plate",
        type=EntryType.SOLO,
        riders=[Rider(first_name="No", last_name="Plate", plate=None)],
    )
    first = Entry(
        plate="5",
        display_name="Mary Anne Knibbe",
        type=EntryType.SOLO,
        riders=[Rider(first_name="Mary Anne", last_name="Knibbe", plate="5")],
    )
    dupe = Entry(
        plate="5",
        display_name="mary anne knibbe",
        type=EntryType.SOLO,
        riders=[Rider(first_name="mary anne", last_name="knibbe", plate="5")],
    )
    roster.load_entries([team_of_one, missing_name, missing_number, first, dupe])

    kinds = [issue.kind for issue in rider_issues(roster)]

    assert kinds == list(_CANONICAL_KINDS)


# ------------------------------ property tests


@st.composite
def _rosters(draw: st.DrawFn) -> Roster:
    """Build an arbitrary in-memory roster for the order check."""
    plate_model = draw(st.sampled_from((PlateModel.RIDER_POOLED, PlateModel.TEAM_RELAY)))
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=plate_model)
    numeric_plate = st.text(
        alphabet=st.characters(min_codepoint=48, max_codepoint=57),
        min_size=1,
        max_size=3,
    )
    name = st.text(min_size=0, max_size=10)
    entries: list[Entry] = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        entry_type = draw(st.sampled_from((EntryType.SOLO, EntryType.TEAM)))
        rider_count = (
            1 if entry_type is EntryType.SOLO else draw(st.integers(min_value=1, max_value=3))
        )
        riders = [
            Rider(
                first_name=draw(name),
                last_name=draw(name),
                plate=draw(st.one_of(st.none(), numeric_plate)),
            )
            for _ in range(rider_count)
        ]
        entries.append(
            Entry(
                plate=draw(numeric_plate),
                display_name=draw(name),
                type=entry_type,
                riders=riders,
            )
        )
    roster.load_entries(entries)
    return roster


@given(_rosters())
@settings(max_examples=200, deadline=None)
def test_rider_issues_kinds_are_always_in_canonical_order(roster: Roster) -> None:
    """The report's kinds never leave the canonical ordering."""
    kinds = [issue.kind for issue in rider_issues(roster)]

    assert kinds == sorted(kinds, key=_CANONICAL_KINDS.index)
