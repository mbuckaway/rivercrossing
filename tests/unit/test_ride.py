# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.ride's RideConfig and RideEngine.

RideConfig (E3.5.1) was written first, against a ``RideConfig`` that
did not exist yet (R-70): module-skeletons.md S4's own reserved name
-- ``RideEngine.__init__(config: RideConfig, ...)`` in E4 -- pre-created
it here next to ``RideStatus``, mirroring how ``RideStatus`` itself was
pre-created ahead of the state machine that consumes it. Boundary rows
follow this repo's own T-4 convention (min-1, min, min+1, max-1, max,
max+1) for every bounded field: ``max_team_size`` (2..10, R-12),
``deck_count`` (>=1, spec.md §4), ``jokers_per_deck`` (0..10, Phase 5's
jokers_spin) and ``planned_duration_s``/``min_lap_s`` (positive,
spec.md §2/§6).

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
Finish with its reopen: ``finish()`` closes the shoe, ``reopen()``
re-opens it so ``deal_manual``/``add_crossing_at`` deal new cards in
REOPENED (spec §15), and undo in REOPENED returns the undone card to
the shoe front for the next deal to reproduce.
"""

import re
import tempfile
import time
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import _pooled_team_roster, _roster_with_entries
from rivercrossing.cards import Card, Shoe, ShoeClosedError
from rivercrossing.hands import best_hand, compare
from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_JOKERS_MODE,
    DEFAULT_JOKERS_PER_DECK,
    FAR_TOO_MANY,
    JOKERS_MODE_PER_DECK,
    JOKERS_MODE_TOTAL,
    NOT_ENOUGH,
    OK,
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
    CardCheck,
    Crossing,
    Event,
    IllegalStateError,
    PendingMiss,
    RideConfig,
    RideConfigError,
    RideEngine,
    RideEngineError,
    RideStatus,
    StartBlockedError,
    UnknownEventActionError,
    UnknownPlateError,
    check_card_sufficiency,
    estimate_cards_needed,
    setup_minimum_violations,
)
from rivercrossing.roster import (
    Entry,
    EntryMode,
    EntryStatus,
    EntryType,
    PlateModel,
    Rider,
    Roster,
)

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


def test_ride_config_bare_required_fields_defaults_jokers_per_deck_to_one() -> None:
    """jokers_spin's XRC value: 1 joker per deck (setup.xrc)."""
    config = _config()

    assert (config.jokers_per_deck, DEFAULT_JOKERS_PER_DECK) == (1, 1)


def test_ride_config_bare_required_fields_defaults_max_cards_to_uncapped() -> None:
    """cap_choice defaults to "Disabled": max_cards is None (R-13)."""
    config = _config()

    assert config.max_cards is None


def test_ride_config_bare_required_fields_defaults_tiebreak_order_to_high_card_first() -> None:
    """Phase 3's default: the venue's high-card draw leads (R-14)."""
    config = _config()

    assert config.tiebreak_order == (TIEBREAK_HIGH_CARD, TIEBREAK_LAPS, TIEBREAK_TOTAL_TIME)


def test_ride_config_bare_required_fields_defaults_logo_path_to_none() -> None:
    """No logo staged by default: the logo column reads "NO LOGO"."""
    config = _config()

    assert config.logo_path is None


def test_ride_config_bare_required_fields_defaults_hold_short_laps_to_false() -> None:
    """W4 default: a short lap always deals unless the operator opts in.

    The always-deal decision rides on ``hold_short_laps=False``, the
    field's dataclass default.
    """
    config = _config()

    assert config.hold_short_laps is False


def test_ride_config_given_a_logo_path_stores_it_verbatim() -> None:
    """A staged logo path round-trips exactly."""
    path = Path(tempfile.gettempdir()) / "gorba-logo.png"

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


# --------------------------------------- Phase 5: jokers mode + range
# The setup dialog's Phase 5 Cards controls: a jokers_spin over
# 0..10 and a per-deck/total radio pair (total checked, setup.xrc), so
# the dialog
# can neither build a count outside 0..10 nor store a mode spelling the
# shoe does not know.


def test_ride_config_bare_required_fields_defaults_jokers_mode_to_total() -> None:
    """jokers_total_radio's XRC default: one ride-wide joker budget."""
    config = _config()

    assert (config.jokers_mode, DEFAULT_JOKERS_MODE) == (JOKERS_MODE_TOTAL, JOKERS_MODE_TOTAL)


@pytest.mark.parametrize(
    "jokers_mode", [JOKERS_MODE_PER_DECK, JOKERS_MODE_TOTAL], ids=["per_deck", "total"]
)
def test_ride_config_jokers_mode_given_a_known_spelling_is_accepted(jokers_mode: str) -> None:
    """Both stored spellings round-trip onto the config unchanged."""
    config = _config(jokers_mode=jokers_mode)

    assert config.jokers_mode == jokers_mode


@pytest.mark.parametrize(
    "jokers_mode",
    ["perdeck", "Per_Deck", "per-deck", "both", ""],
    ids=["no_underscore", "mixed_case", "hyphen", "both", "empty"],
)
def test_ride_config_jokers_mode_given_an_unknown_spelling_raises(jokers_mode: str) -> None:
    """T-5: a mode the shoe cannot read refuses loudly, not silently."""
    with pytest.raises(RideConfigError, match=re.escape("jokers_mode")):
        _config(jokers_mode=jokers_mode)


@pytest.mark.parametrize("jokers_per_deck", [-1, 11], ids=["min-1", "max+1"])
def test_ride_config_jokers_per_deck_out_of_range_raises(jokers_per_deck: int) -> None:
    """T-4: jokers_spin's own 0..10 bound is enforced on the config."""
    with pytest.raises(RideConfigError, match=re.escape("jokers_per_deck")):
        _config(jokers_per_deck=jokers_per_deck)


@pytest.mark.parametrize(
    "jokers_per_deck", [0, 1, 2, 9, 10], ids=["min", "min+1", "two", "max-1", "max"]
)
def test_ride_config_jokers_per_deck_in_range_is_accepted(jokers_per_deck: int) -> None:
    """Every value the spinner offers is accepted as given."""
    config = _config(jokers_per_deck=jokers_per_deck)

    assert config.jokers_per_deck == jokers_per_deck


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


# ----------------------------------------- minimum-setup rule


def test_setup_minimum_violations_complete_config_returns_empty_list() -> None:
    """A fully populated config clears the minimum-setup rule."""
    config = _config()

    assert setup_minimum_violations(config) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("name", "", "name is required"),
        ("name", "   ", "name is required"),
        ("venue", "", "venue is required"),
        ("venue", "\t", "venue is required"),
        ("organizer", "", "organizer is required"),
        ("organizer", " \t ", "organizer is required"),
        ("scorer", "", "scorer is required"),
        ("scorer", "  ", "scorer is required"),
        ("lap_km", 0.0, "lap length must be positive"),
        ("lap_km", -0.5, "lap length must be positive"),
    ],
    ids=[
        "name_blank",
        "name_whitespace",
        "venue_blank",
        "venue_whitespace",
        "organizer_blank",
        "organizer_whitespace",
        "scorer_blank",
        "scorer_whitespace",
        "lap_km_zero",
        "lap_km_negative",
    ],
)
def test_setup_minimum_violations_blank_or_nonpositive_field_reports_its_reason(
    field: str, value: object, reason: str
) -> None:
    """One reason per missing/blank field, whitespace stripped (T-3)."""
    config = _config(**{field: value})

    assert setup_minimum_violations(config) == [reason]


def test_setup_minimum_violations_fully_blank_config_reports_every_reason_in_order() -> None:
    """Each missing field and the lap bound earns exactly one reason."""
    config = _config(name="", venue=" ", organizer="", scorer="  ", lap_km=0.0)

    assert setup_minimum_violations(config) == [
        "name is required",
        "venue is required",
        "organizer is required",
        "scorer is required",
        "lap length must be positive",
    ]


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


# ---------------------------------------------- event sink (E9.1.3)
# The one seam EVERY engine mutation persists through: an optional
# on_event callback receives each event as _append records it. The app
# attaches Store.append to it after load_engine's replay completes, so
# live mutations -- crossings, undo, corrections, lifecycle -- write
# one audit row each, and the replayed tail is never re-persisted.


def test_engine_on_event_receives_every_event_appended_after_wiring() -> None:
    """The sink sees each live mutation, exactly matching the log."""
    received: list[Event] = []
    engine, clock = _make_engine()
    engine.on_event = received.append
    engine.start()
    clock.advance(60)
    engine.record_crossing("12")
    engine.undo_last()

    assert received == list(engine.events)
    assert [event.action for event in received] == ["start", "record_crossing", "undo"]


def test_engine_on_event_receives_the_exact_crossing_payload() -> None:
    """The sink gets the very event record_crossing appended."""
    received: list[Event] = []
    engine, clock = _make_engine()
    engine.start()
    engine.on_event = received.append
    clock.advance(60)

    engine.record_crossing("12")

    assert received == [
        Event(
            action="record_crossing",
            payload={
                "plate": "12",
                "entry_id": "12",
                "lap": 1,
                "crossed_at": "2026-09-20T10:01:00",
            },
        )
    ]


