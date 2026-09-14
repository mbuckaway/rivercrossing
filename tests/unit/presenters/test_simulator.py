# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the Rider Simulator presenter, tests-first (R-70).

``SimulatorPresenter`` is pure Python (R-71): it generates placeholder
entries and riders through ``Roster``'s own primitives and replays one
fixed crossing order through a real ``RideEngine``. Every test
therefore drives the real roster and the real engine -- no wx, no fake
view, and no test double is needed.

Phase 2 rewrote the pooled plate rule: each entry crosses once a lap
under one plate (a pooled team's representative rotates round-robin),
so a team's lap count equals a solo's.

Phase 4 models the rider *wave*. The gun is back-dated so the final
wave's last rider lands on the live clock, lap ``L`` (0-based) opens
at ``actual_start + interval * (L + 1)`` -- one full interval after
the gun, so no derived lap time is ever ``00:00:00`` -- and each
wave's whole field crosses inside the first half-interval, leaving
the second half as the gap before the next wave. ``elapsed()``
therefore reads the whole race once a run finishes, as the main
screen's clock is meant to.

The clock is the naive fixed instant ``test_results.py``'s
``_engine_source_with_correction`` uses: the engine's lap arithmetic
subtracts naive timestamps, so an aware clock would ``TypeError``
against them. ``gorba_config(min_lap_s=1)`` keeps every simulated lap
well above the short-lap floor, so no crossing is flagged for review.
"""

import re
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import gorba_config
from rivercrossing.cards import Shoe
from rivercrossing.ride import Crossing, RideEngine, RideStatus
from rivercrossing.roster import (
    DEFAULT_MAX_TEAM_SIZE,
    MIN_TEAM_SIZE,
    Entry,
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
)
from rivercrossing.ui.presenters.simulator import (
    SimOutcome,
    SimulatorPresenter,
    _lap_offsets,
    _plates_to_record,
    check_message,
    default_interval_minutes,
    resolve_solo,
)

# The fixed naive clock every engine here is built with.
_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001

# Phase 4 back-dates the gun and replays history through a fixed clock,
# so ``elapsed()`` is exact; the tolerance only absorbs the wall time a
# run takes when a caller-injected clock really ticks.
_ELAPSED_TOLERANCE_S = 5.0

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


def _actual_start_of(engine: RideEngine) -> datetime:
    """Return the instant the engine's own start event recorded."""
    return datetime.fromisoformat(str(engine.events[0].payload["actual_start"]))


def _laps_of(engine: RideEngine) -> list[list[Crossing]]:
    """Return each lap's crossings, oldest first, one list per lap."""
    laps: dict[int, list[Crossing]] = {}
    for crossing in engine.crossings:
        laps.setdefault(crossing.seq, []).append(crossing)
    return [laps[seq] for seq in sorted(laps)]


def _waves_of(engine: RideEngine) -> list[tuple[datetime, datetime]]:
    """Return each lap's (first, last) crossing, oldest lap first."""
    return [
        (
            min(crossing.crossed_at for crossing in lap),
            max(crossing.crossed_at for crossing in lap),
        )
        for lap in _laps_of(engine)
    ]


# --------------------------------------------------------- resolve_solo


@pytest.mark.parametrize(
    ("riders", "teams", "expected"),
    [
        pytest.param(79, 40, None, id="floor-minus-one"),
        pytest.param(80, 40, 0, id="floor"),
        pytest.param(81, 40, 0, id="floor-plus-one"),
        pytest.param(159, 40, 0, id="ceiling-minus-one"),
        pytest.param(160, 40, 0, id="ceiling"),
        pytest.param(161, 40, 1, id="ceiling-plus-one"),
        pytest.param(175, 40, 15, id="dialog-defaults"),
        pytest.param(800, 40, 640, id="well-above-ceiling"),
        pytest.param(1, 1, None, id="one-team-floor-minus-one"),
        pytest.param(2, 1, 0, id="one-team-floor"),
        pytest.param(3, 1, 0, id="one-team-mid"),
        pytest.param(4, 1, 0, id="one-team-ceiling"),
        pytest.param(5, 1, 1, id="one-team-ceiling-plus-one"),
    ],
)
def test_resolve_solo_given_a_rider_count_returns_its_boundary_value(
    riders: int, teams: int, expected: int | None
) -> None:
    """T-4: each rider boundary resolves to its own solo."""
    result = resolve_solo(riders, teams)

    assert result == expected


@pytest.mark.parametrize(
    ("riders", "teams", "min_team_size", "max_team_size", "expected"),
    [
        pytest.param(8, 3, 3, 5, None, id="custom-floor-minus-one"),
        pytest.param(9, 3, 3, 5, 0, id="custom-floor"),
        pytest.param(10, 3, 3, 5, 0, id="custom-floor-plus-one"),
        pytest.param(14, 3, 3, 5, 0, id="custom-ceiling-minus-one"),
        pytest.param(15, 3, 3, 5, 0, id="custom-ceiling"),
        pytest.param(16, 3, 3, 5, 1, id="custom-ceiling-plus-one"),
    ],
)
def test_resolve_solo_given_explicit_team_bounds_resolves_against_them(  # noqa: PLR0913, PLR0917
    riders: int, teams: int, min_team_size: int, max_team_size: int, expected: int | None
) -> None:
    """T-4: the caller's own team bounds replace the 2..4 defaults."""
    result = resolve_solo(riders, teams, min_team_size=min_team_size, max_team_size=max_team_size)

    assert result == expected


@given(
    riders=st.integers(min_value=1, max_value=500),
    teams=st.integers(min_value=1, max_value=100),
)
def test_resolve_solo_given_any_counts_returns_an_inside_bounds_solo(
    riders: int, teams: int
) -> None:
    """Property: a resolved solo sits inside the generator bounds."""
    solo = resolve_solo(riders, teams)

    if solo is None:
        assert riders < MIN_TEAM_SIZE * teams
    else:
        assert MIN_TEAM_SIZE * teams <= riders - solo <= DEFAULT_MAX_TEAM_SIZE * teams


# ---------------------------------------------------- check_message

# The Check message's opening paragraph, spelled once: each test below
# pins its own concrete numbers against it. RUF001: the multiplication,
# en-dash and minus glyphs are the message's own display spelling.
_CHECK_MESSAGE_HEAD = (
    "Team riders = teams × riders per team (2–4 per team). "  # noqa: RUF001 -- display glyphs
    "Solo riders = riders − team riders.\n"  # noqa: RUF001 -- display glyph
)


def test_check_message_given_full_teams_names_the_remainder_as_solo() -> None:
    """The 175/40 defaults read back as 160 team riders plus 15 solo."""
    message = check_message(175, 40)

    assert message == (
        _CHECK_MESSAGE_HEAD
        + "With 175 riders and 40 teams, team riders must be between 80 and 160; "
        + "this field fills the teams to 160, so solo riders = 15. Ready to generate."
    )


def test_check_message_given_teams_below_the_ceiling_clears_solo() -> None:
    """A field that fits on teams alone reads back as zero solo."""
    message = check_message(100, 40)

    assert message == (
        _CHECK_MESSAGE_HEAD
        + "With 100 riders and 40 teams, team riders must be between 80 and 160; "
        + "this field fills the teams to 100, so solo riders = 0. Ready to generate."
    )


def test_check_message_given_too_few_riders_names_both_corrections() -> None:
    """An impossible field names both fixes with concrete counts."""
    message = check_message(10, 8)

    assert message == (
        _CHECK_MESSAGE_HEAD
        + "With 10 riders and 8 teams, team riders must be between 16 and 32, "
        + "but the field has only 10 riders.\n"
        + "Increase Number of riders to at least 16 or reduce Number of teams to 5."
    )


def test_check_message_given_riders_below_one_team_names_the_rider_fix_only() -> None:
    """Below one team's floor, only adding riders helps."""
    message = check_message(1, 2)

    assert message == (
        _CHECK_MESSAGE_HEAD
        + "With 1 rider and 2 teams, team riders must be between 4 and 8, "
        + "but the field has only 1 rider.\n"
        + "Increase Number of riders to at least 4."
    )


def test_check_message_given_one_team_reads_the_team_noun_singular() -> None:
    """A one-team field reads "1 team", never "1 teams"."""
    message = check_message(2, 1)

    assert message == (
        _CHECK_MESSAGE_HEAD
        + "With 2 riders and 1 team, team riders must be between 2 and 4; "
        + "this field fills the teams to 2, so solo riders = 0. Ready to generate."
    )


def test_check_message_given_custom_team_bounds_names_them() -> None:
    """The caller's own team bounds are the ones spelled out."""
    message = check_message(16, 3, min_team_size=3, max_team_size=5)

    assert message == (
        "Team riders = teams × riders per team (3–5 per team). "  # noqa: RUF001 -- display glyphs
        "Solo riders = riders − team riders.\n"  # noqa: RUF001 -- display glyph
        "With 16 riders and 3 teams, team riders must be between 9 and 15; "
        "this field fills the teams to 15, so solo riders = 1. Ready to generate."
    )


# ------------------------------------------- default_interval_minutes


@pytest.mark.parametrize(
    ("lap_km", "avg_speed_kmh", "expected"),
    [
        pytest.param(8.0, 12.0, 45, id="demo-ride"),
        pytest.param(4.0, 12.0, 25, id="half-length-lap"),
        pytest.param(8.0, 24.0, 25, id="double-speed"),
        pytest.param(0.1, 60.0, 5, id="shortest-lap"),
        pytest.param(30.0, 1.0, 240, id="clamped-at-the-ceiling"),
    ],
)
def test_default_interval_minutes_given_speed_and_lap_returns_the_formula(
    lap_km: float, avg_speed_kmh: float, expected: int
) -> None:
    """The demo ride (8 km at 12 km/h) opens on 40 + 5 = 45 minutes."""
    result = default_interval_minutes(lap_km, avg_speed_kmh)

    assert result == expected


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        pytest.param(1, 240, 45, id="spin-authored-bounds"),
        pytest.param(45, 240, 45, id="floor-at-the-formula"),
        pytest.param(46, 240, 46, id="floor-above-the-formula"),
        pytest.param(1, 45, 45, id="ceiling-at-the-formula"),
        pytest.param(1, 44, 44, id="ceiling-below-the-formula"),
    ],
)
def test_default_interval_minutes_given_explicit_bounds_clamps_into_them(
    minimum: int, maximum: int, expected: int
) -> None:
    """T-4: the result is clamped to the caller's own spin bounds."""
    result = default_interval_minutes(8.0, 12.0, minimum=minimum, maximum=maximum)

    assert result == expected


