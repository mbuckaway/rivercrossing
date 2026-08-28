# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.ride's RideConfig and RideEngine.

RideConfig (E3.5.1) was written first, against a ``RideConfig`` that
did not exist yet (R-70): module-skeletons.md S4's own reserved name
-- ``RideEngine.__init__(config: RideConfig, ...)`` in E4 -- pre-created
it here next to ``RideStatus``, mirroring how ``RideStatus`` itself was
pre-created ahead of the state machine that consumes it. Boundary rows
follow this repo's own T-4 convention (min-1, min, min+1, max-1, max,
max+1) for every bounded field: ``max_team_size`` (2..10, R-12),
``deck_count`` (>=1, spec.md §4), ``planned_duration_s``/``min_lap_s``
(positive, spec.md §2/§6).

RideEngine (E4.1, below) is the state machine + timing core: spec §3's
DRAFT -> RUNNING -> FINISHED <-> REOPENED transitions with every
illegal move raising (E4.1.1), wall-clock timing from an injected fake
clock (R-30), the set-start-time retro-fix recomputing lap-1 only
(E4.1.2), stop-as-guard with continue (E4.1.3), the start gate over
``Roster.validate_for_start``, the minimal crossing path, and the
standings snapshot. E4.2 extends the crossing path here: one shoe deal
per accepted crossing (R-40, incl. the mid-ride reshuffle audit), the
short-lap hold/confirm/void surface (R-34), and the compensating-write
undo (R-33). E4.3 pins that dealing exact (seed replay), the card cap X
(R-13: laps past the cap still count, later cards still deal but never
score), the manual-deal engine path ``deal_manual`` (spec section 4;
unknown plate raises, DRAFT/FINISHED gate), and the shoe close on
Finish (every later deal raises ``ShoeClosedError`` while REOPENED
undo still reverses).
"""

import re
import time
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from rivercrossing.cards import Card, Shoe, ShoeClosedError
from rivercrossing.hands import best_hand, compare
from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_JOKERS_PER_DECK,
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
    Crossing,
    Event,
    IllegalStateError,
    RideConfig,
    RideConfigError,
    RideEngine,
    RideEngineError,
    RideStatus,
    StartBlockedError,
    UnknownEventActionError,
    UnknownPlateError,
)
from rivercrossing.roster import EntryMode, EntryStatus, PlateModel, Rider, Roster

# A minimal, always-valid kwarg set every test overrides from -- one
# required field at a time, never guessing at a second field's own
# validity while probing the first (T-8's one-focused-assertion
# spirit, applied to arrange too).
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    # naive, by design: planned_start is a pre-persistence, local
    # wall-clock value (RideConfig's own docstring) -- UTC-epoch
    # conversion is EPIC 5's Store concern, not this dataclass's.
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001
    "planned_duration_s": 21600,
    "min_lap_s": 1080,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------- defaults


def test_ride_config_bare_required_fields_defaults_max_team_size_to_four() -> None:
    """max_team_size defaults to 4 (spec.md §1/§2, R-12)."""
    config = _config()

    assert config.max_team_size == 4


def test_ride_config_bare_required_fields_defaults_deck_count_to_eight() -> None:
    """decks_spin's own presenter-supplied default (spec.md §4)."""
    config = _config()

    assert config.deck_count == DEFAULT_DECK_COUNT


def test_ride_config_bare_required_fields_defaults_jokers_per_deck_to_two() -> None:
    """jokers_2_radio's XRC default (xrc-windows.md's setup mock)."""
    config = _config()

    assert config.jokers_per_deck == DEFAULT_JOKERS_PER_DECK


def test_ride_config_bare_required_fields_defaults_max_cards_to_uncapped() -> None:
    """cap_chk unticked by default: max_cards is None (uncapped)."""
    config = _config()

    assert config.max_cards is None


def test_ride_config_bare_required_fields_defaults_tiebreak_order_to_the_spec_order() -> None:
    """R-14's own order: laps, then total time, then high-card draw."""
    config = _config()

    assert config.tiebreak_order == (TIEBREAK_LAPS, TIEBREAK_TOTAL_TIME, TIEBREAK_HIGH_CARD)


def test_ride_config_bare_required_fields_defaults_logo_path_to_none() -> None:
    """logo_picker empty by default: no logo chosen (E5 owns BLOBs)."""
    config = _config()

    assert config.logo_path is None


def test_ride_config_given_a_logo_path_stores_it_verbatim() -> None:
    """A chosen logo_picker path round-trips exactly."""
    path = Path("/tmp/gorba-logo.png")  # noqa: S108 -- a stored value, never opened here

    config = _config(logo_path=path)

    assert config.logo_path == path


def test_ride_config_given_every_required_field_stores_each_verbatim() -> None:
    """Every required field round-trips exactly, in one built config."""
    config = _config()

    assert (config.name, config.venue, config.entry_mode, config.plate_model) == (
        "GORBA EPIC 2026",
        "Sea to Sky Gondola",
        EntryMode.MIXED,
        PlateModel.RIDER_POOLED,
    )


# ------------------------------------------------------ frozen/kw-only


def test_ride_config_mutation_raises_frozen_instance_error() -> None:
    """RideConfig is frozen (module-skeletons.md S4's own rule)."""
    config = _config()

    with pytest.raises(FrozenInstanceError, match=re.escape("cannot assign to field 'name'")):
        config.name = "Changed"  # type: ignore[misc]


def test_ride_config_requires_every_field_as_keyword() -> None:
    """RideConfig takes no positional arguments (kw_only=True)."""
    with pytest.raises(TypeError, match=re.escape("takes 1 positional argument")):
        RideConfig("GORBA EPIC 2026")  # type: ignore[misc, call-arg]


# ----------------------------------------------- max_team_size bound


@pytest.mark.parametrize("max_team_size", [1, 11], ids=["min-1", "max+1"])
def test_ride_config_max_team_size_out_of_range_raises(max_team_size: int) -> None:
    """max_team_size outside 2..10 raises (R-12)."""
    with pytest.raises(RideConfigError, match=re.escape("max_team_size")):
        _config(max_team_size=max_team_size)


