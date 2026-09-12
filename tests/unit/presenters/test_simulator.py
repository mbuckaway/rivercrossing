# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the Rider Simulator presenter, tests-first (R-70).

``SimulatorPresenter`` is pure Python (R-71): it generates placeholder
entries and riders through ``Roster``'s own primitives and replays one
fixed crossing order through a real ``RideEngine``. Every test
therefore drives the real roster and the real engine -- no wx, no fake
view, and no test double is needed.

The clock is the naive fixed instant ``test_results.py``'s
``_engine_source_with_correction`` uses: the engine's lap arithmetic
subtracts naive timestamps, so an aware clock would ``TypeError``
against them. ``gorba_config(min_lap_s=1)`` keeps every simulated lap
well above the short-lap floor, so no crossing is flagged for review.
"""

import re
from dataclasses import replace
from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import gorba_config
from rivercrossing.cards import Shoe
from rivercrossing.ride import RideEngine, RideStatus
from rivercrossing.roster import (
    MIN_TEAM_SIZE,
    Entry,
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
)
from rivercrossing.ui.presenters.simulator import SimOutcome, SimulatorPresenter

# The fixed naive clock every engine here is built with.
_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001

# The seed every generator test pins, so names, sexes and plates are
# reproducible run to run.
_SEED = 42


def _draft(
    *, plate_model: PlateModel = PlateModel.RIDER_POOLED
) -> tuple[SimulatorPresenter, RideEngine, Roster]:
    """Build a DRAFT mixed ride over *plate_model* and its presenter.

    The roster is empty; a test populates it through the presenter or
    the roster's own primitives, then runs the simulation.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=plate_model)
    config = replace(gorba_config(min_lap_s=1), plate_model=plate_model)
    engine = RideEngine(
        config=config,
        shoe=Shoe(
            decks=config.deck_count,
            jokers_per_deck=config.jokers_per_deck,
            seed=20260920,
        ),
        clock=lambda: _START,
        roster=roster,
    )
    return SimulatorPresenter(engine, roster), engine, roster


def _riders_of(roster: Roster) -> list[Rider]:
    """Return every rider in *roster*, in entry creation order."""
    return [rider for entry in roster.entries for rider in entry.riders]


def _entries_of(roster: Roster, entry_type: EntryType) -> list[Entry]:
    """Return every entry of *entry_type*, in creation order."""
    return [entry for entry in roster.entries if entry.type is entry_type]


def _signature(roster: Roster) -> list[tuple[str, list[tuple[str, str, str | None, str | None]]]]:
    """Return each entry's name and riders, for a compare."""
    return [
        (
            entry.display_name,
            [(r.first_name, r.last_name, r.plate, r.sex) for r in entry.riders],
        )
        for entry in roster.entries
    ]


# ------------------------------------------------------- generate_teams


def test_generate_teams_three_creates_named_team_entries() -> None:
    """Three teams land as TEAM-0001..0003, in that order."""
    presenter, _engine, roster = _draft()

    presenter.generate_teams(3)

    assert [entry.display_name for entry in roster.entries] == [
        "TEAM-0001",
        "TEAM-0002",
        "TEAM-0003",
    ]
    assert {entry.type for entry in roster.entries} == {EntryType.TEAM}


def test_generate_teams_zero_creates_no_entries() -> None:
    """A zero count is a no-op, not an error."""
    presenter, _engine, roster = _draft()

    presenter.generate_teams(0)

    assert roster.entries == ()


def test_generate_teams_one_creates_the_first_named_team() -> None:
    """The smallest count names the first team TEAM-0001."""
    presenter, _engine, roster = _draft()

    presenter.generate_teams(1)

    assert [entry.display_name for entry in roster.entries] == ["TEAM-0001"]


# ------------------------------------------------------ generate_riders