@given(
    lap_km=st.floats(min_value=0.1, max_value=5000.0, allow_nan=False, allow_infinity=False),
    avg_speed_kmh=st.floats(
        min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False
    ),
)
def test_default_interval_minutes_given_any_ride_stays_inside_the_spin(
    lap_km: float, avg_speed_kmh: float
) -> None:
    """Property: the default stays inside the spin's 1..240."""
    result = default_interval_minutes(lap_km, avg_speed_kmh)

    assert 1 <= result <= 240


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


def test_run_simulation_records_one_crossing_per_entry_per_lap() -> None:
    """Two laps over six entries record exactly twelve crossings."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    outcome = presenter.run_simulation(laps=2, interval_minutes=1)

    assert outcome == SimOutcome(cancelled=False, recorded=12, blocked=None)
    assert len(engine.crossings) == 12
    assert {result.laps for result in engine.snapshot()} == {2}


def test_run_simulation_pooled_team_laps_equal_solo_laps() -> None:
    """Phase 5: a pooled team and a solo cover the same 12 laps."""
    presenter, engine, roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)
    solo = _entries_of(roster, EntryType.SOLO)[0]
    team = _entries_of(roster, EntryType.TEAM)[0]

    presenter.run_simulation(laps=12, interval_minutes=45)

    laps_by_entry = {result.entry_id: result.laps for result in engine.snapshot()}
    assert laps_by_entry[team.plate] == laps_by_entry[solo.plate] == 12


def test_run_simulation_given_the_dialog_defaults_records_660_crossings() -> None:
    """40 teams + 15 solos over 12 laps record 660 crossings."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(175, 40, 15, seed=_SEED)

    outcome = presenter.run_simulation(laps=12, interval_minutes=45)

    assert outcome == SimOutcome(cancelled=False, recorded=660, blocked=None)
    assert {result.laps for result in engine.snapshot()} == {12}