def test_engine_on_event_never_receives_events_recorded_before_wiring() -> None:
    """Replay-safety: events before the sink attaches stay silent.

    E9.1.3: Store.load_engine replays persisted events onto a fresh
    engine whose sink is not yet attached; a later attach must not
    re-persist that tail.
    """
    received: list[Event] = []
    engine, _clock = _make_engine()
    engine.start()
    engine.record_crossing("12")
    engine.on_event = received.append

    engine.undo_last()

    assert [event.action for event in received] == ["undo"]


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
    """reopen() moves FINISHED -> REOPENED and re-opens the shoe (R-64).

    The shoe closes on Finish (E4.3); reopening a finished ride re-opens
    it so corrections can deal new cards (spec §15, E7.1.1).
    """
    engine, _ = _make_engine()
    engine.start()
    engine.finish()

    engine.reopen()

    assert engine.state is RideStatus.REOPENED
    assert engine.events[-1].action == "reopen"
    assert engine._shoe.is_closed is False


def test_finish_again_from_reopened_transitions_to_finished() -> None:
    """finish() re-locks REOPENED -> FINISHED (spec §3)."""
    engine, _ = _make_engine()
    engine.start()
    engine.finish()
    engine.reopen()

    engine.finish()

    assert engine.state is RideStatus.FINISHED
    assert engine.events[-1].action == "finish"


# ---------------------------------------------- C2 REOPENED -> RUNNING
# Continue riding from the corrections state: the console's Start
# button/row is enabled in REOPENED (C2), so the engine must accept it
# -- keep the recorded actual_start, clear the stop guard, move the
# roster back to RUNNING and append a ``continue`` audit row.


def test_start_from_reopened_continues_the_ride_and_keeps_actual_start() -> None:
    """C2: Start on REOPENED rides on (continue), never a new gun."""
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(600)
    engine.finish()
    engine.reopen()

    event = engine.start()

    assert engine.state is RideStatus.RUNNING
    assert event.action == "continue"
    assert event.payload == {"actual_start": "2026-09-20T10:00:00"}
    assert engine._roster.status is RideStatus.RUNNING
    assert engine.stopped is False


def test_start_from_reopened_discards_the_closed_finish_instant() -> None:
    """C2: continuing reopens the clock -- closed_elapsed is zero."""
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(600)
    engine.finish()
    engine.reopen()

    engine.start()

    assert engine.closed_elapsed() == 0.0


# ------------------------------------------- C3 closed-ride elapsed
# FINISHED and REOPENED show a frozen final time: the engine records
# the finish instant (``finish()``), preserves it through ``reopen()``
# and replay (``apply``), and exposes it as ``closed_elapsed()`` so the
# console never advances a closed ride's clock.


def test_closed_elapsed_given_a_finished_ride_reports_the_recorded_finish() -> None:
    """C3: closed_elapsed() is finished_at - actual_start."""
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(1234)
    engine.finish()

    assert engine.closed_elapsed() == pytest.approx(1234.0)


def test_closed_elapsed_given_a_reopened_ride_keeps_the_finish_elapsed() -> None:
    """C3: reopen preserves the finish instant -- clock stays closed."""
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(500)
    engine.finish()
    clock.advance(9999)
    engine.reopen()

    assert engine.closed_elapsed() == pytest.approx(500.0)


def test_closed_elapsed_given_a_never_started_ride_returns_zero() -> None:
    """C3 boundary: no actual_start means no elapsed to close."""
    engine, _ = _make_engine()

    assert engine.closed_elapsed() == 0.0