@pytest.mark.parametrize("max_team_size", [2, 3, 9, 10], ids=["min", "min+1", "max-1", "max"])
def test_ride_config_max_team_size_in_range_is_accepted(max_team_size: int) -> None:
    """max_team_size within 2..10 is accepted as given."""
    config = _config(max_team_size=max_team_size)

    assert config.max_team_size == max_team_size


# --------------------------------------------------- deck_count bound


@pytest.mark.parametrize("deck_count", [0, -1], ids=["min-1", "min-2"])
def test_ride_config_deck_count_below_one_raises(deck_count: int) -> None:
    """deck_count below 1 raises (spec.md §4: >=1 deck needed)."""
    with pytest.raises(RideConfigError, match=re.escape("deck_count")):
        _config(deck_count=deck_count)


@pytest.mark.parametrize("deck_count", [1, 2, 8], ids=["min", "min+1", "default"])
def test_ride_config_deck_count_at_or_above_one_is_accepted(deck_count: int) -> None:
    """deck_count >= 1 is accepted as given."""
    config = _config(deck_count=deck_count)

    assert config.deck_count == deck_count


# ------------------------------------------- planned_duration_s bound


@pytest.mark.parametrize("planned_duration_s", [0, -1], ids=["zero", "negative"])
def test_ride_config_planned_duration_not_positive_raises(planned_duration_s: int) -> None:
    """planned_duration_s must be positive (spec.md §2)."""
    with pytest.raises(RideConfigError, match=re.escape("planned_duration_s")):
        _config(planned_duration_s=planned_duration_s)


@pytest.mark.parametrize("planned_duration_s", [1, 21600], ids=["min+1", "six_hours"])
def test_ride_config_planned_duration_positive_is_accepted(planned_duration_s: int) -> None:
    """A positive planned_duration_s is accepted as given."""
    config = _config(planned_duration_s=planned_duration_s)

    assert config.planned_duration_s == planned_duration_s


# --------------------------------------------------- min_lap_s bound


@pytest.mark.parametrize("min_lap_s", [0, -1], ids=["zero", "negative"])
def test_ride_config_min_lap_not_positive_raises(min_lap_s: int) -> None:
    """min_lap_s must be positive (spec.md §6)."""
    with pytest.raises(RideConfigError, match=re.escape("min_lap_s")):
        _config(min_lap_s=min_lap_s)


@pytest.mark.parametrize("min_lap_s", [1, 1080], ids=["min+1", "eighteen_minutes"])
def test_ride_config_min_lap_positive_is_accepted(min_lap_s: int) -> None:
    """A positive min_lap_s is accepted as given."""
    config = _config(min_lap_s=min_lap_s)

    assert config.min_lap_s == min_lap_s


# ==================================================== E4.1 engine


class _FakeClock:
    """A scriptable wall clock for RideEngine's injected clock."""

    def __init__(self, start: datetime) -> None:
        """Freeze the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a naive datetime on the fixed day, Sept 20, 2026."""
    return datetime(2026, 9, 20, hour, minute, second)  # noqa: DTZ001 -- naive by design, as RideConfig's planned_start


def _roster_with_entries(*plates: str) -> Roster:
    """Build a MIXED rider_pooled roster of one solo entry per plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(name=f"Rider {plate}", plate=plate)
    return roster


def _make_engine(
    *,
    roster: Roster | None = None,
    clock: _FakeClock | None = None,
    config: RideConfig | None = None,
) -> tuple[RideEngine, _FakeClock]:
    """Build a DRAFT engine over a valid config, shoe and roster."""
    config = config if config is not None else _config()
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    roster = roster if roster is not None else _roster_with_entries("12", "34")
    clock = clock if clock is not None else _FakeClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    return engine, clock


def _record_crossings(  # noqa: PLR0913 -- seeded batch recorder: (engine, plate, count) + (start_at, step_s)
    engine: RideEngine, plate: str, count: int, *, start_at: datetime, step_s: float
) -> None:
    """Record *count* crossings for *plate*, *step_s* apart."""
    for index in range(count):
        engine.record_crossing(plate, at=start_at + timedelta(seconds=index * step_s))


def _engine_in(state: str) -> tuple[RideEngine, _FakeClock]:
    """Build an engine already in one of the five states."""
    engine, clock = _make_engine()
    if state == "running":
        engine.start()
    elif state == "stopped":
        engine.start()
        engine.stop()
    elif state == "finished":
        engine.start()
        engine.finish()
    elif state == "reopened":
        engine.start()
        engine.finish()
        engine.reopen()
    return engine, clock


# ------------------------------------------------------ state machine


def test_engine_bare_construction_starts_in_draft() -> None:
    """A fresh engine begins DRAFT with no events (spec §3)."""
    engine, _ = _make_engine()

    assert (engine.state, engine.events) == (RideStatus.DRAFT, ())


def test_start_from_draft_transitions_to_running_and_writes_audit_row() -> None:
    """start() moves DRAFT -> RUNNING and appends a start event."""
    engine, _ = _make_engine()

    event = engine.start()

    assert engine.state is RideStatus.RUNNING
    assert event == Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
    assert engine.events == (event,)


def test_start_with_explicit_at_retro_sets_actual_start() -> None:
    """start(at=...) back-dates actual_start (R-30, the missed gun)."""
    engine, _ = _make_engine()

    engine.start(at=_dt(9, 45))

    assert engine.events[-1].payload == {"actual_start": "2026-09-20T09:45:00"}


def test_start_sets_roster_status_to_running() -> None:
    """The roster's status mirrors the engine's transition (E3.1.2)."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)

    engine.start()

    assert roster.status is RideStatus.RUNNING


def test_finish_from_running_transitions_to_finished() -> None:
    """finish() moves RUNNING -> FINISHED (spec §3)."""
    engine, _ = _make_engine()
    engine.start()

    engine.finish()

    assert engine.state is RideStatus.FINISHED
    assert engine.events[-1].action == "finish"