def test_generate_riders_mixed_pooled_splits_solo_and_team_riders() -> None:
    """Four solo entries plus six riders split across two teams."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(10, 2, 4, seed=_SEED)

    assert len(_entries_of(roster, EntryType.SOLO)) == 4
    assert [len(entry.riders) for entry in _entries_of(roster, EntryType.TEAM)] == [
        3,
        3,
    ]
    assert len(_riders_of(roster)) == 10


def test_generate_riders_keeps_every_team_between_the_size_bounds() -> None:
    """Each generated team clears the floor and the ceiling."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(10, 2, 4, seed=_SEED)

    sizes = [len(entry.riders) for entry in _entries_of(roster, EntryType.TEAM)]
    assert min(sizes) >= MIN_TEAM_SIZE
    assert max(sizes) <= roster.max_team_size


def test_generate_riders_names_are_unique_and_numbered() -> None:
    """Names run FIRSTNAME-0001..0010 with no repeat."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(10, 2, 4, seed=_SEED)

    expected_numbers = list(range(1, 11))
    assert sorted(rider.first_name for rider in _riders_of(roster)) == [
        f"FIRSTNAME-{number:04d}" for number in expected_numbers
    ]
    assert sorted(rider.last_name for rider in _riders_of(roster)) == [
        f"LASTNAME-{number:04d}" for number in expected_numbers
    ]


def test_generate_riders_sexes_are_male_or_female() -> None:
    """Every generated rider carries the "M"/"F" the engine expects."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(10, 2, 4, seed=_SEED)

    assert {rider.sex for rider in _riders_of(roster)} <= {"M", "F"}


def test_generate_riders_same_seed_generates_identical_rosters() -> None:
    """One seed reproduces names, plates and sexes exactly."""
    first, _first_engine, first_roster = _draft()
    second, _second_engine, second_roster = _draft()

    first.generate_riders(10, 2, 4, seed=_SEED)
    second.generate_riders(10, 2, 4, seed=_SEED)

    assert _signature(first_roster) == _signature(second_roster)


def test_generate_riders_solo_plates_precede_team_rider_plates() -> None:
    """The solo riders come first, so their plates are lower."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(10, 2, 4, seed=_SEED)

    solo_plates = [int(entry.plate) for entry in _entries_of(roster, EntryType.SOLO)]
    team_plates = [
        int(rider.plate) for entry in _entries_of(roster, EntryType.TEAM) for rider in entry.riders
    ]
    assert solo_plates == sorted(solo_plates)
    assert max(solo_plates) < min(team_plates)


def test_generate_riders_uses_existing_teams_without_creating_more() -> None:
    """Pre-created teams are filled, never duplicated."""
    presenter, _engine, roster = _draft()
    presenter.generate_teams(2)

    presenter.generate_riders(8, 2, 2, seed=_SEED)

    assert len(roster.entries) == 4
    assert [entry.display_name for entry in _entries_of(roster, EntryType.TEAM)] == [
        "TEAM-0001",
        "TEAM-0002",
    ]


def test_generate_riders_relay_leaves_team_riders_plateless() -> None:
    """A relay team's riders carry no plate: the entry owns it."""
    presenter, _engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)

    presenter.generate_riders(6, 2, 2, seed=_SEED)

    team_riders = [
        rider for entry in _entries_of(roster, EntryType.TEAM) for rider in entry.riders
    ]
    assert [rider.plate for rider in team_riders] == [None, None, None, None]


@pytest.mark.parametrize(
    ("total", "teams", "solo", "per_team"),
    [
        pytest.param(2, 1, 0, (2,), id="floor-one-team"),
        pytest.param(4, 2, 0, (2, 2), id="floor-two-teams"),
        pytest.param(7, 2, 1, (3, 3), id="floor-plus-one"),
        pytest.param(8, 2, 0, (4, 4), id="ceiling-two-teams"),
        pytest.param(6, 3, 0, (2, 2, 2), id="floor-three-teams"),
        pytest.param(12, 3, 0, (4, 4, 4), id="ceiling-three-teams"),
    ],
)
def test_generate_riders_accepts_every_team_rider_boundary(  # noqa: PLR0913, PLR0917
    total: int, teams: int, solo: int, per_team: tuple[int, ...]
) -> None:
    """The team-rider floor and ceiling are both accepted."""
    presenter, _engine, roster = _draft()

    presenter.generate_riders(total, teams, solo, seed=_SEED)

    assert [len(entry.riders) for entry in _entries_of(roster, EntryType.TEAM)] == list(per_team)
    assert len(_riders_of(roster)) == total