@pytest.mark.parametrize(
    ("start_state", "method", "match"),
    [
        ("finished", "start", "cannot start"),
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
    roster.create_team_entry_of_one(
        display_name="Half Team", rider=Rider(first_name="Bo", last_name="", plate="7")
    )
    engine, _ = _make_engine(roster=roster)

    with pytest.raises(StartBlockedError, match=re.escape("team size must be at least 2")):
        engine.start()

    assert engine.state is RideStatus.DRAFT
    assert engine.events == ()


def test_start_with_empty_roster_raises_start_blocked_and_stays_draft() -> None:
    """An empty roster blocks start; the ride stays DRAFT."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    engine, _ = _make_engine(roster=roster)

    with pytest.raises(StartBlockedError, match=re.escape("roster has no riders")):
        engine.start()

    assert engine.state is RideStatus.DRAFT
    assert engine.events == ()


def test_start_with_missing_setup_field_raises_start_blocked_and_stays_draft() -> None:
    """A blank venue blocks start; no state change (setup rule)."""
    engine, _ = _make_engine(config=_config(venue=""))

    with pytest.raises(
        StartBlockedError,
        match=re.escape("ride setup is incomplete: venue is required"),
    ):
        engine.start()

    assert engine.state is RideStatus.DRAFT
    assert engine.events == ()


def test_start_with_multiple_missing_setup_fields_joins_every_reason() -> None:
    """start() joins every missing-field reason into the refusal."""
    engine, _ = _make_engine(config=_config(name="", venue="", lap_km=0.0))

    with pytest.raises(
        StartBlockedError,
        match=re.escape("name is required; venue is required; lap length must be positive"),
    ):
        engine.start()

    assert engine.state is RideStatus.DRAFT
    assert engine.events == ()


# ------------------------------ start gate: structured reasons


def test_start_with_empty_roster_reports_the_single_reason_in_reasons() -> None:
    """A blocked start carries its one reason structured (Phase 5)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    engine, _ = _make_engine(roster=roster)

    with pytest.raises(StartBlockedError, match=re.escape("roster has no riders")) as excinfo:
        engine.start()

    assert excinfo.value.reasons == ("roster has no riders",)
    assert str(excinfo.value) == "roster has no riders"


def test_start_with_setup_violations_reports_each_field_in_reasons() -> None:
    """Every setup violation lands in ``reasons``, one entry each."""
    engine, _ = _make_engine(config=_config(name="", venue="", lap_km=0.0))

    with pytest.raises(
        StartBlockedError,
        match=re.escape("name is required; venue is required; lap length must be positive"),
    ) as excinfo:
        engine.start()

    assert excinfo.value.reasons == (
        "name is required",
        "venue is required",
        "lap length must be positive",
    )
    assert str(excinfo.value) == (
        "ride setup is incomplete: name is required; venue is required; "
        "lap length must be positive"
    )


def test_start_with_roster_violations_reports_plate_prefixed_reasons() -> None:
    """Each roster violation lands in ``reasons`` as "plate: reason"."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry_of_one(
        display_name="Half Team", rider=Rider(first_name="Bo", last_name="", plate="7")
    )
    engine, _ = _make_engine(roster=roster)

    with pytest.raises(
        StartBlockedError,
        match=re.escape("7: team size must be at least 2, got 1"),
    ) as excinfo:
        engine.start()

    assert excinfo.value.reasons == ("7: team size must be at least 2, got 1",)
    assert str(excinfo.value) == (
        "roster is not ready to start: 7: team size must be at least 2, got 1"
    )


def test_start_blocked_error_given_no_reasons_raises_type_error() -> None:
    """``reasons`` is required keyword-only, never defaulted."""
    with pytest.raises(TypeError, match="reasons"):
        StartBlockedError("roster has no riders")


@given(message=st.text(min_size=1), reasons=st.lists(st.text(), max_size=5))
def test_start_blocked_error_structured_reasons_never_change_the_message(
    message: str, reasons: list[str]
) -> None:
    """The joined message survives whatever ``reasons`` carries.

    Round-trip invariant for Phase 5's wiring: every pre-Phase-5 caller
    reads ``str(exc)``, so adding structured reasons must leave it --
    and ``args`` -- byte-for-byte the message it was raised with.
    """
    exc = StartBlockedError(message, reasons=tuple(reasons))

    assert str(exc) == message
    assert exc.args == (message,)
    assert exc.reasons == tuple(reasons)


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
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
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


def test_snapshot_includes_dnf_entries_with_dnf_flag() -> None:
    """DNF'd entries stay listed, marked dnf=True for rank (spec §6)."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    # Arranged directly; mark_dnf (E7.1.1) sets it.
    roster.entries[0].status = EntryStatus.DNF
    results = engine.snapshot()

    assert [result.plate for result in results] == ["12", "34"]
    assert results[0].dnf is True
    assert results[1].dnf is False


def test_snapshot_forfeits_a_dnf_team_riders_cards_and_keeps_the_team() -> None:
    """A DNF rider's cards leave the pool; the team stays in."""
    engine = _pooled_team_engine()
    forfeited = engine.record_crossing("45", at=_dt(10, 2)).card
    kept = engine.record_crossing("9", at=_dt(10, 4)).card

    engine.mark_dnf("45", reason="mechanical failure")

    results = {result.plate: result for result in engine.snapshot()}
    assert results["9"].dnf is False
    assert results["9"].cards == (kept,)
    assert results["9"].hand == best_hand((kept,))
    assert forfeited not in results["9"].cards


def test_snapshot_marks_a_team_dnf_when_every_rider_is_dnf() -> None:
    """All riders out means the entry is out -- excluded, cards gone."""
    engine = _pooled_team_engine()
    engine.record_crossing("45", at=_dt(10, 2))

    engine.mark_dnf("45", reason="mechanical failure")
    engine.mark_dnf("9", reason="mechanical failure")

    results = {result.plate: result for result in engine.snapshot()}
    assert results["9"].dnf is True
    assert results["9"].cards == ()


def test_snapshot_keeps_a_dnf_riders_cards_in_the_credited_hand_read() -> None:
    """Forfeiture is scoring-only: the Cards column still keeps them."""
    engine = _pooled_team_engine()
    card = engine.record_crossing("45", at=_dt(10, 2)).card

    engine.mark_dnf("45", reason="mechanical failure")

    assert engine.credited_cards("9") == (card,)


def test_snapshot_sets_a_solo_entries_sex_from_its_lone_rider() -> None:
    """A solo snapshot carries its rider's "M"/"F" (E7)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Luca", last_name="Ferrari", plate="12", sex="M")
    engine, _ = _make_engine(roster=roster)

    results = engine.snapshot()

    assert [result.sex for result in results] == ["M"]


def test_snapshot_leaves_a_solo_entries_sex_none_when_the_rider_has_none() -> None:
    """An unknown rider sex stays None; exports render it blank."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Luca", last_name="Ferrari", plate="12")
    engine, _ = _make_engine(roster=roster)

    results = engine.snapshot()

    assert [result.sex for result in results] == [None]


def test_snapshot_leaves_a_team_entries_sex_none_even_when_its_riders_have_one() -> None:
    """A team has no single sex, so its row never carries one."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45", sex="F"),
            Rider(first_name="Bo", last_name="", plate="9", sex="M"),
        ],
    )
    engine, _ = _make_engine(roster=roster)

    results = engine.snapshot()

    assert [result.sex for result in results] == [None]


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


def test_entry_count_given_empty_roster_returns_zero() -> None:
    """W5: the stop flow reads a zero-entry roster as no riders."""
    engine, _ = _make_engine(roster=_roster_with_entries())

    assert engine.entry_count == 0


def test_entry_count_given_two_entries_returns_two() -> None:
    """W5: the count matches the roster the engine was built over."""
    engine, _ = _make_engine(roster=_roster_with_entries("12", "34"))

    assert engine.entry_count == 2


def test_entry_count_tracks_entries_added_after_construction() -> None:
    """W5: the count is a live roster read, never a construction copy.

    The roster the engine holds is shared and mutable, so an entry
    added after ``RideEngine`` construction must appear in the count
    (mirrors ``on_course``'s own live read).
    """
    roster = _roster_with_entries("12")
    engine, _ = _make_engine(roster=roster)
    roster.create_solo_entry(first_name="Rider 34", last_name="", plate="34")

    assert engine.entry_count == 2


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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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


def test_record_crossing_short_lap_given_always_deal_flags_but_still_credits() -> None:
    """W4 default: a short lap flags for review AND credits its card.

    ``flagged`` is the review channel -- the FLAGGED audio cue and the
    Needs Review panel -- not the hold decision. Always-deal therefore
    flags the short lap while still crediting its card (R-34).
    """
    engine, _ = _make_engine()
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 0, 30))

    assert result.accepted is True
    assert result.flagged is True
    assert engine.held_crossings() == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].laps == 1
    assert results["12"].cards == (result.card,)


@pytest.mark.parametrize("hold_short_laps", [True, False], ids=["hold", "always_deal"])
def test_record_crossing_short_lap_flags_under_both_card_policies(
    hold_short_laps: bool,  # noqa: FBT001 -- parametrize passes the flag positionally
) -> None:
    """A lap under min_lap_s flags under both card policies.

    The policy decides the card's disposition -- held or credited --
    never whether the short lap is flagged for review.
    """
    engine, _ = _make_engine(config=_config(hold_short_laps=hold_short_laps))
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 0, 30))

    assert result.flagged is True


def test_record_crossing_given_always_deal_credits_every_accepted_lap() -> None:
    """E1 regression: Always Deal credits a card on every crossing.

    With ``hold_short_laps=False`` (the W4 default) the short-lap
    policy gate is the only thing that ever holds a card, so five
    crossings -- a very short opener, then laps one second under,
    exactly at, one second over ``min_lap_s``, and a long one --
    credit all five cards and leave the hold queue empty. The short
    laps are still flagged for review (the first two), which is a
    display fact independent of the credit decision.
    """
    engine, _ = _make_engine(config=_config(hold_short_laps=False, min_lap_s=600))
    engine.start()
    times = [_dt(10, 0, 5), _dt(10, 10, 4), _dt(10, 20, 4), _dt(10, 30, 5), _dt(11, 30, 5)]

    results = [engine.record_crossing("12", at=at) for at in times]

    results_by_plate = {entry.plate: entry for entry in engine.snapshot()}
    assert [result.accepted for result in results] == [True] * len(times)
    assert [result.flagged for result in results] == [True, True, False, False, False]
    assert results_by_plate["12"].laps == len(times)
    assert results_by_plate["12"].cards == tuple(result.card for result in results)
    assert engine.held_crossings() == ()


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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 17, 59))

    assert result.flagged is True
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == ()


def test_record_crossing_deals_the_shoe_next_card_in_deal_index_order() -> None:
    """Each accepted crossing deals shoe[deal_index++] in turn."""
    expected = Shoe(decks=8, jokers_per_deck=DEFAULT_JOKERS_PER_DECK, seed=20260920)
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
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]

    getattr(engine, action)(held.crossing)

    assert len(engine.held_crossings()) == expected_held
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert len(results["12"].cards) == expected_hand_cards


def test_confirm_held_returns_audit_event_and_best_hand_improves() -> None:
    """confirm_held writes an audit row; the credited hand improves."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    crossing = engine.held_crossings()[0].crossing
    engine.confirm_held(crossing)

    with pytest.raises(IllegalStateError, match=re.escape("crossing's card is not held")):
        engine.confirm_held(crossing)


def test_void_held_already_voided_crossing_raises_illegal_state_error() -> None:
    """void_held on a non-held crossing raises (R-34 negative)."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    normal = engine.record_crossing("12", at=_dt(10, 30))
    flagged = engine.record_crossing("12", at=_dt(10, 32))
    engine.confirm_held(engine.held_crossings()[0].crossing)

    results = {entry.plate: entry for entry in engine.snapshot()}

    assert results["12"].cards == (normal.card, flagged.card)
    assert results["12"].hand == best_hand((normal.card, flagged.card))


def test_snapshot_excludes_held_and_voided_cards_from_the_hand() -> None:
    """Held (unconfirmed) and voided cards never reach the hand."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
            "reason": "Undo last crossing",
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
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
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
    )
    engine, _ = _make_engine(roster=roster)
    engine.start()

    event = engine.deal_manual("45", reason="replacement")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert event.payload["entry_id"] == "9"
    assert results["9"].cards == (Card.parse(str(event.payload["card"])),)


def test_deal_manual_bonus_card_is_entry_scoped_and_survives_a_riders_dnf() -> None:
    """A bonus card carries no rider tag, so no DNF forfeits it.

    ``deal_manual`` credits the entry with no rider tag (the typed
    plate rides in the audit payload only), so a pooled rider's DNF
    forfeits only the cards their own crossings dealt -- the bonus card
    stays in the entry's scoring hand.
    """
    engine = _pooled_team_engine()
    manual = engine.deal_manual("45", reason="bonus card")
    bonus = Card.parse(str(manual.payload["card"]))
    engine.record_crossing("45", at=_dt(10, 2))

    engine.mark_dnf("45", reason="mechanical failure")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["9"].cards == (bonus,)


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


def test_deal_manual_from_reopened_after_finish_deals_and_audits() -> None:
    """REOPENED re-opens the shoe: deal_manual deals a new card.

    spec §15 lists the manual add as legal in RUNNING and REOPENED.
    """
    engine, _ = _make_engine()
    engine.start()
    engine.finish()
    engine.reopen()

    event = engine.deal_manual("12", reason="replacement")

    results = {entry.plate: entry for entry in engine.snapshot()}
    card = Card.parse(str(event.payload["card"]))
    assert event.action == "deal_manual"
    assert engine._shoe.dealt == 1
    assert engine._shoe.is_closed is False
    assert results["12"].cards == (card,)
    assert results["12"].hand == best_hand((card,))


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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    """finish() closes the shoe; every later deal raises (E2.2.1).

    The shoe stays closed while FINISHED -- only ``reopen()`` opens it
    again (see the REOPENED deal tests below).
    """
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

    assert shoe.is_closed is True
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


def test_undo_last_from_reopened_after_finish_returns_card_to_the_shoe() -> None:
    """REOPENED undo reverses; the card returns to the shoe front.

    reopen() re-opens the shoe, so undo's restitution succeeds: the
    undone card goes back to the front and the next correction deal
    reproduces it (deterministic continuation, R-40).
    """
    engine, _ = _make_engine()
    engine.start()
    crossing = engine.record_crossing("12", at=_dt(10, 30))
    engine.finish()
    engine.reopen()

    engine.undo_last()

    assert engine.lap_times("12") == ()
    assert engine._shoe.dealt == 0  # reopened shoe: card returned, not retired
    assert engine._shoe.deal()[0] == crossing.card  # next deal reproduces it


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


# --------------- crossings list: ride-start + DNF read accessors
# The console's crossings feed renders each crossing's elapsed time
# (``crossed_at`` minus the ride's start) and marks a DNF rider's rows,
# so those two facts need one public read each -- the feed is a wx-free
# presenter module and must not reach into the engine's privates.


def test_engine_actual_start_given_a_draft_ride_is_none() -> None:
    """T-4 boundary: a ride that never started has no elapsed origin."""
    engine, _ = _make_engine()

    assert engine.actual_start is None


def test_engine_actual_start_given_a_started_ride_returns_the_start_instant() -> None:
    """The start instant every elapsed value derives from."""
    engine, _ = _make_engine()
    engine.start()

    assert engine.actual_start == _dt(10, 0)


def test_engine_actual_start_given_a_back_dated_start_follows_the_correction() -> None:
    """``set_start_time`` moves the origin with it (spec §3, 3d)."""
    engine, _ = _make_engine()
    engine.start()

    engine.set_start_time(_dt(9, 55))

    assert engine.actual_start == _dt(9, 55)


def test_engine_dnf_riders_given_no_marks_is_empty() -> None:
    """T-4 boundary: nothing marked, nothing reported."""
    engine = _pooled_team_engine()

    assert engine.dnf_riders == frozenset()


def test_engine_dnf_riders_given_a_pooled_rider_mark_returns_that_plate() -> None:
    """The per-rider set names the rider's own typed plate."""
    engine = _pooled_team_engine()

    engine.mark_dnf("45", reason="mechanical failure")

    assert engine.dnf_riders == frozenset({"45"})


def test_engine_dnf_riders_given_a_solo_mark_keeps_the_set_empty() -> None:
    """A solo mark is the entry's own status, not a per-rider one."""
    engine, _ = _make_engine()
    engine.start()

    engine.mark_dnf("12", reason="withdrawn")

    assert engine.dnf_riders == frozenset()


def test_engine_entry_is_dnf_given_a_healthy_entry_is_false() -> None:
    """T-3 negative: an unmarked entry is still in the results."""
    engine = _pooled_team_engine()
    team = engine._roster.resolve_plate("9")  # the roster the engine was built with

    assert engine.entry_is_dnf(team) is False


def test_engine_entry_is_dnf_given_a_dnf_entry_status_is_true() -> None:
    """A DNF'd entry status (solo or relay) is out of the results."""
    roster = _roster_with_entries("12", "34")
    engine, _ = _make_engine(roster=roster)
    roster.entries[0].status = EntryStatus.DNF

    assert engine.entry_is_dnf(roster.entries[0]) is True


def test_engine_entry_is_dnf_given_a_pooled_team_with_one_rider_out_is_false() -> None:
    """One rider's DNF never takes the whole pooled team down."""
    engine = _pooled_team_engine()
    engine.mark_dnf("45", reason="mechanical failure")
    team = engine._roster.resolve_plate("9")  # the roster the engine was built with

    assert engine.entry_is_dnf(team) is False


def test_engine_entry_is_dnf_given_a_pooled_team_all_out_is_true() -> None:
    """Every rider out means the team is out (Phase 3's rule)."""
    engine = _pooled_team_engine()
    engine.mark_dnf("45", reason="mechanical failure")
    engine.mark_dnf("9", reason="mechanical failure")
    team = engine._roster.resolve_plate("9")  # the roster the engine was built with

    assert engine.entry_is_dnf(team) is True


def test_engine_entry_is_dnf_given_a_riderless_entry_is_false() -> None:
    """T-4 boundary: an empty team is never all-DNF."""
    engine, _ = _make_engine()
    riderless = Entry(plate="9", display_name="Dirt Dynamos", type=EntryType.TEAM)

    assert engine.entry_is_dnf(riderless) is False


# -------------------------- Phase 4: credited-cards read accessor


def test_credited_cards_given_no_crossings_returns_an_empty_tuple() -> None:
    """T-4 boundary: an entry that never crossed credits none."""
    engine, _ = _make_engine()
    engine.start()

    assert engine.credited_cards("12") == ()


def test_credited_cards_given_an_unknown_plate_returns_an_empty_tuple() -> None:
    """Negative: a plate no entry owns credits nothing, never raises."""
    engine, _ = _make_engine()
    engine.start()

    assert engine.credited_cards("999") == ()


def test_credited_cards_given_one_crossing_returns_the_dealt_card() -> None:
    """T-4 boundary: one lap credits exactly the card the shoe dealt."""
    engine, _ = _make_engine()
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 30))

    assert engine.credited_cards("12") == (result.card,)