def test_reopen_from_finished_transitions_to_reopened() -> None:
    """reopen() moves FINISHED -> REOPENED (corrections-only, R-64)."""
    engine, _ = _make_engine()
    engine.start()
    engine.finish()

    engine.reopen()

    assert engine.state is RideStatus.REOPENED
    assert engine.events[-1].action == "reopen"


def test_finish_again_from_reopened_transitions_to_finished() -> None:
    """finish() re-locks REOPENED -> FINISHED (spec §3)."""
    engine, _ = _make_engine()
    engine.start()
    engine.finish()
    engine.reopen()

    engine.finish()

    assert engine.state is RideStatus.FINISHED
    assert engine.events[-1].action == "finish"


@pytest.mark.parametrize(
    ("start_state", "method", "match"),
    [
        ("finished", "start", "cannot start"),
        ("reopened", "start", "cannot start"),
        ("draft", "finish", "cannot finish"),
        ("finished", "finish", "cannot finish"),
        ("draft", "reopen", "cannot reopen"),
        ("running", "reopen", "cannot reopen"),
        ("reopened", "reopen", "cannot reopen"),
        ("draft", "stop", "cannot stop"),
        ("finished", "stop", "cannot stop"),
        ("reopened", "stop", "cannot stop"),
        ("stopped", "stop", "already stopped"),
        ("draft", "elapsed", "has not started"),
        ("draft", "remaining", "has not started"),
    ],
)
def test_engine_illegal_operation_raises_illegal_state_error(
    start_state: str, method: str, match: str
) -> None:
    """Every illegal transition raises, pinned per row (T-12)."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        getattr(engine, method)()


def test_set_start_time_from_draft_raises_illegal_state_error() -> None:
    """set_start_time is a live-ride correction (RUNNING only)."""
    engine, _ = _make_engine()

    with pytest.raises(IllegalStateError, match=re.escape("cannot set start time from draft")):
        engine.set_start_time(_dt(9, 0))


# ----------------------------------------------------------- wall clock


def test_elapsed_derives_from_injected_clock_not_a_stored_timer() -> None:
    """elapsed() is now - actual_start from the clock (R-30)."""
    engine, clock = _make_engine()
    engine.start()
    clock.advance(90)

    assert engine.elapsed() == 90.0


def test_elapsed_after_finish_keeps_deriving_from_clock() -> None:
    """No stored timer: elapsed still moves after finish (spec §3)."""
    engine, clock = _make_engine()
    engine.start()
    engine.finish()
    clock.advance(120)

    assert engine.elapsed() == 120.0


def test_remaining_derives_from_clock_and_planned_duration() -> None:
    """remaining() is planned_duration_s - elapsed() (R-30)."""
    engine, clock = _make_engine()
    engine.start()
    clock.advance(60)

    assert engine.remaining() == 21600 - 60


# ------------------------------------------------------- set start time


def test_set_start_time_recomputes_lap_one_and_writes_audit_row() -> None:
    """A back-dated start grows lap-1 time and logs an event (3d)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 3, 20))

    event = engine.set_start_time(_dt(9, 55))

    assert engine.lap_times("12") == (500.0,)
    assert event == Event(
        action="set_start_time",
        payload={
            "actual_start": "2026-09-20T09:55:00",
            "previous_start": "2026-09-20T10:00:00",
        },
    )


def test_set_start_time_recomputes_only_lap_one_never_later_laps() -> None:
    """Later laps derive from their own crossing, so they stay put."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 3, 20))
    engine.record_crossing("12", at=_dt(10, 5))

    engine.set_start_time(_dt(9, 55))

    assert engine.lap_times("12") == (500.0, 100.0)


# -------------------------------------------------------- stop/continue


def test_stop_returns_event_and_blocks_crossings_with_refusal_result() -> None:
    """Stop locks entry with a refusal; the ride stays RUNNING."""
    engine, _ = _make_engine()
    engine.start()
    engine.stop()

    result = engine.record_crossing("12")

    assert result.accepted is False
    assert result.reason == "ride is stopped"
    assert result.lap == 0
    assert engine.state is RideStatus.RUNNING
    assert engine.lap_times("12") == ()


def test_start_after_stop_continues_with_unchanged_actual_start() -> None:
    """start() on RUNNING continues; actual_start is unchanged."""
    engine, clock = _make_engine()
    engine.start()
    engine.stop()
    clock.advance(600)

    engine.start()

    assert engine.state is RideStatus.RUNNING
    assert engine.elapsed() == 600.0
    assert engine.events[-1] == Event(
        action="continue", payload={"actual_start": "2026-09-20T10:00:00"}
    )


def test_continue_after_stop_accepts_crossings_with_no_time_lost() -> None:
    """A continued ride laps from the original actual_start."""
    engine, clock = _make_engine()
    engine.start()
    engine.stop()
    clock.advance(600)
    engine.start()

    result = engine.record_crossing("12")

    assert result.accepted is True
    assert result.lap == 1
    assert result.lap_time == 600.0


# -------------------------------------------------- start gate


def test_start_with_below_floor_team_raises_start_blocked_and_stays_draft() -> None:
    """A below-floor team blocks start; no state change (R-12)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry_of_one(display_name="Half Team", rider=Rider(name="Bo", plate="7"))
    engine, _ = _make_engine(roster=roster)

    with pytest.raises(StartBlockedError, match=re.escape("team size must be at least 2")):
        engine.start()

    assert engine.state is RideStatus.DRAFT
    assert engine.events == ()


# -------------------------------------------------- record_crossing


def test_record_crossing_unknown_plate_returns_refusal_result() -> None:
    """An unknown plate comes back refused, not raised (cue is E4.4)."""
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("999")

    assert result.accepted is False
    assert result.reason == "unknown_plate"
    assert result.entry_id is None
    assert result.lap == 0
    assert result.card is None
    assert result.flagged is False


