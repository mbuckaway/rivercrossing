# SPDX-License-Identifier: GPL-3.0-only
"""Hypothesis property suite for Roster (E3.1.1).

module-skeletons.md S5 names this directory for property tests; the
task-briefs.md E3.1.1 brief names its own property directly: "any
sequence of valid mutations preserves plate uniqueness and
team-size bounds." A sequence mixes solo/team creates and rider
moves with mostly-independent, sometimes-colliding plate values --
Hypothesis will generate both plate collisions (rejected by
DuplicatePlateError) and team sizes outside 2..max_team_size
(rejected by TeamSizeError); ``_apply`` treats every
:class:`RosterError` as "this particular mutation was invalid," a
no-op, since the property is about mutations the roster *accepts*,
not about every generated action succeeding.

Example counts stay modest to match tests/property/test_cards_
properties.py's own budget: each example replays up to 15 actions
against a fresh Roster, well inside a few seconds for the whole file.

E3.2's 2026-08-09 follow-on decision relaxes the team-size lower
bound this suite asserts: see ``_assert_plate_and_team_size_
invariants``'s own docstring for why it is now 1, not 2.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.roster import (
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
    RosterError,
)

_NAME = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)
_PLATE = st.integers(min_value=1, max_value=12).map(str)
_TEAM_RIDER_COUNT = st.integers(min_value=1, max_value=5)
_INDEX = st.integers(min_value=0, max_value=20)


@dataclass(frozen=True)
class _CreateSolo:
    """A generated create_solo_entry() call."""

    name: str
    plate: str


@dataclass(frozen=True)
class _CreateTeam:
    """A generated create_team_entry() call (size may be invalid)."""

    display_name: str
    rider_names: tuple[str, ...]
    plates: tuple[str, ...]


@dataclass(frozen=True)
class _MoveRider:
    """A generated move_rider() call, indices wrap onto entries."""

    from_index: int
    rider_index: int
    to_index: int


@st.composite
def _create_team_action(draw: st.DrawFn) -> _CreateTeam:
    """Build one _CreateTeam action with a random rider count."""
    rider_count = draw(_TEAM_RIDER_COUNT)
    names = tuple(draw(_NAME) for _ in range(rider_count))
    plates = tuple(draw(_PLATE) for _ in range(rider_count))
    return _CreateTeam(display_name=draw(_NAME), rider_names=names, plates=plates)


_ACTION = st.one_of(
    st.builds(_CreateSolo, name=_NAME, plate=_PLATE),
    _create_team_action(),
    st.builds(_MoveRider, from_index=_INDEX, rider_index=_INDEX, to_index=_INDEX),
)


def _apply_move(roster: Roster, action: _MoveRider) -> None:
    """Replay *action* against whichever live entries it wraps onto."""
    entries = roster.entries
    if not entries:
        return
    from_entry = entries[action.from_index % len(entries)]
    if not from_entry.riders:
        return
    rider = from_entry.riders[action.rider_index % len(from_entry.riders)]
    to_entry = entries[action.to_index % len(entries)]
    roster.move_rider(rider, to_entry=to_entry)


def _apply(roster: Roster, action: _CreateSolo | _CreateTeam | _MoveRider) -> None:
    """Apply *action*, treating a rejected mutation as a no-op."""
    try:
        if isinstance(action, _CreateSolo):
            roster.create_solo_entry(name=action.name, plate=action.plate)
        elif isinstance(action, _CreateTeam):
            riders = [
                Rider(name=name, plate=plate)
                for name, plate in zip(action.rider_names, action.plates, strict=True)
            ]
            roster.create_team_entry(display_name=action.display_name, riders=riders)
        else:
            _apply_move(roster, action)
    except RosterError:
        return


def _run_sequence(roster: Roster, actions: list[_CreateSolo | _CreateTeam | _MoveRider]) -> None:
    """Replay *actions* against *roster* in order."""
    for action in actions:
        _apply(roster, action)


def _assert_plate_and_team_size_invariants(roster: Roster) -> None:
    """Assert no *cross-entry* plate repeats; sizes in bounds.

    Each entry contributes its own plate plus its riders' plates as
    one set, deduplicating a pooled entry's derived plate against
    its own adopted rider's plate (S1's intended, not a collision);
    ``claimed`` must stay disjoint across *different* entries.

    A team's lower bound is 1, not ``MIN_TEAM_SIZE`` (2): the
    2026-08-09 follow-on decision allows a transient size-1 team in
    DRAFT (move_rider dissolves it outright at size 0, so it never
    lingers in ``roster.entries``); the 2-rider floor moves to
    ``validate_for_start()``, a start-time check this sequence-replay
    property does not call. The upper bound is unchanged.
    """
    claimed: set[str] = set()
    for entry in roster.entries:
        entry_plates = {entry.plate} | {
            rider.plate for rider in entry.riders if rider.plate is not None
        }
        assert claimed.isdisjoint(entry_plates)
        claimed |= entry_plates
        if entry.type is EntryType.SOLO:
            assert entry.team_size == 1
        else:
            assert 1 <= entry.team_size <= roster.max_team_size


@given(actions=st.lists(_ACTION, max_size=15))
@settings(max_examples=100, deadline=None)
def test_roster_any_valid_mutation_sequence_preserves_plate_and_size_invariants(
    actions: list[_CreateSolo | _CreateTeam | _MoveRider],
) -> None:
    """Any accepted mutation sequence keeps plates/sizes valid."""
    roster = Roster(
        entry_mode=EntryMode.MIXED, max_team_size=6, plate_model=PlateModel.RIDER_POOLED
    )

    _run_sequence(roster, actions)

    _assert_plate_and_team_size_invariants(roster)


def _fill_relay_plates(roster: Roster, plates: list[int]) -> None:
    """Register one relay solo entry per plate in *plates*."""
    for plate in plates:
        roster.create_solo_entry(name="rider", plate=str(plate))


@given(plates=st.lists(st.integers(min_value=1, max_value=500), max_size=20, unique=True))
@settings(max_examples=100, deadline=None)
def test_roster_next_free_plate_never_collides_with_a_plate_already_in_use(
    plates: list[int],
) -> None:
    """next_free_plate() never repeats a plate already in use."""
    roster = Roster(plate_model=PlateModel.TEAM_RELAY)
    _fill_relay_plates(roster, plates)

    candidate = roster.next_free_plate()

    assert candidate not in {str(plate) for plate in plates}