def test_credited_cards_given_many_crossings_returns_them_in_deal_order() -> None:
    """T-4 boundary: many laps credit their cards, oldest first."""
    engine, _ = _make_engine()
    engine.start()
    first = engine.record_crossing("12", at=_dt(10, 30))
    second = engine.record_crossing("12", at=_dt(10, 31))
    third = engine.record_crossing("12", at=_dt(10, 32))

    assert engine.credited_cards("12") == (first.card, second.card, third.card)


def test_credited_cards_given_a_held_short_lap_credits_nothing_yet() -> None:
    """R-34: a held card is dealt but never credited until released."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))

    assert engine.credited_cards("12") == ()


def test_credited_cards_after_confirming_a_held_card_credits_it() -> None:
    """R-34: confirming a held card credits it."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    engine.confirm_held(held.crossing)

    assert engine.credited_cards("12") == (held.card,)


def test_credited_cards_after_voiding_a_held_card_still_credits_nothing() -> None:
    """R-34: a voided card never reaches the credited hand."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    engine.void_held(held.crossing)

    assert engine.credited_cards("12") == ()


def test_credited_cards_given_a_pooled_team_returns_the_entrys_whole_hand() -> None:
    """Cards belong to the entry: two riders' laps pool."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
    )
    engine, _ = _make_engine(roster=roster)
    engine.start()
    first = engine.record_crossing("45", at=_dt(10, 30))
    second = engine.record_crossing("9", at=_dt(10, 31))
    entry = roster.entries[0]

    assert engine.credited_cards(entry.plate) == (first.card, second.card)


# --------------------------------------- W9: held-card lookup (R-34)


def test_engine_held_card_for_given_a_held_crossing_returns_its_card() -> None:
    """A short-lap crossing's held card resolves by crossing (W9)."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    result = engine.record_crossing("12", at=_dt(10, 0, 30))
    crossing = engine.held_crossings()[0].crossing

    held_card = engine.held_card_for(crossing)

    assert held_card == result.card
    assert engine.card_for(crossing) == result.card  # dealt, not credited


def test_engine_held_card_for_given_a_credited_crossing_returns_none() -> None:
    """A normal lap's card is credited, never held -- None (W9)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    crossing = engine.crossings[-1]

    assert engine.held_card_for(crossing) is None


def test_engine_held_card_for_given_a_short_lap_under_always_deal_returns_none() -> None:
    """W4 default: a short lap credits like any other -- not held."""
    engine, _ = _make_engine()
    engine.start()

    engine.record_crossing("12", at=_dt(10, 0, 30))

    assert engine.held_crossings() == ()
    assert engine.held_card_for(engine.crossings[-1]) is None


@pytest.mark.parametrize(
    "release",
    ["confirm_held", "void_held"],
    ids=["released_by_confirm", "released_by_void"],
)
def test_engine_held_card_for_given_a_released_crossing_returns_none(release: str) -> None:
    """Confirm or void moves the card out of the hold queue (W9)."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    held = engine.held_crossings()[0]
    getattr(engine, release)(held.crossing)

    assert engine.held_card_for(held.crossing) is None


def test_engine_held_card_for_given_a_crossing_never_dealt_returns_none() -> None:
    """A stranger crossing is not in the hold queue -- None (W9)."""
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
    engine.start()
    crossing = Crossing(entry_id="12", seq=99, crossed_at=_dt(10, 30))

    assert engine.held_card_for(crossing) is None


def test_engine_shoe_remaining_and_total_track_the_current_cycle() -> None:
    """The Shoe counter's source: remaining + dealt = cycle total."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    assert engine.shoe_total == 424  # 8 decks x (52 + 1 joker)
    assert engine.shoe_remaining == 423


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


def test_apply_start_event_on_empty_roster_replays_running_state() -> None:
    """Replay restores a persisted start without re-judging live gates.

    A running ride whose start predates the empty-roster start gate
    (the E9.2.2 sim's deliberately empty TEAM_RELAY shell) must still
    resume: replay reproduces what was persisted -- that start already
    cleared the readiness gates when it ran live.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    engine, _ = _make_engine(roster=roster)
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
    engine, _ = _make_engine(config=_config(hold_short_laps=True))
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
            "reason": "Undo last crossing",
        },
    )

    engine.apply(event)

    assert engine.lap_times("12") == (1800.0,)
    assert engine.events[-1] == event


def test_apply_deal_manual_event_credits_card_with_the_payload_reason() -> None:
    """apply("deal_manual") deals one card with the payload reason."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    reference = Shoe(decks=8, jokers_per_deck=DEFAULT_JOKERS_PER_DECK, seed=20260920)
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
    assert engine._shoe.is_closed is True
    with pytest.raises(ShoeClosedError, match=re.escape("shoe is closed")):
        engine._shoe.deal()


def test_apply_reopen_event_returns_finished_ride_to_reopened() -> None:
    """apply("reopen") moves FINISHED -> REOPENED and re-opens the shoe.

    The replay of a ``reopen`` event must reproduce the open/closed
    transition exactly: the fresh replay shoe closes on ``finish`` and
    opens again on ``reopen``, so a replayed REOPENED deal_manual deals.
    """
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.finish()
    event = Event(action="reopen", payload={"reopened_at": "2026-09-20T12:05:00"})

    engine.apply(event)

    assert engine.state is RideStatus.REOPENED
    assert engine._shoe.is_closed is False


def test_apply_finish_event_re_applies_the_recorded_finish_instant() -> None:
    """C3: replay reads the persisted finished_at, not replay time.

    An old ride replayed today must show its own recorded finish, so
    ``closed_elapsed()`` uses the payload timestamp and the re-appended
    event keeps that payload.
    """
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(3600)  # the replay clock is nowhere near the finish instant
    event = Event(action="finish", payload={"finished_at": "2026-09-20T10:30:00"})

    engine.apply(event)

    assert engine.closed_elapsed() == pytest.approx(1800.0)
    assert engine.events[-1] == event