def test_record_crossing_before_start_returns_refusal_result() -> None:
    """Live entry is RUNNING-only; other states refuse with a result."""
    engine, _ = _make_engine()

    result = engine.record_crossing("12")

    assert result.accepted is False
    assert result.reason == "ride is not running"


def test_record_crossing_credits_one_lap_and_marks_has_data() -> None:
    """A recorded crossing credits lap 1 and marks has_data."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 2))

    assert result.accepted is True
    assert result.entry_id == "12"
    assert result.lap == 1
    assert result.lap_time == 120.0
    assert roster.entries[0].has_data is True
    assert engine.events[-1] == Event(
        action="record_crossing",
        payload={"plate": "12", "entry_id": "12", "lap": 1, "crossed_at": "2026-09-20T10:02:00"},
    )


def test_record_crossing_second_lap_times_from_previous_crossing() -> None:
    """Lap 2's time is this minus the previous crossing (spec §6)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 2))

    result = engine.record_crossing("12", at=_dt(10, 4, 30))

    assert (result.lap, result.lap_time) == (2, 150.0)


def test_record_crossing_omitted_at_uses_injected_clock() -> None:
    """at=None stamps the crossing from the injected clock."""
    engine, clock = _make_engine()
    engine.start()
    clock.advance(90)

    result = engine.record_crossing("12")

    assert (result.accepted, result.lap_time) == (True, 90.0)


def test_record_crossing_pooled_rider_plate_credits_the_team() -> None:
    """A rider's plate resolves to the team entry (R-16 pooling)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    engine, _ = _make_engine(roster=roster)
    engine.start()

    result = engine.record_crossing("45")

    assert result.accepted is True
    assert result.entry_id == "9"
    assert result.entry_name == "Dirt Dynamos"


# ----------------------------------------------------- snapshot


def test_snapshot_before_start_returns_one_result_per_active_entry() -> None:
    """Pre-start snapshot: one EntryResult per ACTIVE entry."""
    engine, _ = _make_engine()

    results = engine.snapshot()

    assert [result.plate for result in results] == ["12", "34"]


def test_snapshot_before_start_reports_zero_laps_empty_cards_and_high_card_hand() -> None:
    """Pre-start results are laps=0, cards=(), hand=best_hand(())."""
    engine, _ = _make_engine()

    results = engine.snapshot()

    assert all(
        (result.laps, result.total_time, result.best_lap, result.cards, result.hand, result.dnf)
        == (0, 0.0, 0.0, (), best_hand(()), False)
        for result in results
    )


def test_snapshot_on_empty_roster_returns_empty_list() -> None:
    """An engine over an empty roster snapshots to []."""
    engine, _ = _make_engine(roster=Roster(entry_mode=EntryMode.MIXED))

    assert engine.snapshot() == []


def test_snapshot_after_crossings_reflects_laps_total_and_best_lap() -> None:
    """Snapshot totals derive laps, total and best lap (spec §6)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 3, 20))
    engine.record_crossing("12", at=_dt(10, 5, 20))

    results = {result.plate: result for result in engine.snapshot()}

    assert (results["12"].laps, results["12"].total_time, results["12"].best_lap) == (
        2,
        320.0,
        120.0,
    )
    assert (results["34"].laps, results["34"].total_time, results["34"].best_lap) == (0, 0.0, 0.0)


def test_snapshot_excludes_dnf_entries() -> None:
    """DNF'd entries drop out of the standings snapshot (spec §6)."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    # Arranged directly: mark_dnf is E4.2's engine method, not here yet.
    roster.entries[0].status = EntryStatus.DNF

    results = engine.snapshot()

    assert [result.plate for result in results] == ["34"]


def test_lap_times_empty_for_entry_without_laps() -> None:
    """An entry with no crossings reports no lap times."""
    engine, _ = _make_engine()
    engine.start()

    assert engine.lap_times("34") == ()


def test_on_course_counts_active_entries_with_odd_lap_counts() -> None:
    """Odd lap counts mean out on the loop; even means back."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("34", at=_dt(10, 3))
    engine.record_crossing("34", at=_dt(10, 4))

    assert engine.on_course == 1


# ============================================ E4.2 crossings + dealing


def test_record_crossing_normal_lap_credits_card_and_reports_flagged_false() -> None:
    """A lap at/above min_lap_s credits its card to the hand."""
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 30))

    assert result.accepted is True
    assert result.flagged is False
    assert engine.held_crossings() == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (result.card,)
    assert results["12"].hand == best_hand((result.card,))


def test_record_crossing_short_lap_flags_holds_card_and_still_records_lap() -> None:
    """A lap under min_lap_s flags short, records, holds its card."""
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 0, 30))

    assert result.accepted is True
    assert result.flagged is True
    held = engine.held_crossings()
    assert len(held) == 1
    assert held[0].crossing.seq == 1
    assert held[0].card == result.card
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 1
    assert results["12"].cards == ()


def test_record_crossing_min_lap_exact_equal_is_not_flagged() -> None:
    """A lap exactly at min_lap_s is normal, never flagged (spec §6)."""
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 18))

    assert result.flagged is False
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (result.card,)


def test_record_crossing_min_lap_one_second_under_is_flagged() -> None:
    """A lap a second under min_lap_s flags short, holds card."""
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 17, 59))

    assert result.flagged is True
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == ()


def test_record_crossing_deals_the_shoe_next_card_in_deal_index_order() -> None:
    """Each accepted crossing deals shoe[deal_index++] in turn."""
    expected = Shoe(decks=8, jokers_per_deck=2, seed=20260920)
    engine, _ = _make_engine()
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))
    third = engine.record_crossing("34", at=_dt(10, 34))

    assert (first.card, second.card, third.card) == (
        expected.deal()[0],
        expected.deal()[0],
        expected.deal()[0],
    )
    assert engine._shoe.dealt == 3