@pytest.mark.parametrize(
    ("total", "teams", "solo", "message"),
    [
        pytest.param(0, 2, 0, "total must be at least 1", id="total-at-floor"),
        pytest.param(-1, 2, 0, "total must be at least 1", id="total-below-floor"),
        pytest.param(10, 0, 0, "teams must be at least 1", id="teams-at-floor"),
        pytest.param(10, -1, 0, "teams must be at least 1", id="teams-below-floor"),
        pytest.param(10, 2, -1, "solo must be between 0 and 10", id="solo-below-zero"),
        pytest.param(10, 2, 11, "solo must be between 0 and 10", id="solo-above-total"),
        pytest.param(
            10,
            2,
            10,
            "team riders must be between 4 and 8, got 0",
            id="team-riders-at-zero",
        ),
        pytest.param(
            10,
            2,
            7,
            "team riders must be between 4 and 8, got 3",
            id="team-riders-below-floor",
        ),
        pytest.param(
            10,
            2,
            1,
            "team riders must be between 4 and 8, got 9",
            id="team-riders-above-ceiling",
        ),
    ],
)
def test_generate_riders_refuses_input_outside_the_generator_bounds(  # noqa: PLR0913, PLR0917
    total: int, teams: int, solo: int, message: str
) -> None:
    """Each bound refuses with its own message, and writes nothing."""
    presenter, _engine, roster = _draft()

    with pytest.raises(ValueError, match=re.escape(message)):
        presenter.generate_riders(total, teams, solo, seed=_SEED)

    assert roster.entries == ()


# ------------------------------------------------ roster_changed


def test_roster_changed_starts_false_before_any_generation() -> None:
    """A fresh presenter has committed nothing to the roster yet."""
    presenter, _engine, _roster = _draft()

    assert presenter.roster_changed is False


def test_roster_changed_is_true_after_generate_teams() -> None:
    """generate_teams commits its teams, so the roster must persist."""
    presenter, _engine, _roster = _draft()

    presenter.generate_teams(1)

    assert presenter.roster_changed is True


def test_roster_changed_is_true_after_generate_riders() -> None:
    """generate_riders commits riders; persist the roster."""
    presenter, _engine, _roster = _draft()

    presenter.generate_riders(2, 1, 0, seed=_SEED)

    assert presenter.roster_changed is True


def test_roster_changed_is_true_after_generate_solo_riders() -> None:
    """generate_solo_riders commits riders; persist the roster."""
    presenter, _engine, _roster = _draft()

    presenter.generate_solo_riders(1, seed=_SEED)

    assert presenter.roster_changed is True


# ------------------------------------------------ generate_solo_riders


def test_generate_solo_riders_five_creates_five_numbered_solo_entries() -> None:
    """Five solo entries number FIRSTNAME-0001..0005."""
    presenter, _engine, roster = _draft()

    presenter.generate_solo_riders(5, seed=_SEED)

    entries = _entries_of(roster, EntryType.SOLO)
    assert [entry.display_name for entry in entries] == [
        f"FIRSTNAME-{number:04d} LASTNAME-{number:04d}" for number in range(1, 6)
    ]
    assert {entry.type for entry in roster.entries} == {EntryType.SOLO}


def test_generate_solo_riders_plates_are_present_and_unique() -> None:
    """Every solo entry claims its own non-empty plate."""
    presenter, _engine, roster = _draft()

    presenter.generate_solo_riders(5, seed=_SEED)

    plates = [entry.plate for entry in roster.entries]
    assert all(plate for plate in plates)
    assert len(set(plates)) == 5


def test_generate_solo_riders_sexes_are_male_or_female() -> None:
    """Every generated solo rider carries "M" or "F"."""
    presenter, _engine, roster = _draft()

    presenter.generate_solo_riders(5, seed=_SEED)

    assert {rider.sex for rider in _riders_of(roster)} <= {"M", "F"}