def test_run_simulation_first_lap_opens_one_interval_after_the_gun() -> None:
    """Phase 4: lap 1's leader crosses a full interval after the gun."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=1, interval_minutes=45)

    first_lap = sorted(crossing.crossed_at for crossing in engine.crossings)
    assert min(first_lap) == _actual_start_of(engine) + timedelta(minutes=45)


def test_run_simulation_no_derived_lap_time_is_zero() -> None:
    """Phase 4: no lap time is zero; lap 1 clears the interval."""
    presenter, engine, roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=3, interval_minutes=45)

    lap_times = [seconds for entry in roster.entries for seconds in engine.lap_times(entry.plate)]
    assert len(lap_times) == 18
    assert min(lap_times) == 45 * 60.0


def test_run_simulation_lap_offsets_stay_inside_the_first_half_interval() -> None:
    """Phase 4: a lap's offsets span 0 .. interval // 2, ascending."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=2, interval_minutes=45)

    waves = _waves_of(engine)
    second_wave_base = _actual_start_of(engine) + timedelta(minutes=90)
    assert waves[1][0] == second_wave_base
    assert waves[1][1] - second_wave_base == timedelta(minutes=22)
    assert waves[0][0] - _actual_start_of(engine) == timedelta(minutes=45)


def test_run_simulation_half_interval_gap_separates_the_waves() -> None:
    """Phase 4: each wave opens a half-interval after the last one."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=3, interval_minutes=5)

    waves = _waves_of(engine)
    gun = _actual_start_of(engine)
    assert waves[0][0] == gun + timedelta(minutes=5)
    assert waves[0][1] <= gun + timedelta(minutes=7.5)
    assert waves[1][0] - waves[0][1] >= timedelta(minutes=2.5)
    assert waves[2][0] - waves[1][1] >= timedelta(minutes=2.5)


def test_run_simulation_second_lap_opens_one_interval_after_the_first() -> None:
    """Lap 2's wave opens exactly two intervals after the gun."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=2, interval_minutes=45)

    lap_two_base = _actual_start_of(engine) + timedelta(minutes=90)
    second_lap = [crossing.crossed_at for crossing in engine.crossings if crossing.seq == 2]
    assert min(second_lap) == lap_two_base