def test_record_crossing_pooled_rider_out_lapping_teammates_is_uncapped() -> None:
    """One rider may out-lap teammates; laps and cards pool uncapped."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    engine, _ = _make_engine(roster=roster, config=_config(min_lap_s=60))
    engine.start()
    _record_crossings(engine, "45", 5, start_at=_dt(10, 1), step_s=600)
    _record_crossings(engine, "9", 2, start_at=_dt(10, 51), step_s=600)

    results = {entry.plate: entry for entry in engine.snapshot()}

    assert results["9"].laps == 7
    assert len(results["9"].cards) == 7
    assert engine.lap_times("9") == (60.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0)


# ----------------------------------------- E4.2 held cards (R-34)


@pytest.mark.parametrize(
    ("action", "expected_hand_cards", "expected_held"),
    [
        ("confirm_held", 1, 0),
        ("void_held", 0, 0),
    ],
    ids=["confirm_releases_into_hand", "void_discards_never_credited"],
)
def test_record_crossing_held_card_confirm_void_table(
    action: str, expected_hand_cards: int, expected_held: int
) -> None:
    """Held-card lifecycle: confirm credits, void discards (R-34)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]

    getattr(engine, action)(held.crossing)

    assert len(engine.held_crossings()) == expected_held
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert len(results["12"].cards) == expected_hand_cards


def test_confirm_held_returns_audit_event_and_best_hand_improves() -> None:
    """confirm_held writes an audit row; the credited hand improves."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]

    event = engine.confirm_held(held.crossing)

    assert event == Event(
        action="confirm_held",
        payload={"entry_id": "12", "seq": 1, "card": held.card.code()},
    )
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (held.card,)
    assert results["12"].hand == best_hand((held.card,))


def test_void_held_returns_audit_event_and_hand_stays_empty() -> None:
    """void_held writes an audit row and never credits the card."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]

    event = engine.void_held(held.crossing)

    assert event == Event(
        action="void_held",
        payload={"entry_id": "12", "seq": 1, "card": held.card.code()},
    )
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == ()
    assert results["12"].hand == best_hand(())


def test_confirm_held_already_credited_crossing_raises_illegal_state_error() -> None:
    """confirm_held on a non-held crossing raises (R-34 negative)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    crossing = engine.held_crossings()[0].crossing
    engine.confirm_held(crossing)

    with pytest.raises(IllegalStateError, match=re.escape("crossing's card is not held")):
        engine.confirm_held(crossing)


def test_void_held_already_voided_crossing_raises_illegal_state_error() -> None:
    """void_held on a non-held crossing raises (R-34 negative)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    crossing = engine.held_crossings()[0].crossing
    engine.void_held(crossing)

    with pytest.raises(IllegalStateError, match=re.escape("crossing's card is not held")):
        engine.void_held(crossing)


def test_record_crossing_shoe_exhaustion_reshuffles_and_audits() -> None:
    """ShoeEmpty mid-ride reshuffles (seed+1) and audits it (R-40)."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1)
    engine, _ = _make_engine(config=config)
    engine.start()
    _record_crossings(engine, "12", 52, start_at=_dt(10, 0), step_s=60)

    result = engine.record_crossing("12", at=_dt(10, 53))

    assert result.accepted is True
    assert engine.events[-2] == Event(action="shoe_reshuffle", payload={"cycle": 2})
    assert engine.events[-1].action == "record_crossing"
    reshuffled = Shoe(decks=1, jokers_per_deck=0, seed=20260921)
    assert result.card == reshuffled.deal()[0]


def test_snapshot_cards_reflect_credited_and_released_cards() -> None:
    """EntryResult.cards pools credited plus released cards (R-34)."""
    engine, _ = _make_engine()
    engine.start()
    normal = engine.record_crossing("12", at=_dt(10, 30))
    flagged = engine.record_crossing("12", at=_dt(10, 32))
    engine.confirm_held(engine.held_crossings()[0].crossing)

    results = {entry.plate: entry for entry in engine.snapshot()}

    assert results["12"].cards == (normal.card, flagged.card)
    assert results["12"].hand == best_hand((normal.card, flagged.card))


def test_snapshot_excludes_held_and_voided_cards_from_the_hand() -> None:
    """Held (unconfirmed) and voided cards never reach the hand."""
    engine, _ = _make_engine()
    engine.start()
    normal = engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("12", at=_dt(10, 33))
    engine.void_held(engine.held_crossings()[-1].crossing)

    results = {entry.plate: entry for entry in engine.snapshot()}

    assert results["12"].cards == (normal.card,)
    assert results["12"].hand == best_hand((normal.card,))
    assert len(engine.held_crossings()) == 1


# --------------------------------------------- E4.2 undo (R-33)


def test_undo_last_removes_lap_restitutes_card_and_audits() -> None:
    """undo_last reverses the last crossing and audits it."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))

    event = engine.undo_last()

    assert event == Event(
        action="undo",
        payload={
            "entry_id": "12",
            "seq": 2,
            "crossed_at": "2026-09-20T10:32:00",
            "card": second.card.code(),
        },
    )
    assert engine.lap_times("12") == (1800.0,)
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 1
    assert results["12"].cards == (first.card,)


def test_undo_last_after_manual_deal_retires_crossing_card_not_front() -> None:
    """Undo after a manual deal never disturbs the manual card (R-33).

    The manual card is the shoe's last deal, so the undone crossing's
    own card cannot return to the front; it retires with the shoe
    instead, deterministically (E5.1.2 replay reproduces the same shoe
    point). The manual credit stays in the hand.
    """
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start(at=_dt(10, 0))
    crossing = engine.record_crossing("12", at=_dt(10, 30))
    manual = engine.deal_manual("12", reason="replacement card")

    event = engine.undo_last()

    assert event.action == "undo"
    assert event.payload["card"] == crossing.card.code()
    assert engine.lap_times("12") == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    manual_card = Card.parse(str(manual.payload["card"]))
    assert results["12"].cards == (manual_card,)
    assert engine._shoe.dealt == 2  # crossing card retired; manual card still dealt