def test_generate_solo_riders_same_seed_generates_identical_rosters() -> None:
    """One seed reproduces names, plates and sexes exactly."""
    first, _first_engine, first_roster = _draft()
    second, _second_engine, second_roster = _draft()

    first.generate_solo_riders(5, seed=_SEED)
    second.generate_solo_riders(5, seed=_SEED)

    assert _signature(first_roster) == _signature(second_roster)


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        pytest.param(1, 1, id="min"),
        pytest.param(2, 2, id="min-plus-one"),
        pytest.param(5, 5, id="many"),
    ],
)
def test_generate_solo_riders_accepts_every_total_boundary(total: int, expected: int) -> None:
    """The total floor and above each create that many solo entries."""
    presenter, _engine, roster = _draft()

    presenter.generate_solo_riders(total, seed=_SEED)

    assert len(_entries_of(roster, EntryType.SOLO)) == expected


@pytest.mark.parametrize(
    ("total", "message"),
    [
        pytest.param(0, "total must be at least 1", id="total-at-floor"),
        pytest.param(-1, "total must be at least 1", id="total-below-floor"),
    ],
)
def test_generate_solo_riders_refuses_total_below_one(total: int, message: str) -> None:
    """A below-one total refuses first, writing nothing."""
    presenter, _engine, roster = _draft()

    with pytest.raises(ValueError, match=re.escape(message)):
        presenter.generate_solo_riders(total, seed=_SEED)

    assert roster.entries == ()


@given(
    total=st.integers(min_value=1, max_value=20),
    seed=st.integers(min_value=-1000, max_value=1000),
)
def test_generate_solo_riders_given_any_total_creates_that_many_unique_plates(
    total: int, seed: int
) -> None:
    """Property: solo generation plates each entry once (T-7)."""
    presenter, _engine, roster = _draft()

    presenter.generate_solo_riders(total, seed=seed)

    plates = [entry.plate for entry in roster.entries]
    assert len(plates) == total
    assert len(set(plates)) == total


# ------------------------------------------------------------- validate


@pytest.mark.parametrize(
    ("laps", "interval_minutes", "expected"),
    [
        pytest.param(0, 1, "laps must be at least 1", id="laps-below-floor"),
        pytest.param(-1, 1, "laps must be at least 1", id="laps-negative"),
        pytest.param(1, 0, "interval must be at least 1 minute", id="interval-below-floor"),
        pytest.param(1, -5, "interval must be at least 1 minute", id="interval-negative"),
        pytest.param(
            0,
            0,
            "laps must be at least 1; interval must be at least 1 minute",
            id="both-below-floor",
        ),
        pytest.param(1, 1, None, id="both-at-floor"),
        pytest.param(2, 1, None, id="laps-above-floor"),
        pytest.param(1, 2, None, id="interval-above-floor"),
    ],
)
def test_validate_returns_the_joined_refusal_or_none(
    laps: int, interval_minutes: int, expected: str | None
) -> None:
    """A below-floor value is refused; both at the floor is accepted."""
    presenter, _engine, _roster = _draft()

    result = presenter.validate(laps=laps, interval_minutes=interval_minutes)

    assert result == expected


@given(
    laps=st.integers(min_value=-1000, max_value=1000),
    interval_minutes=st.integers(min_value=-1000, max_value=1000),
)
def test_validate_returns_none_exactly_when_both_values_are_positive(
    laps: int, interval_minutes: int
) -> None:
    """Property: validate() is clean iff both bounds clear 1 (T-7)."""
    presenter, _engine, _roster = _draft()

    result = presenter.validate(laps=laps, interval_minutes=interval_minutes)

    assert (result is None) == (laps >= 1 and interval_minutes >= 1)
    assert result is None or "must be at least 1" in result


# ------------------------------------------------------- run_simulation