def test_run_simulation_keeps_the_same_field_order_for_every_lap() -> None:
    """Phase 4: the shuffled field order is stable lap after lap."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=3, interval_minutes=45)

    orders = [[crossing.entry_id for crossing in lap] for lap in _laps_of(engine)]
    assert len(orders) == 3
    assert orders[0] == orders[1] == orders[2]
    assert len(set(orders[0])) == 6


def test_run_simulation_backdates_the_gun_by_the_whole_race() -> None:
    """Phase 4: the gun sits the race plus a half-interval back."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=12, interval_minutes=45)

    assert engine.actual_start == _START - timedelta(minutes=12 * 45) - timedelta(minutes=22.5)


def test_run_simulation_elapsed_reads_the_whole_race_at_completion() -> None:
    """Phase 4: elapsed() shows the whole race once the run ends."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=12, interval_minutes=45)

    expected = (12 * 45 + 22.5) * 60
    assert abs(engine.elapsed() - expected) <= _ELAPSED_TOLERANCE_S


def test_run_simulation_last_crossing_lands_on_the_live_clock() -> None:
    """Phase 4: the final wave's last rider lands on the live clock."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=2, interval_minutes=45)

    last = max(crossing.crossed_at for crossing in engine.crossings)
    assert last <= _START
    assert _START - last <= timedelta(minutes=1)


def test_run_simulation_appends_start_and_stop_events() -> None:
    """Phase 4: the run opens with start and closes with stop."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    presenter.run_simulation(laps=1, interval_minutes=45)

    actions = [event.action for event in engine.events]
    assert actions[0] == "start"
    assert actions[-1] == "stop"


def test_run_simulation_pooled_team_rotates_its_representative_each_lap() -> None:
    """A two-rider team alternates its riders' plates."""
    presenter, engine, roster = _draft()
    presenter.generate_riders(2, 1, 0, seed=_SEED)
    team = _entries_of(roster, EntryType.TEAM)[0]

    presenter.run_simulation(laps=3, interval_minutes=1)

    assert [crossing.rider_plate for crossing in engine.crossings] == [
        team.riders[0].plate,
        team.riders[1].plate,
        team.riders[0].plate,
    ]


def test_run_simulation_same_seed_replays_the_same_race() -> None:
    """One seed reproduces the crossing order and every instant."""
    first, first_engine, _first_roster = _draft()
    second, second_engine, _second_roster = _draft()
    first.generate_riders(10, 2, 4, seed=_SEED)
    second.generate_riders(10, 2, 4, seed=_SEED)

    first.run_simulation(laps=2, interval_minutes=45)
    second.run_simulation(laps=2, interval_minutes=45)

    first_race = [(c.entry_id, c.rider_plate, c.crossed_at) for c in first_engine.crossings]
    second_race = [(c.entry_id, c.rider_plate, c.crossed_at) for c in second_engine.crossings]
    assert first_race == second_race