def test_undo_then_rerecord_deals_the_same_card_from_the_shoe_front() -> None:
    """Undo restitutes the card; re-record deals it again (R-33)."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    original = engine.record_crossing("12", at=_dt(10, 30))
    engine.undo_last()

    rerecord = engine.record_crossing("12", at=_dt(10, 31))

    assert rerecord.card == original.card
    assert engine.lap_times("12") == (1860.0,)  # lap 1 again: crossed_at - actual_start


def test_undo_last_held_crossing_releases_hold_and_restitutes_card() -> None:
    """Undo of a held crossing drops the hold, never credits."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]

    engine.undo_last()

    assert engine.held_crossings() == ()
    assert engine.lap_times("12") == ()
    redo = engine.record_crossing("12", at=_dt(10, 0, 45))
    assert redo.card == held.card


def test_undo_last_voided_crossing_returns_its_card_to_the_shoe() -> None:
    """Undo fully reverses a voided crossing, card back to shoe."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    engine.void_held(held.crossing)

    engine.undo_last()

    redo = engine.record_crossing("12", at=_dt(10, 0, 45))
    assert redo.card == held.card


def test_undo_last_with_zero_crossings_raises_illegal_state_error() -> None:
    """undo_last on an empty ride raises (E4.2.3 negative)."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(IllegalStateError, match=re.escape("no crossings to undo")):
        engine.undo_last()


def test_undo_last_from_finished_raises_illegal_state_error() -> None:
    """Undo is a corrections path, blocked once FINISHED (spec §3)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()

    with pytest.raises(IllegalStateError, match=re.escape("cannot undo from finished")):
        engine.undo_last()


def test_undo_last_from_reopened_reverses_the_crossing() -> None:
    """REOPENED corrections allow undo (spec §3/§6)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()
    engine.reopen()

    engine.undo_last()

    assert engine.lap_times("12") == ()


# -------------------------------------------------- R-31 perf budget


def test_record_crossing_batch_of_100_averages_under_100ms() -> None:
    """100 real-engine crossings average well under 100 ms each (R-31).

    Mirrors tests/unit/test_hands.py's measured-budget style: seeded,
    no sleeps, and the bound is the requirement itself -- recording is
    dict/list work plus one shoe deal, so the real margin is orders of
    magnitude even on a slow CI runner.
    """
    engine, _ = _make_engine()
    engine.start()

    start = time.perf_counter()
    _record_crossings(engine, "12", 100, start_at=_dt(10, 0), step_s=60)
    elapsed = time.perf_counter() - start

    assert elapsed / 100 < 0.1  # R-31: feedback payload under 100 ms average


# ====================================== E4.3 dealing accounting (R-40)


def test_record_crossing_deals_match_reference_shoe_sequence() -> None:
    """Each crossing's card is shoe[deal_index++] in order (R-40)."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1)
    engine, _ = _make_engine(config=config)
    engine.start()
    reference = Shoe(decks=1, jokers_per_deck=0, seed=20260920)

    cards = tuple(engine.record_crossing("12", at=_dt(10, index)).card for index in range(1, 21))

    assert cards == tuple(reference.deal()[0] for _ in range(20))


def test_record_crossing_deal_sequence_replays_identically_from_seed() -> None:
    """Shoe.replay rebuilds the exact live shoe; next deal matches."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1)
    engine, _ = _make_engine(config=config)
    engine.start()
    for index in range(1, 21):
        engine.record_crossing("12", at=_dt(10, index))

    replayed = Shoe.replay(
        decks=1,
        jokers_per_deck=0,
        seed=20260920,
        deals=engine._shoe.dealt,
        cycles=engine._shoe.cycle,
    )

    assert replayed.deal()[0] == engine._shoe.deal()[0]


# ----------------------------------------- E4.3 card cap X (R-13)


def test_snapshot_cap_slices_scoring_cards_while_laps_past_cap_count() -> None:
    """max_cards caps scoring; laps past the cap still count (R-13)."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1, max_cards=2)
    engine, _ = _make_engine(config=config)
    engine.start()
    for index in range(1, 6):
        engine.record_crossing("12", at=_dt(10, index))

    results = {entry.plate: entry for entry in engine.snapshot()}

    reference = Shoe(decks=1, jokers_per_deck=0, seed=20260920)
    expected = tuple(reference.deal()[0] for _ in range(2))
    assert results["12"].laps == 5
    assert results["12"].cards == expected
    assert results["12"].hand == best_hand(expected)


def test_snapshot_cap_blocks_would_improve_eleventh_card() -> None:
    """A card past X is dealt but never improves the scored hand."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1, max_cards=10)
    shoe = Shoe(decks=1, jokers_per_deck=0, seed=1)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=_FakeClock(config.planned_start),
        roster=_roster_with_entries("12"),
    )
    engine.start()
    for index in range(1, 12):
        engine.record_crossing("12", at=_dt(10, index))

    results = {entry.plate: entry for entry in engine.snapshot()}
    reference = Shoe(decks=1, jokers_per_deck=0, seed=1)
    all_eleven = tuple(reference.deal()[0] for _ in range(11))

    assert results["12"].laps == 11
    assert results["12"].cards == all_eleven[:10]
    assert results["12"].hand == best_hand(all_eleven[:10])
    assert compare(best_hand(all_eleven[:10]), best_hand(all_eleven)) == -1
    assert engine._shoe.dealt == 11  # deal accounting unchanged past the cap


def test_snapshot_cap_applies_to_pooled_team_total() -> None:
    """R-16 pooling: the cap slices the team's pooled total (R-13)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1, max_cards=3)
    engine, _ = _make_engine(roster=roster, config=config)
    engine.start()
    _record_crossings(engine, "45", 5, start_at=_dt(10, 1), step_s=60)

    results = {entry.plate: entry for entry in engine.snapshot()}

    reference = Shoe(decks=1, jokers_per_deck=0, seed=20260920)
    expected = tuple(reference.deal()[0] for _ in range(3))
    assert results["9"].laps == 5
    assert results["9"].cards == expected
    assert results["9"].hand == best_hand(expected)


def test_snapshot_uncapped_default_scores_every_dealt_card() -> None:
    """max_cards=None (default) scores every credited card (R-16)."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1)
    engine, _ = _make_engine(config=config)
    engine.start()
    for index in range(1, 7):
        engine.record_crossing("12", at=_dt(10, index))

    results = {entry.plate: entry for entry in engine.snapshot()}

    reference = Shoe(decks=1, jokers_per_deck=0, seed=20260920)
    expected = tuple(reference.deal()[0] for _ in range(6))
    assert results["12"].cards == expected
    assert results["12"].hand == best_hand(expected)