def test_apply_reopen_event_re_applies_the_recorded_reopened_instant() -> None:
    """C3: replay keeps the recorded reopen payload and finish."""
    engine, clock = _make_engine()
    engine.start(at=_dt(10, 0))
    clock.advance(3600)
    engine.apply(Event(action="finish", payload={"finished_at": "2026-09-20T10:01:40"}))
    event = Event(action="reopen", payload={"reopened_at": "2026-09-20T11:00:00"})

    engine.apply(event)

    assert engine.state is RideStatus.REOPENED
    assert engine.events[-1] == event
    assert engine.closed_elapsed() == pytest.approx(100.0)


def test_apply_replay_finish_reopen_deal_manual_is_equivalent() -> None:
    """Replaying finish/reopen/deal_manual reproduces the live state.

    E5.1.2's equivalence, extended to the reopened shoe: the replayed
    ``reopen`` opens the fresh shoe exactly as the live one, so the
    replayed ``deal_manual`` deals the identical next card at the same
    shoe point.
    """
    live, _ = _make_engine()
    live.start(at=_dt(10, 0))
    live.record_crossing("12", at=_dt(10, 30))
    live.finish()
    live.reopen()
    manual = live.deal_manual("12", reason="replacement")

    replayed, _ = _make_engine()
    for event in live.events:
        replayed.apply(event)

    replayed_manual = replayed.events[-1]
    assert replayed.state is live.state
    assert replayed._shoe.is_closed is live._shoe.is_closed
    assert (replayed._shoe.dealt, replayed._shoe.cycle) == (live._shoe.dealt, live._shoe.cycle)
    assert replayed.snapshot() == live.snapshot()
    assert replayed_manual.action == "deal_manual"
    assert replayed_manual.payload["card"] == manual.payload["card"]


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


# ============================================================ D2
# Edit Ride: the engine's config is the live ride's own settings, so an
# edit replaces it in place -- the ride, its shoe and its event log are
# untouched.


def test_engine_clock_returns_the_injected_clock_source() -> None:
    """The console-rebuild seam carries an injected clock (R-74)."""
    engine, clock = _make_engine()

    assert engine.clock is clock


def test_engine_update_config_replaces_the_live_config() -> None:
    """D2: an edited ride's settings become the engine's own config."""
    engine, _clock = _make_engine()
    edited = _config(name="Renamed Ride", venue="New Venue", lap_km=6.5)

    engine.update_config(edited)

    assert engine.config == edited


def test_engine_update_config_keeps_the_ride_state_and_its_events() -> None:
    """D2: editing setup settings never rewinds a started ride."""
    engine, _clock = _make_engine()
    engine.start()
    engine.record_crossing("12", at=engine.config.planned_start + timedelta(seconds=60))
    events_before = engine.events
    crossings_before = engine.crossings

    engine.update_config(_config(name="Renamed Ride"))

    assert engine.state is RideStatus.RUNNING
    assert engine.events == events_before
    assert engine.crossings == crossings_before


# D2's other half: a DRAFT shoe-structure edit also rebuilds the live
# shoe from the same stored seed, so the stored config and the shoe
# cannot silently diverge (SHOWMECHANICS.md's Edit-gating promise).
# Past DRAFT the shoe is untouched -- a FINISHED ride's closed shoe in
# particular must never be rebuilt.

_DRAFT_EDIT_SEED = 20260920


def _draft_engine_with_seed(seed: int) -> RideEngine:
    """Build a DRAFT engine over a per-deck shoe with a known seed."""
    config = _config(jokers_mode=JOKERS_MODE_PER_DECK)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=seed)
    return RideEngine(
        config=config,
        shoe=shoe,
        clock=_FakeClock(config.planned_start),
        roster=_roster_with_entries("12"),
    )


def test_engine_update_config_given_a_draft_deck_count_change_rebuilds_the_shoe() -> None:
    """D2: a DRAFT deck_count edit re-sizes the live shoe."""
    engine = _draft_engine_with_seed(_DRAFT_EDIT_SEED)

    engine.update_config(_config(jokers_mode=JOKERS_MODE_PER_DECK, deck_count=2))

    assert (engine.shoe_total, engine.shoe_remaining) == (2 * 53, 2 * 53)


def test_engine_update_config_given_a_draft_joker_change_re_deals_from_the_stored_seed() -> None:
    """D2: the rebuilt shoe keeps the seed and the composition."""
    engine = _draft_engine_with_seed(_DRAFT_EDIT_SEED)
    engine.update_config(
        _config(jokers_mode=JOKERS_MODE_PER_DECK, deck_count=2, jokers_per_deck=2)
    )
    engine.start()

    result = engine.record_crossing("12", at=_dt(10, 0, 30))

    reference = Shoe(decks=2, jokers_per_deck=2, seed=_DRAFT_EDIT_SEED)
    assert (engine.shoe_total, result.card) == (2 * 54, reference.deal()[0])


def test_engine_update_config_given_a_running_ride_keeps_the_live_shoe() -> None:
    """D2: a started ride's shoe is never rebuilt by a config edit."""
    engine = _draft_engine_with_seed(_DRAFT_EDIT_SEED)
    engine.start()
    engine.record_crossing("12", at=_dt(10, 0, 30))
    before = (engine.shoe_total, engine.shoe_remaining)

    engine.update_config(_config(jokers_mode=JOKERS_MODE_PER_DECK, deck_count=2))

    assert (engine.shoe_total, engine.shoe_remaining) == before


def test_engine_update_config_given_a_finished_ride_keeps_its_closed_shoe() -> None:
    """D2: a FINISHED ride's closed shoe is never rebuilt."""
    engine = _draft_engine_with_seed(_DRAFT_EDIT_SEED)
    engine.start()
    engine.finish()
    before = (engine.shoe_total, engine.shoe_remaining)

    engine.update_config(_config(jokers_mode=JOKERS_MODE_PER_DECK, deck_count=2))

    assert (engine.shoe_total, engine.shoe_remaining) == before


# ============================ J1: per-rider crossing attribution


def _pooled_team_engine() -> RideEngine:
    """Build a RUNNING engine over the pooled team roster."""
    engine, _ = _make_engine(roster=_pooled_team_roster(), config=_config(min_lap_s=1))
    engine.start()
    return engine


def test_crossing_given_no_rider_plate_defaults_to_none() -> None:
    """The field defaults last so positional builds still compile."""
    assert Crossing("12", 1, _dt(10, 0)).rider_plate is None


def test_record_crossing_given_pooled_rider_plate_stores_it_as_the_rider_plate() -> None:
    """J1: the typed rider plate rides on the crossing itself."""
    engine = _pooled_team_engine()

    engine.record_crossing("45", at=_dt(10, 2))

    assert engine.crossings[-1] == Crossing(
        entry_id="9", seq=1, crossed_at=_dt(10, 2), rider_plate="45"
    )


def test_record_crossing_given_entry_plate_stores_it_as_the_rider_plate() -> None:
    """A solo or relay crossing attributes the entry plate."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()

    engine.record_crossing("12", at=_dt(10, 2))

    assert engine.crossings[-1].rider_plate == "12"


def test_edit_crossing_preserves_the_typing_riders_plate() -> None:
    """A time-only correction never re-attributes the lap."""
    engine = _pooled_team_engine()
    engine.record_crossing("45", at=_dt(10, 2))

    engine.edit_crossing("9", 1, _dt(10, 3), reason="mis-keyed time")

    assert engine.crossings[-1].rider_plate == "45"


def test_add_crossing_at_stores_the_typed_plate_as_the_rider_plate() -> None:
    """A back-filled crossing attributes the typed plate."""
    engine = _pooled_team_engine()

    engine.add_crossing_at("45", _dt(10, 2), reason="missed crossing")

    assert engine.crossings[-1].rider_plate == "45"


def test_reassign_crossing_sets_the_new_plate_as_the_rider_plate() -> None:
    """Reassign is the wrong-plate fix: attribute the new plate."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 2))

    engine.reassign_crossing(1, "34", reason="mis-keyed plate")

    moved = [crossing for crossing in engine.crossings if crossing.entry_id == "34"]
    assert [crossing.rider_plate for crossing in moved] == ["34"]


def test_void_crossing_renumbering_preserves_the_remaining_riders_plate() -> None:
    """The renumber rebuild keeps each later lap's attributed rider."""
    engine = _pooled_team_engine()
    engine.record_crossing("9", at=_dt(10, 2))  # Priya, lap 1
    engine.record_crossing("45", at=_dt(10, 4))  # Sarah, lap 2

    engine.void_crossing("9", 1, reason="double entry")

    assert [(crossing.seq, crossing.rider_plate) for crossing in engine.crossings] == [(1, "45")]


def test_reassign_crossing_renumbering_preserves_the_remaining_riders_plate() -> None:
    """The source entry's closed-up laps keep their attribution."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 2))
    engine.record_crossing("12", at=_dt(10, 4))
    engine.record_crossing("34", at=_dt(10, 6))

    engine.reassign_crossing(1, "34", reason="mis-keyed plate")

    remaining = [crossing for crossing in engine.crossings if crossing.entry_id == "12"]
    assert [(crossing.seq, crossing.rider_plate) for crossing in remaining] == [(1, "12")]


def test_apply_record_crossing_event_rebuilds_the_typed_rider_plate() -> None:
    """E5.1.2: the payload's plate rebuilds the attribution."""
    roster = _pooled_team_roster()
    engine, _ = _make_engine(roster=roster, config=_config(min_lap_s=1))
    engine.start(at=_dt(10, 0))
    event = Event(
        action="record_crossing",
        payload={
            "plate": "45",
            "entry_id": "9",
            "lap": 1,
            "crossed_at": "2026-09-20T10:02:00",
        },
    )

    engine.apply(event)

    assert engine.crossings[-1].rider_plate == "45"