def test_run_simulation_leaves_the_ride_running_and_stopped() -> None:
    """The ride ends RUNNING with plate entry locked by Stop."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=1, interval_minutes=1)

    assert engine.state is RideStatus.RUNNING
    assert engine.stopped is True


def test_run_simulation_solo_lap_times_match_the_interval() -> None:
    """A solo entry's every lap is exactly one interval long."""
    presenter, engine, roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)
    solo = _entries_of(roster, EntryType.SOLO)[0]

    presenter.run_simulation(laps=3, interval_minutes=1)

    assert engine.lap_times(solo.plate) == (60.0, 60.0, 60.0)


def test_run_simulation_reuses_one_entry_order_for_every_lap() -> None:
    """Every lap crosses the same entries in order, clock rising."""
    presenter, engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    presenter.run_simulation(laps=3, interval_minutes=1)

    entry_ids = [crossing.entry_id for crossing in engine.crossings]
    assert entry_ids[0:6] == entry_ids[6:12] == entry_ids[12:18]
    first_lap = [crossing.crossed_at for crossing in engine.crossings[:6]]
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

    assert calls[-1] == (12, 12)
    assert [done for done, _total in calls] == list(range(1, 13))


def test_run_simulation_uncancelled_predicate_runs_to_the_end() -> None:
    """A cancel check that never fires does not cut the run short."""
    presenter, _engine, _roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    outcome = presenter.run_simulation(laps=1, interval_minutes=1, is_cancelled=lambda: False)

    assert outcome == SimOutcome(cancelled=False, recorded=6, blocked=None)


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
    """A relay lap records once per entry, on the entry's own plate."""
    presenter, engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    outcome = presenter.run_simulation(laps=1, interval_minutes=1)

    assert outcome == SimOutcome(cancelled=False, recorded=4, blocked=None)
    assert sorted(crossing.entry_id for crossing in engine.crossings) == sorted(
        entry.plate for entry in roster.entries
    )


def test_run_simulation_relay_team_laps_equal_solo_laps() -> None:
    """A relay team and a solo cover the same number of laps."""
    presenter, engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(6, 2, 2, seed=_SEED)
    solo = _entries_of(roster, EntryType.SOLO)[0]
    team = _entries_of(roster, EntryType.TEAM)[0]

    presenter.run_simulation(laps=3, interval_minutes=1)

    laps_by_entry = {result.entry_id: result.laps for result in engine.snapshot()}
    assert laps_by_entry[team.plate] == laps_by_entry[solo.plate] == 3


# ------------------------------------------------ _plates_to_record


def test_plates_to_record_relay_returns_one_plate_per_entry() -> None:
    """A relay ride records one plate per entry, not one per rider."""
    presenter, _engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    plates = _plates_to_record(roster)

    assert plates == [entry.plate for entry in roster.entries]


def test_plates_to_record_relay_is_the_entry_plate_on_every_lap() -> None:
    """A relay team's entry plate never changes lap to lap."""
    presenter, _engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(6, 2, 2, seed=_SEED)

    assert _plates_to_record(roster, 1) == _plates_to_record(roster, 0)
    assert _plates_to_record(roster, 0) == [entry.plate for entry in roster.entries]


def test_plates_to_record_pooled_returns_one_plate_per_entry() -> None:
    """A pooled ride records one plate per entry, solos included."""
    presenter, _engine, roster = _draft()
    presenter.generate_riders(10, 2, 4, seed=_SEED)

    plates = _plates_to_record(roster)

    assert len(plates) == 6
    assert plates == [
        entry.plate if entry.type is EntryType.SOLO else entry.riders[0].plate
        for entry in roster.entries
    ]


def test_plates_to_record_pooled_rotates_the_team_representative_per_lap() -> None:
    """A four-rider team sends each rider in turn, then starts again."""
    presenter, _engine, roster = _draft()
    presenter.generate_riders(6, 1, 2, seed=_SEED)
    team = _entries_of(roster, EntryType.TEAM)[0]
    position = list(roster.entries).index(team)

    assert [_plates_to_record(roster, lap)[position] for lap in range(5)] == [
        team.riders[0].plate,
        team.riders[1].plate,
        team.riders[2].plate,
        team.riders[3].plate,
        team.riders[0].plate,
    ]