# ------------------------------------------- E4.3 manual deal (spec §4)


def test_deal_manual_credits_card_marks_has_data_and_audits_reason() -> None:
    """deal_manual credits one card and audits plate/card/reason."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    engine.start()

    event = engine.deal_manual("12", reason="replacement card")

    assert event.action == "deal_manual"
    assert (event.payload["plate"], event.payload["entry_id"]) == ("12", "12")
    assert event.payload["reason"] == "replacement card"
    manual_card = Card.parse(str(event.payload["card"]))
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (manual_card,)
    assert results["12"].hand == best_hand((manual_card,))
    assert roster.entries[0].has_data is True
    assert engine._shoe.dealt == 1


def test_deal_manual_pooled_rider_plate_credits_the_team() -> None:
    """A rider_pooled rider's plate credits their team (R-16)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(name="Sarah", plate="45"), Rider(name="Priya", plate="9")],
    )
    engine, _ = _make_engine(roster=roster)
    engine.start()

    event = engine.deal_manual("45", reason="replacement")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert event.payload["entry_id"] == "9"
    assert results["9"].cards == (Card.parse(str(event.payload["card"])),)


@pytest.mark.parametrize(
    ("start_state", "match"),
    [
        ("draft", "cannot deal manually from draft"),
        ("finished", "cannot deal manually from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_deal_manual_from_non_live_state_raises_illegal_state_error(
    start_state: str, match: str
) -> None:
    """deal_manual is a live-ride action: DRAFT/FINISHED raise."""
    engine, _ = _engine_in(start_state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.deal_manual("12", reason="replacement")


def test_deal_manual_unknown_plate_raises_unknown_plate_error() -> None:
    """An unresolvable plate raises UnknownPlateError, never credits."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate")):
        engine.deal_manual("999", reason="replacement")


def test_deal_manual_from_reopened_after_finish_raises_shoe_closed_error() -> None:
    """REOPENED passes the gate; the closed shoe refuses."""
    engine, _ = _make_engine()
    engine.start()
    engine.finish()
    engine.reopen()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        engine.deal_manual("12", reason="replacement")


def test_deal_manual_respects_card_cap() -> None:
    """A manual card past max_cards deals but never scores (R-13)."""
    config = _config(deck_count=1, jokers_per_deck=0, min_lap_s=1, max_cards=1)
    engine, _ = _make_engine(config=config)
    engine.start()
    crossing = engine.record_crossing("12", at=_dt(10, 1))

    manual = engine.deal_manual("12", reason="replacement card")

    results = {entry.plate: entry for entry in engine.snapshot()}
    manual_card = Card.parse(str(manual.payload["card"]))
    assert results["12"].cards == (crossing.card,)
    assert results["12"].hand == best_hand((crossing.card,))
    assert manual_card not in results["12"].cards
    assert engine._shoe.dealt == 2  # still dealt, just not scored


def test_deal_manual_credits_directly_never_releases_held_card() -> None:
    """deal_manual never bypasses the held queue (R-34)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))  # short lap -> card held
    held_before = engine.held_crossings()

    manual = engine.deal_manual("12", reason="replacement card")

    manual_card = Card.parse(str(manual.payload["card"]))
    assert engine.held_crossings() == held_before
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (manual_card,)
    assert results["12"].hand == best_hand((manual_card,))


# ----------------------------- E4.3 shoe close on Finish (spec §4)


def test_finish_closes_the_shoe_and_later_deals_raise_shoe_closed_error() -> None:
    """finish() closes the shoe; every later deal raises (E2.2.1)."""
    config = _config()
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=_FakeClock(config.planned_start),
        roster=_roster_with_entries("12"),
    )
    engine.start()

    engine.finish()

    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        shoe.deal()


def test_record_crossing_after_finish_refuses_without_dealing() -> None:
    """Crossings after finish refuse; no card leaves the shoe."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()

    result = engine.record_crossing("12", at=_dt(10, 31))

    assert result.accepted is False
    assert result.reason == "ride is not running"
    assert engine._shoe.dealt == 1


def test_undo_last_from_reopened_after_finish_reverses_with_closed_shoe() -> None:
    """REOPENED undo still reverses; the card retires with the shoe."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()
    engine.reopen()

    engine.undo_last()

    assert engine.lap_times("12") == ()
    assert engine._shoe.dealt == 1  # closed shoe: card not returned


# ------------------------------------- E4.4.1 console read accessors


def test_engine_crossings_property_returns_recorded_crossings_oldest_first() -> None:
    """The feed read seam: every recorded crossing, oldest first."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 31))

    crossings = engine.crossings

    assert len(crossings) == 2
    assert [c.seq for c in crossings] == [1, 2]
    assert crossings[0].entry_id == "12"
    assert crossings[0].crossed_at == _dt(10, 30)


def test_engine_card_for_returns_the_card_dealt_for_a_recorded_crossing() -> None:
    """The feed's Card column reads the per-crossing deal (R-40)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    crossing = engine.crossings[-1]

    card = engine.card_for(crossing)

    assert card.code() == engine._shoe._cards[0].code()


def test_engine_card_for_given_an_unknown_crossing_raises_key_error() -> None:
    """Negative: a crossing never dealt has no card to show."""
    engine, _ = _make_engine()
    engine.start()
    crossing = Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 30))

    with pytest.raises(KeyError, match=re.escape(str(crossing))):
        engine.card_for(crossing)