# ================================ K: record a miss (pending miss)
# A miss is a passing whose number the scorer did not catch: the
# operator types one of the miss symbols instead of a plate. The engine
# keeps it in a separate pending queue -- NOT in _crossings -- so no
# crossing-shaped consumer (card_for/undo_last/reassign_crossing/_laps/
# feed counters) sees it, and no card is dealt until
# assign_plate_to_miss resolves a real plate.


def test_pending_miss_is_frozen() -> None:
    """A pending miss is an immutable record (frozen dataclass)."""
    miss = PendingMiss(miss_seq=1, crossed_at=_dt(10, 5))

    with pytest.raises(FrozenInstanceError):
        miss.miss_seq = 2  # type: ignore[misc]


def test_pending_misses_before_any_miss_returns_empty_tuple() -> None:
    """A fresh engine holds no pending misses."""
    engine, _ = _engine_in("running")

    assert engine.pending_misses() == ()


@pytest.mark.parametrize("state", ["running", "reopened"], ids=["running", "reopened"])
def test_record_miss_given_a_live_state_appends_the_pending_miss_and_event(
    state: str,
) -> None:
    """RUNNING/REOPENED record one miss and one audit event."""
    engine, _ = _engine_in(state)

    event = engine.record_miss(_dt(10, 5), reason="missed number")

    assert event == Event(
        action="record_miss",
        payload={
            "miss_seq": 1,
            "crossed_at": "2026-09-20T10:05:00",
            "reason": "missed number",
        },
    )
    assert engine.pending_misses() == (PendingMiss(miss_seq=1, crossed_at=_dt(10, 5)),)
    assert engine.events[-1] == event


def test_record_miss_given_two_misses_numbers_them_in_recording_order() -> None:
    """miss_seq is a ride-wide 1-based ordinal, like Crossing.seq."""
    engine, _ = _engine_in("running")

    first = engine.record_miss(_dt(10, 5), reason="missed number")
    second = engine.record_miss(_dt(10, 6), reason="missed number")

    assert (first.payload["miss_seq"], second.payload["miss_seq"]) == (1, 2)
    assert [miss.miss_seq for miss in engine.pending_misses()] == [1, 2]


def test_record_miss_deals_no_card_and_leaves_the_crossings_untouched() -> None:
    """A miss is not a Crossing: no deal, no credited hand."""
    engine, _ = _engine_in("running")
    shoe_remaining = engine.shoe_remaining

    engine.record_miss(_dt(10, 5), reason="missed number")

    assert (engine.crossings, engine.shoe_remaining) == ((), shoe_remaining)
    assert engine.credited_cards("12") == ()
    assert engine.held_crossings() == ()


def test_record_miss_is_ignored_by_the_snapshot() -> None:
    """A pending miss contributes no laps, cards or total time."""
    engine, _ = _engine_in("running")

    engine.record_miss(_dt(10, 5), reason="missed number")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert (results["12"].laps, results["12"].cards, results["12"].total_time) == (0, (), 0.0)


@pytest.mark.parametrize(
    ("state", "match"),
    [
        ("draft", "cannot record miss from draft"),
        ("finished", "cannot record miss from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_record_miss_from_a_non_live_state_raises_illegal_state_error(
    state: str, match: str
) -> None:
    """A miss records only while RUNNING or REOPENED."""
    engine, _ = _engine_in(state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.record_miss(_dt(10, 5), reason="missed number")


@pytest.mark.parametrize("reason", ["", "   "], ids=["empty", "whitespace_only"])
def test_record_miss_given_a_blank_reason_raises_value_error(reason: str) -> None:
    """Every audited command requires a non-blank reason (R-33)."""
    engine, _ = _engine_in("running")

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.record_miss(_dt(10, 5), reason=reason)


# ------------------------------------------- assign_plate_to_miss


def test_assign_plate_to_miss_removes_the_miss_and_records_the_crossing() -> None:
    """The edit turns a pending miss into a crossing at its instant."""
    engine, _ = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")

    event = engine.assign_plate_to_miss(1, "12", reason="rider identified")

    assert event == Event(
        action="assign_plate_to_miss",
        payload={
            "miss_seq": 1,
            "new_plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:05:00",
            "reason": "rider identified",
        },
    )
    assert engine.pending_misses() == ()
    assert engine.crossings == (
        Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 5), rider_plate="12"),
    )
    assert engine.events[-1] == event


def test_assign_plate_to_miss_deals_exactly_one_card_into_the_hand() -> None:
    """The card is assigned at edit time, never at miss time (R-40)."""
    engine, _ = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")
    reference = Shoe(decks=8, jokers_per_deck=DEFAULT_JOKERS_PER_DECK, seed=20260920)

    engine.assign_plate_to_miss(1, "12", reason="rider identified")

    results = {entry.plate: entry for entry in engine.snapshot()}
    assert engine._shoe.dealt == 1
    assert results["12"].cards == (reference.deal()[0],)
    assert results["12"].laps == 1


def test_assign_plate_to_miss_records_the_crossing_at_the_miss_instant_not_now() -> None:
    """The crossing keeps the miss's crossed_at, not the edit time."""
    engine, clock = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")
    clock.advance(600)

    engine.assign_plate_to_miss(1, "12", reason="rider identified")

    assert engine.crossings[0].crossed_at == _dt(10, 5)


def test_assign_plate_to_miss_given_an_unknown_plate_raises_and_keeps_the_miss() -> None:
    """An unresolvable plate fails loudly, keeping the miss."""
    engine, _ = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")

    with pytest.raises(UnknownPlateError, match=re.escape("unknown plate: 999")):
        engine.assign_plate_to_miss(1, "999", reason="rider identified")

    assert [miss.miss_seq for miss in engine.pending_misses()] == [1]
    assert engine.crossings == ()


@pytest.mark.parametrize("miss_seq", [0, 2], ids=["min-1", "max+1"])
def test_assign_plate_to_miss_given_an_unknown_seq_raises_illegal_state_error(
    miss_seq: int,
) -> None:
    """Only a real pending miss's miss_seq resolves (boundary rows)."""
    engine, _ = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")

    with pytest.raises(
        IllegalStateError, match=re.escape(f"no pending miss with miss_seq {miss_seq}")
    ):
        engine.assign_plate_to_miss(miss_seq, "12", reason="rider identified")


@pytest.mark.parametrize(
    ("state", "match"),
    [
        ("draft", "cannot assign plate to miss from draft"),
        ("finished", "cannot assign plate to miss from finished"),
    ],
    ids=["draft_refused", "finished_refused"],
)
def test_assign_plate_to_miss_from_a_non_live_state_raises_illegal_state_error(
    state: str, match: str
) -> None:
    """The edit records only while RUNNING or REOPENED."""
    engine, _ = _engine_in(state)

    with pytest.raises(IllegalStateError, match=re.escape(match)):
        engine.assign_plate_to_miss(1, "12", reason="rider identified")


@pytest.mark.parametrize("reason", ["", "   "], ids=["empty", "whitespace_only"])
def test_assign_plate_to_miss_given_a_blank_reason_raises_value_error(reason: str) -> None:
    """Every audited command requires a non-blank reason (R-33)."""
    engine, _ = _engine_in("running")
    engine.record_miss(_dt(10, 5), reason="missed number")

    with pytest.raises(ValueError, match=re.escape("reason must not be empty")):
        engine.assign_plate_to_miss(1, "12", reason=reason)


# ----------------------------------------------- miss replay (E5.1.2)


def test_apply_record_miss_event_replays_the_pending_miss() -> None:
    """Replaying record_miss rebuilds the pending queue entry."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(
        action="record_miss",
        payload={
            "miss_seq": 1,
            "crossed_at": "2026-09-20T10:05:00",
            "reason": "missed number",
        },
    )

    engine.apply(event)

    assert engine.pending_misses() == (PendingMiss(miss_seq=1, crossed_at=_dt(10, 5)),)
    assert engine.events[-1] == event


def test_apply_assign_plate_to_miss_event_replays_the_crossing() -> None:
    """Replaying assign_plate_to_miss rebuilds the recorded crossing."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.apply(
        Event(
            action="record_miss",
            payload={
                "miss_seq": 1,
                "crossed_at": "2026-09-20T10:05:00",
                "reason": "missed number",
            },
        )
    )
    event = Event(
        action="assign_plate_to_miss",
        payload={
            "miss_seq": 1,
            "new_plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:05:00",
            "reason": "rider identified",
        },
    )

    engine.apply(event)

    assert engine.pending_misses() == ()
    assert engine.crossings == (
        Crossing(entry_id="12", seq=1, crossed_at=_dt(10, 5), rider_plate="12"),
    )
    assert engine.events[-1] == event


def test_apply_replay_record_miss_then_assign_is_equivalent() -> None:
    """Replaying a miss and its edit reproduces the live state."""
    live, _ = _make_engine()
    live.start(at=_dt(10, 0))
    live.record_miss(_dt(10, 5), reason="missed number")
    live.assign_plate_to_miss(1, "12", reason="rider identified")

    replayed, _ = _make_engine()
    # logic-coverage-exempt: T-8 -- the loop is pure Arrange
    # (re-applying the recorded log); assertions run after the loop.
    for event in live.events:
        replayed.apply(event)

    assert replayed.snapshot() == live.snapshot()
    assert replayed.pending_misses() == live.pending_misses()
    assert replayed._shoe.dealt == live._shoe.dealt