def test_run_simulation_records_one_crossing_per_rider_per_lap() -> None:
    """Two laps over ten riders record exactly twenty crossings."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    outcome = presenter.run_simulation(laps=2, interval_minutes=1)

    assert outcome == SimOutcome(cancelled=False, recorded=20, blocked=None)
    assert len(engine.crossings) == 20


def test_run_simulation_leaves_the_ride_running_and_stopped() -> None:
    """The ride ends RUNNING with plate entry locked by Stop."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=1, interval_minutes=1)

    assert engine.state is RideStatus.RUNNING
    assert engine.stopped is True


def test_run_simulation_solo_lap_times_match_the_interval() -> None:
    """A solo entry's later laps are exactly one interval apart."""
    presenter, engine, roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)
    solo = _entries_of(roster, EntryType.SOLO)[0]

    presenter.run_simulation(laps=3, interval_minutes=1)

    lap_times = engine.lap_times(solo.plate)
    assert len(lap_times) == 3
    assert 60.0 < lap_times[0] < 120.0
    assert lap_times[1:] == (60.0, 60.0)


def test_run_simulation_reuses_one_ascending_order_for_every_lap() -> None:
    """Every lap crosses in the same order, its own clock rising."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=3, interval_minutes=1)

    plates = [crossing.rider_plate for crossing in engine.crossings]
    assert plates[0:10] == plates[10:20] == plates[20:30]
    first_lap = [crossing.crossed_at for crossing in engine.crossings[:10]]
    assert first_lap == sorted(first_lap)


def test_run_simulation_reports_monotonic_progress_to_the_total() -> None:
    """on_progress counts every crossing once, up to the total."""
    presenter, _engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)
    calls: list[tuple[int, int]] = []

    presenter.run_simulation(
        laps=2,
        interval_minutes=1,
        on_progress=lambda done, total: calls.append((done, total)),
    )

    assert calls[-1] == (20, 20)
    assert [done for done, _total in calls] == list(range(1, 21))


def test_run_simulation_uncancelled_predicate_runs_to_the_end() -> None:
    """A cancel check that never fires does not cut the run short."""
    presenter, _engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    outcome = presenter.run_simulation(laps=1, interval_minutes=1, is_cancelled=lambda: False)

    assert outcome == SimOutcome(cancelled=False, recorded=10, blocked=None)


def test_run_simulation_cancel_stops_after_the_first_crossing() -> None:
    """A firing cancel check ends the run stopped, one crossing in."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    outcome = presenter.run_simulation(laps=2, interval_minutes=1, is_cancelled=lambda: True)

    assert outcome == SimOutcome(cancelled=True, recorded=1, blocked=None)
    assert engine.stopped is True
    assert engine.state is RideStatus.RUNNING


def test_run_simulation_empty_roster_is_blocked_with_the_engine_reason() -> None:
    """An empty roster blocks the start and records nothing."""
    presenter, engine, _roster = _draft()

    outcome = presenter.run_simulation(laps=1, interval_minutes=1)

    assert outcome == SimOutcome(cancelled=False, recorded=0, blocked=("roster has no riders",))
    assert engine.state is RideStatus.DRAFT


def test_run_simulation_team_below_the_size_floor_is_blocked() -> None:
    """A team under the floor blocks the start, naming the team."""
    presenter, _engine, roster = _draft()
    team = roster.create_empty_team(display_name="TEAM-0001")
    roster.add_rider_to_team(
        Rider(first_name="A", last_name="B", plate=roster.next_free_plate(), sex="M"),
        to_entry=team,
    )

    outcome = presenter.run_simulation(laps=1, interval_minutes=1)

    assert outcome.blocked == (f"{team.plate}: team size must be at least {MIN_TEAM_SIZE}, got 1",)
    assert outcome.recorded == 0


def test_run_simulation_relay_records_crossings_under_the_entry_plate() -> None:
    """A relay crossing records on the team's plate, not a rider's."""
    presenter, engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    outcome = presenter.run_simulation(laps=1, interval_minutes=1)

    assert outcome == SimOutcome(cancelled=False, recorded=6, blocked=None)
    assert {crossing.entry_id for crossing in engine.crossings} == {
        entry.plate for entry in roster.entries
    }