def test_engine_shoe_remaining_and_total_track_the_current_cycle() -> None:
    """The Shoe counter's source: remaining + dealt = cycle total."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    assert engine.shoe_total == 432  # 8 decks x (52 + 2 jokers)
    assert engine.shoe_remaining == 431


def test_engine_config_property_returns_the_frozen_setup_config() -> None:
    """Ride metadata (name/date) the library source reads stays kept."""
    engine, _ = _make_engine()

    assert engine.config is not None
    assert engine.config.name == "GORBA EPIC 2026"


# ======================= E5.1.2 replay seam: apply (task-briefs.md)


def test_apply_start_event_transitions_to_running_and_records_event() -> None:
    """apply("start") rebuilds RUNNING from the payload actual_start."""
    engine, _ = _make_engine()
    event = Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})

    engine.apply(event)

    assert engine.state is RideStatus.RUNNING
    assert engine.events == (event,)


def test_apply_continue_event_keeps_actual_start_unchanged() -> None:
    """apply("continue") resumes a RUNNING engine; start stays put."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(action="continue", payload={"actual_start": "2026-09-20T10:00:00"})

    engine.apply(event)

    assert engine.state is RideStatus.RUNNING
    assert engine.elapsed() == 0.0
    assert engine.events[-1] == event


def test_apply_record_crossing_event_credits_lap_and_deals_deterministic_card() -> None:
    """apply("record_crossing") credits one lap from the payload."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start(at=_dt(10, 0))
    event = Event(
        action="record_crossing",
        payload={
            "plate": "12",
            "entry_id": "12",
            "lap": 1,
            "crossed_at": "2026-09-20T10:02:00",
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (120.0,)
    assert engine.events[-1] == event


def test_apply_set_start_time_event_backdates_actual_start() -> None:
    """apply("set_start_time") recomputes lap-1 from the payload."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 2))
    event = Event(
        action="set_start_time",
        payload={
            "actual_start": "2026-09-20T09:55:00",
            "previous_start": "2026-09-20T10:00:00",
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (420.0,)
    assert engine.events[-1] == event


def test_apply_confirm_held_event_releases_held_card_into_the_hand() -> None:
    """apply("confirm_held") releases a held card by entry/seq."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    event = Event(
        action="confirm_held",
        payload={"entry_id": "12", "seq": 1, "card": held.card.code()},
    )

    engine.apply(event)

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (held.card,)
    assert engine.events[-1] == event


def test_apply_void_held_event_discards_held_card_never_credited() -> None:
    """apply("void_held") discards the held card, never credited."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    event = Event(
        action="void_held",
        payload={"entry_id": "12", "seq": 1, "card": held.card.code()},
    )

    engine.apply(event)

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == ()
    assert engine.events[-1] == event


def test_apply_undo_event_reverses_the_last_crossing() -> None:
    """apply("undo") reverses the most recent crossing (R-33)."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 32))
    event = Event(
        action="undo",
        payload={
            "entry_id": "12",
            "seq": 2,
            "crossed_at": "2026-09-20T10:32:00",
            "card": second.card.code(),
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (1800.0,)
    assert engine.events[-1] == event


def test_apply_deal_manual_event_credits_card_with_the_payload_reason() -> None:
    """apply("deal_manual") deals one card with the payload reason."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    reference = Shoe(decks=8, jokers_per_deck=2, seed=20260920)
    expected = reference.deal()[0]
    event = Event(
        action="deal_manual",
        payload={
            "plate": "12",
            "entry_id": "12",
            "card": expected.code(),
            "reason": "replacement card",
        },
    )

    engine.apply(event)

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (expected,)
    assert engine.events[-1] == event


def test_apply_stop_event_locks_entry_with_refusal_result() -> None:
    """apply("stop") locks plate entry; the ride stays RUNNING."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(action="stop", payload={"stopped_at": "2026-09-20T10:01:00"})

    engine.apply(event)

    result = engine.record_crossing("12")
    assert result.accepted is False
    assert result.reason == "ride is stopped"


def test_apply_finish_event_closes_shoe_and_marks_finished() -> None:
    """apply("finish") transitions to FINISHED and closes the shoe."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(action="finish", payload={"finished_at": "2026-09-20T12:00:00"})

    engine.apply(event)

    assert engine.state is RideStatus.FINISHED
    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        engine._shoe.deal()


def test_apply_reopen_event_returns_finished_ride_to_reopened() -> None:
    """apply("reopen") moves FINISHED -> REOPENED (corrections-only)."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.finish()
    event = Event(action="reopen", payload={"reopened_at": "2026-09-20T12:05:00"})

    engine.apply(event)

    assert engine.state is RideStatus.REOPENED


def test_apply_shoe_reshuffle_event_is_a_noop_not_a_reshuffle() -> None:
    """Replaying the reshuffle event never double-reshuffles the shoe.

    The event is the audit record of a reshuffle the deal loop already
    performed; on replay the next deal (not this event) reproduces it
    when the fresh shoe empties (spec section 4, task-briefs E5.1.2's
    own "do not store the dealt card" decision).
    """
    engine, _ = _make_engine(config=_config(deck_count=1, jokers_per_deck=0, min_lap_s=1))
    engine.start(at=_dt(10, 0))
    event = Event(action="shoe_reshuffle", payload={"cycle": 2})

    engine.apply(event)

    assert engine.events == (
        Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
    )
    assert engine._shoe.cycle == 1


def test_apply_unknown_event_action_raises_unknown_event_action_error() -> None:
    """An event the dispatch does not know fails loudly (negative)."""
    engine, _ = _make_engine()

    with pytest.raises(UnknownEventActionError, match=re.escape("bogus_action")):
        engine.apply(Event(action="bogus_action", payload={}))


def test_apply_confirm_held_for_an_unrecorded_crossing_raises_clear_error() -> None:
    """confirm_held for a crossing the engine never saw fails loudly."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(
        action="confirm_held",
        payload={"entry_id": "12", "seq": 99, "card": "AS"},
    )

    with pytest.raises(RideEngineError, match=re.escape("no crossing")):
        engine.apply(event)