# ============================================================ plan §10
# Card-sufficiency estimate: the crossing count a ride expects from its
# field, and how the shoe's own card count compares to it. Pure helpers
# on RideConfig + Roster (no wx, R-71), so they are tested here beside
# ride.py's other pure surface.

# A lap length of 1 km at 3600 km/h makes one lap exactly 1 second, so
# ``planned_duration_s`` is the expected lap count verbatim -- the
# verdict boundary rows below can then name the default shoe's own 417
# cards (8 decks x 52 + 1 joker: the default config's jokers mode is
# ``total``).
_ONE_SECOND_LAP_KM = 1.0
_ONE_SECOND_LAP_SPEED_KMH = 3600.0

# The default shoe: DEFAULT_DECK_COUNT x 52 + DEFAULT_JOKERS_PER_DECK
# (the total-mode capacity check_card_sufficiency reports).
_DEFAULT_SHOE_CARDS = DEFAULT_DECK_COUNT * 52 + DEFAULT_JOKERS_PER_DECK


def _card_check_roster(*, plate_model: PlateModel = PlateModel.RIDER_POOLED) -> Roster:
    """Return a two-entry / four-rider roster for estimate tests."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=plate_model)
    roster.load_entries(
        [
            Entry(
                plate="1",
                display_name="Luca Ferrari",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Luca", last_name="Ferrari", plate="1")],
            ),
            Entry(
                plate="2",
                display_name="Dirt Dynamos",
                type=EntryType.TEAM,
                riders=[
                    Rider(first_name="Sarah", last_name="Okafor", plate="2"),
                    Rider(first_name="Priya", last_name="Nair", plate="3"),
                    Rider(first_name="Tom", last_name="Hale", plate="4"),
                ],
            ),
        ]
    )
    return roster


def _single_rider_roster() -> Roster:
    """Return a pooled roster holding one solo entry, one rider."""
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    roster.load_entries(
        [
            Entry(
                plate="7",
                display_name="Luca Ferrari",
                type=EntryType.SOLO,
                riders=[Rider(first_name="Luca", last_name="Ferrari", plate="7")],
            )
        ]
    )
    return roster


def _force_field(config: RideConfig, field: str, value: object) -> RideConfig:
    """Return *config* with one field forced past ``__post_init__``.

    ``RideConfig.__post_init__`` refuses a non-positive
    ``planned_duration_s`` (spec §2), so the estimate's own guard for
    that value is unreachable through a normally built config; forcing
    the field is the only way to exercise it (T-3).
    """
    object.__setattr__(config, field, value)
    return config


# -------------------------------------- estimate_cards_needed


def test_estimate_cards_needed_given_rider_pooled_counts_every_rider() -> None:
    """A pooled ride deals per rider: 4 riders x 27 laps = 108."""
    config = _config(lap_km=8.0, planned_duration_s=21600)

    result = estimate_cards_needed(config, _card_check_roster(), 36.0)

    assert result == 108


def test_estimate_cards_needed_given_team_relay_counts_entries_only() -> None:
    """A relay ride deals per entry: 2 entries x 27 laps = 54."""
    config = _config(lap_km=8.0, planned_duration_s=21600, plate_model=PlateModel.TEAM_RELAY)
    roster = _card_check_roster(plate_model=PlateModel.TEAM_RELAY)

    result = estimate_cards_needed(config, roster, 36.0)

    assert result == 54


def test_estimate_cards_needed_given_a_partial_lap_rounds_up() -> None:
    """T-3: a fractional lap count is a ceil (22.5 -> 23 laps x 4)."""
    config = _config(lap_km=8.0, planned_duration_s=21600)

    result = estimate_cards_needed(config, _card_check_roster(), 30.0)

    assert result == 92


def test_estimate_cards_needed_given_an_exact_lap_multiple_does_not_round_up() -> None:
    """T-3: an exact division stays put (24 laps x 4)."""
    config = _config(lap_km=8.0, planned_duration_s=21600)

    result = estimate_cards_needed(config, _card_check_roster(), 32.0)

    assert result == 96


@pytest.mark.parametrize("lap_km", [0.0, -1.0], ids=["zero", "negative"])
def test_estimate_cards_needed_given_a_nonpositive_lap_km_returns_none(lap_km: float) -> None:
    """T-3: no lap length, no estimate."""
    config = _config(lap_km=lap_km)

    result = estimate_cards_needed(config, _card_check_roster(), 36.0)

    assert result is None


@pytest.mark.parametrize("speed", [0.0, -12.0], ids=["zero", "negative"])
def test_estimate_cards_needed_given_a_nonpositive_speed_returns_none(speed: float) -> None:
    """T-3: a stopped field never finishes a lap."""
    config = _config(lap_km=8.0)

    result = estimate_cards_needed(config, _card_check_roster(), speed)

    assert result is None


def test_estimate_cards_needed_given_a_nonpositive_duration_returns_none() -> None:
    """T-3: zero planned duration has no laps to estimate."""
    config = _force_field(_config(lap_km=8.0), "planned_duration_s", 0)

    result = estimate_cards_needed(config, _card_check_roster(), 36.0)

    assert result is None


def test_estimate_cards_needed_given_an_empty_roster_returns_none() -> None:
    """T-4: no entries, no estimate (collection min = empty)."""
    config = _config(lap_km=8.0)

    result = estimate_cards_needed(config, Roster(), 36.0)

    assert result is None


def test_estimate_cards_needed_given_a_single_rider_roster_returns_the_lap_count() -> None:
    """T-4: the smallest non-empty roster (one entry, one rider)."""
    config = _config(lap_km=8.0, planned_duration_s=21600)

    result = estimate_cards_needed(config, _single_rider_roster(), 36.0)

    assert result == 27


@given(
    speeds=st.lists(
        st.floats(min_value=1.0, max_value=400.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=4,
    )
)
def test_estimate_cards_needed_given_any_two_speeds_is_monotonic_in_speed(
    speeds: list[float],
) -> None:
    """T-7 property: a faster field never needs fewer cards."""
    config = _config(lap_km=8.0, planned_duration_s=21600)
    roster = _card_check_roster()

    slower = min(speeds)
    faster = max(speeds)

    assert estimate_cards_needed(config, roster, slower) <= estimate_cards_needed(
        config, roster, faster
    )


# ------------------------------------- check_card_sufficiency


@pytest.mark.parametrize(
    ("planned_duration_s", "verdict"),
    [
        (1, FAR_TOO_MANY),  # T-4: far below the 2x boundary
        (208, FAR_TOO_MANY),  # T-4: 2x boundary - 1
        (209, OK),  # T-4: the 2x boundary itself
        (417, OK),  # T-4: the 1x boundary (exactly the shoe)
        (418, NOT_ENOUGH),  # T-4: 1x boundary + 1
    ],
    ids=["far_below", "below_2x", "at_2x", "at_1x", "above_1x"],
)
def test_check_card_sufficiency_given_each_boundary_returns_its_verdict(
    planned_duration_s: int, verdict: str
) -> None:
    """The shoe's 1x/2x boundaries decide the three verdicts."""
    config = _config(
        lap_km=_ONE_SECOND_LAP_KM,
        planned_duration_s=planned_duration_s,
        jokers_mode=JOKERS_MODE_TOTAL,
    )

    result = check_card_sufficiency(config, _single_rider_roster(), _ONE_SECOND_LAP_SPEED_KMH)

    assert result == CardCheck(
        shoe_cards=_DEFAULT_SHOE_CARDS,
        expected=planned_duration_s,
        verdict=verdict,
    )


@pytest.mark.parametrize(
    ("jokers_mode", "expected_shoe_cards"),
    [
        (JOKERS_MODE_PER_DECK, 112),  # deck_count x (52 + jokers_per_deck)
        (JOKERS_MODE_TOTAL, 108),  # deck_count x 52 + jokers_per_deck
    ],
    ids=["per_deck", "total"],
)
def test_check_card_sufficiency_given_a_custom_shoe_counts_the_rides_jokers_mode(
    jokers_mode: str, expected_shoe_cards: int
) -> None:
    """shoe_cards follows the ride's own jokers mode, not one rule."""
    config = _config(
        lap_km=_ONE_SECOND_LAP_KM,
        planned_duration_s=100,
        deck_count=2,
        jokers_per_deck=4,
        jokers_mode=jokers_mode,
    )

    result = check_card_sufficiency(config, _single_rider_roster(), _ONE_SECOND_LAP_SPEED_KMH)

    assert (result.shoe_cards, result.expected, result.verdict) == (expected_shoe_cards, 100, OK)


def test_check_card_sufficiency_given_no_estimate_returns_none() -> None:
    """T-3: an impossible estimate yields no card check at all."""
    config = _config(lap_km=0.0)

    result = check_card_sufficiency(config, _single_rider_roster(), 36.0)

    assert result is None


# ============== Phase 3: minimum lap time + duplicate crossings
#
# A lap that takes no time is not a lap: the corrections surface
# refuses to write one, so a mis-keyed add or edit can never
# manufacture a second identical crossing. ``edit_crossing``'s
# replacement instant must be strictly after the entry's preceding lap
# (``actual_start`` for the earliest one) and ``add_crossing_at``'s
# explicit instant strictly after the entry's latest crossing
# (``actual_start`` when the entry has none). ``duplicate_crossings``
# is the read-only projection the console's Needs Review tab reads:
# every live pair sharing one entry and one instant, oldest first, with
# no side effects.
#
# logic-coverage-exempt: T-7 -- duplicate_crossings and the refusals
# are projections/mutations over live engine state, not a pure-function
# module, and AGENTS.md allows no property tests without permission.