def test_plates_to_record_pooled_riderless_team_uses_the_entry_plate() -> None:
    """A zero-rider team falls back to its provisional claim."""
    _presenter, _engine, roster = _draft()
    team = roster.create_empty_team(display_name="TEAM-0001")

    plates = _plates_to_record(roster)

    assert plates == [team.plate]


@pytest.mark.parametrize("plate_model", [PlateModel.RIDER_POOLED, PlateModel.TEAM_RELAY])
def test_plates_to_record_empty_roster_returns_no_plates(plate_model: PlateModel) -> None:
    """An empty roster records nothing, under either model."""
    _presenter, _engine, roster = _draft(plate_model=plate_model)

    plates = _plates_to_record(roster)

    assert plates == []


@pytest.mark.parametrize(
    ("plate_model", "expected"),
    [
        pytest.param(PlateModel.TEAM_RELAY, 1, id="single-entry-relay"),
        pytest.param(PlateModel.RIDER_POOLED, 1, id="single-entry-pooled"),
    ],
)
def test_plates_to_record_single_team_records_one_plate_per_model(
    plate_model: PlateModel, expected: int
) -> None:
    """One two-rider team yields one plate, relay or pooled."""
    presenter, _engine, roster = _draft(plate_model=plate_model)
    presenter.generate_riders(2, 1, 0, seed=_SEED)

    plates = _plates_to_record(roster)

    assert len(plates) == expected


@given(
    total=st.integers(min_value=2, max_value=6),
    seed=st.integers(min_value=-1000, max_value=1000),
)
def test_plates_to_record_given_a_relay_ride_returns_one_plate_per_entry(
    total: int, seed: int
) -> None:
    """Property: one relay plate per entry, all distinct (T-7)."""
    presenter, _engine, roster = _draft(plate_model=PlateModel.TEAM_RELAY)
    presenter.generate_riders(total, 1, total - 2, seed=seed)

    plates = _plates_to_record(roster)

    assert len(plates) == len(roster.entries)
    assert len(set(plates)) == len(plates)


@given(
    total=st.integers(min_value=2, max_value=6),
    seed=st.integers(min_value=-1000, max_value=1000),
)
def test_plates_to_record_given_a_pooled_ride_returns_one_plate_per_entry(
    total: int, seed: int
) -> None:
    """Property: one pooled plate per entry, none absent (T-7)."""
    presenter, _engine, roster = _draft()
    presenter.generate_riders(total, 1, total - 2, seed=seed)

    plates = _plates_to_record(roster)

    assert len(plates) == len(roster.entries)


# --------------------------------------------------------- _lap_offsets


@pytest.mark.parametrize(
    ("entry_count", "interval_minutes", "expected"),
    [
        pytest.param(0, 45, [], id="empty-field"),
        pytest.param(1, 45, [0], id="single-entry"),
        pytest.param(2, 45, [0, 22], id="two-entries"),
        pytest.param(6, 45, [0, 4, 8, 13, 17, 22], id="many-entries"),
        pytest.param(2, 2, [0, 1], id="two-minute-interval"),
        pytest.param(2, 1, [0, 0], id="one-minute-interval"),
        pytest.param(3, 5, [0, 1, 2], id="odd-interval"),
    ],
)
def test_lap_offsets_given_a_field_returns_ascending_first_half_wave_minutes(
    entry_count: int, interval_minutes: int, expected: list[int]
) -> None:
    """T-4: the wave ascends 0 .. interval // 2, one per entry."""
    offsets = _lap_offsets(entry_count, interval_minutes)

    assert offsets == expected


def test_lap_offsets_given_the_widest_field_reaches_the_half_interval() -> None:
    """A 240-entry wave inside a 240-minute interval spans 0..120."""
    offsets = _lap_offsets(240, 240)

    assert (len(offsets), offsets[0], offsets[-1]) == (240, 0, 120)


@given(
    entry_count=st.integers(min_value=1, max_value=200),
    interval_minutes=st.integers(min_value=1, max_value=240),
)
def test_lap_offsets_given_any_field_stays_inside_the_first_half_interval(
    entry_count: int, interval_minutes: int
) -> None:
    """Property: the leader opens at 0, all inside the half (T-7)."""
    offsets = _lap_offsets(entry_count, interval_minutes)

    assert len(offsets) == entry_count
    assert offsets[0] == 0
    assert sorted(offsets) == offsets
    assert max(offsets) <= interval_minutes // 2