_REFUSAL = "lap time must be positive"


def test_edit_crossing_given_a_time_at_the_previous_lap_is_refused() -> None:
    """A zero-second lap is refused, not credited."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.edit_crossing("12", 2, _dt(10, 30), reason="mis-keyed time")


def test_edit_crossing_given_a_time_before_the_previous_lap_is_refused() -> None:
    """A negative lap time is refused, not credited."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.edit_crossing("12", 2, _dt(10, 29), reason="mis-keyed time")


def test_edit_crossing_given_a_time_at_actual_start_on_lap_one_is_refused() -> None:
    """Lap 1 is measured from the gun: the gun itself is refused."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.edit_crossing("12", 1, _dt(10, 0), reason="mis-keyed time")


def test_edit_crossing_given_a_time_before_actual_start_on_lap_one_is_refused() -> None:
    """Lap 1 may not be dated before the gun either."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.edit_crossing("12", 1, _dt(9, 59, 59), reason="mis-keyed time")


def test_edit_crossing_given_a_time_one_second_after_the_previous_lap_is_accepted() -> None:
    """The first instant past the previous lap is a real lap."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))

    engine.edit_crossing("12", 2, _dt(10, 30, 1), reason="mis-keyed time")

    assert engine.lap_times("12") == (1800.0, 1.0)


def test_edit_crossing_given_a_later_time_recomputes_the_lap() -> None:
    """A positive replacement time still re-times the lap (spec §6)."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))

    engine.edit_crossing("12", 2, _dt(10, 45), reason="mis-keyed time")

    assert engine.lap_times("12") == (1800.0, 900.0)


def test_edit_crossing_given_a_refused_time_leaves_the_ride_untouched() -> None:
    """A refusal writes nothing: no audit row, no re-timed crossing."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))
    events_before = len(engine.events)

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.edit_crossing("12", 2, _dt(10, 30), reason="mis-keyed time")

    assert (len(engine.events), engine.crossings[-1].crossed_at) == (
        events_before,
        _dt(10, 40),
    )


def test_add_crossing_at_given_a_time_at_the_latest_crossing_is_refused() -> None:
    """The latest crossing's own instant is not a new lap."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.add_crossing_at("12", _dt(10, 30), reason="missed crossing")


def test_add_crossing_at_given_a_time_before_the_latest_crossing_is_refused() -> None:
    """A back-dated add that precedes the last lap is refused."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.add_crossing_at("12", _dt(10, 25), reason="missed crossing")


def test_add_crossing_at_given_a_time_at_actual_start_with_no_crossings_is_refused() -> None:
    """A first lap is measured from the gun: the gun is refused."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.add_crossing_at("12", _dt(10, 0), reason="missed crossing")


def test_add_crossing_at_given_a_time_before_actual_start_with_no_crossings_is_refused() -> None:
    """A first lap may not be dated before the gun either."""
    engine, _ = _make_engine()
    engine.start()

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.add_crossing_at("12", _dt(9, 59, 59), reason="missed crossing")


def test_add_crossing_at_given_a_time_after_the_latest_crossing_is_accepted() -> None:
    """One second past the entry's last lap is a real lap."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))

    engine.add_crossing_at("12", _dt(10, 30, 1), reason="missed crossing")

    assert engine.lap_times("12") == (1800.0, 1.0)


def test_add_crossing_at_given_a_time_after_actual_start_with_no_crossings_is_accepted() -> None:
    """An entry's first lap may be any instant after the gun."""
    engine, _ = _make_engine()
    engine.start()

    engine.add_crossing_at("12", _dt(10, 0, 1), reason="missed crossing")

    assert engine.lap_times("12") == (1.0,)


def test_add_crossing_at_given_a_refused_time_deals_no_card_and_appends_no_event() -> None:
    """A refusal is atomic: no card leaves the shoe, no row lands."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    before = (engine._shoe.dealt, len(engine.events))

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.add_crossing_at("12", _dt(10, 30), reason="missed crossing")

    assert (engine._shoe.dealt, len(engine.events)) == before


def test_duplicate_crossings_given_no_crossings_returns_an_empty_tuple() -> None:
    """An empty ride has no duplicates to review."""
    engine, _ = _make_engine()
    engine.start()

    assert engine.duplicate_crossings() == ()


def test_duplicate_crossings_given_one_instant_recorded_twice_returns_the_pair() -> None:
    """The double entry the review tab exists to catch."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    first, second = engine.crossings

    assert engine.duplicate_crossings() == ((first, second),)


def test_duplicate_crossings_given_distinct_instants_returns_an_empty_tuple() -> None:
    """Two laps of one entry are not duplicates however close."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30, 1))

    assert engine.duplicate_crossings() == ()


def test_duplicate_crossings_given_two_entries_at_one_instant_returns_an_empty_tuple() -> None:
    """One instant on two entries is two riders, not a double entry."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("34", at=_dt(10, 30))

    assert engine.duplicate_crossings() == ()


def test_duplicate_crossings_given_several_pairs_returns_them_oldest_first() -> None:
    """Pairs order by instant, each pair in record order.

    Recorded out of chronological order (10:40, then 10:35, then
    10:30) so the oldest-first ordering is the projection's, not the
    record log's.
    """
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 40))
    engine.record_crossing("12", at=_dt(10, 40))
    engine.record_crossing("34", at=_dt(10, 35))
    engine.record_crossing("34", at=_dt(10, 35))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    crossings = engine.crossings

    assert engine.duplicate_crossings() == (
        (crossings[4], crossings[5]),  # 12 @ 10:30
        (crossings[2], crossings[3]),  # 34 @ 10:35
        (crossings[0], crossings[1]),  # 12 @ 10:40
    )


def test_duplicate_crossings_given_one_twin_voided_returns_an_empty_tuple() -> None:
    """Voiding one of the pair clears it: only live crossings count."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))

    engine.void_crossing("12", 2, reason="double entry")

    assert engine.duplicate_crossings() == ()


def test_duplicate_crossings_leaves_the_ride_untouched() -> None:
    """The projection is read-only: no event, no crossing moves."""
    engine, _ = _make_engine()
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    before = (engine.crossings, len(engine.events))

    engine.duplicate_crossings()

    assert (engine.crossings, len(engine.events)) == before


# ==================== Phase 3's lap gate is absolute on replay
# The audit log *is* the ride, but the gate is absolute: ``apply``
# re-applies a persisted ``edit_crossing``/``add_crossing_at`` through
# the public command, so a stored row that violates the zero/negative
# lap rule is refused exactly as a live correction would be. The app is
# unreleased and its databases are erased, so there are no pre-gate rows
# to preserve. A legal live command always met the gate when it ran, so
# replaying one still rebuilds the ride.


def test_apply_edit_crossing_given_a_zero_lap_event_raises_value_error() -> None:
    """A persisted zero-lap edit is re-judged, not replayed."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))
    event = Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 2,
            "previous_crossed_at": "2026-09-20T10:40:00",
            "crossed_at": "2026-09-20T10:30:00",
            "reason": "mis-keyed time",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_edit_crossing_given_a_negative_lap_event_raises_value_error() -> None:
    """A persisted edit before the previous lap is refused on replay."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))
    event = Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 2,
            "previous_crossed_at": "2026-09-20T10:40:00",
            "crossed_at": "2026-09-20T10:29:00",
            "reason": "mis-keyed time",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_edit_crossing_given_lap_one_at_actual_start_raises_value_error() -> None:
    """Lap 1 is measured from the gun, replay included (Phase 3)."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    event = Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 1,
            "previous_crossed_at": "2026-09-20T10:30:00",
            "crossed_at": "2026-09-20T10:00:00",
            "reason": "mis-keyed time",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_add_crossing_at_given_a_time_at_the_latest_crossing_raises_value_error() -> None:
    """A persisted add at the latest lap is refused on replay."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    event = Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:30:00",
            "reason": "missed crossing",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_add_crossing_at_given_a_back_dated_event_raises_value_error() -> None:
    """A persisted add before the latest lap is refused on replay."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    event = Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:25:00",
            "reason": "missed crossing",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_add_crossing_at_given_a_first_lap_at_actual_start_raises_value_error() -> None:
    """A persisted first lap at the gun is refused on replay."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    event = Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:00:00",
            "reason": "missed crossing",
        },
    )

    with pytest.raises(ValueError, match=re.escape(_REFUSAL)):
        engine.apply(event)


def test_apply_edit_crossing_given_a_legal_event_still_replays_it() -> None:
    """A legal persisted edit met the gate and still rebuilds."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))
    event = Event(
        action="edit_crossing",
        payload={
            "entry_id": "12",
            "seq": 2,
            "previous_crossed_at": "2026-09-20T10:40:00",
            "crossed_at": "2026-09-20T10:45:00",
            "reason": "mis-keyed time",
        },
    )

    engine.apply(event)

    assert (engine.lap_times("12"), engine.events[-1]) == ((1800.0, 900.0), event)


def test_apply_add_crossing_at_given_a_legal_event_still_replays_it() -> None:
    """A legal persisted add met the gate and still rebuilds."""
    engine, _ = _make_engine()
    engine.start(at=_dt(10, 0))
    engine.record_crossing("12", at=_dt(10, 30))
    event = Event(
        action="add_crossing_at",
        payload={
            "plate": "12",
            "entry_id": "12",
            "crossed_at": "2026-09-20T10:35:00",
            "reason": "missed crossing",
        },
    )

    engine.apply(event)

    assert (engine.lap_times("12"), engine.events[-1]) == ((1800.0, 300.0), event)
