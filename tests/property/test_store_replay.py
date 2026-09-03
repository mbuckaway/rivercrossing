# SPDX-License-Identifier: GPL-3.0-only
"""Store event-replay equivalence property (E5.1.2).

The store is an event log: every :class:`~rivercrossing.ride.
RideEngine` mutation appends one :class:`~rivercrossing.ride.Event`
to :attr:`RideEngine.events`, :meth:`~rivercrossing.store.Store.append`
persists each as one ``audit`` row, and :meth:`~rivercrossing.store.
Store.load_engine` rebuilds a fresh engine by replaying those events
in append order. This suite asserts task-briefs E5.1.2's done-when:
for many random-but-seeded event sequences, live engine state equals
replayed engine state, reusing the E4 sim generator's field and fake
clock (tests/simulations/test_ride_replay.py) plus a Hypothesis-driven
random walk.

The comparison contract (stated here because the property is only as
good as what it compares):

- ``snapshot()`` -- per-entry laps, derived times, credited cards and
  best hand, equal list for list.
- ``events`` -- same count and same action sequence. Payload bytes are
  NOT compared: ``stop``/``finish``/``reopen`` re-stamp their payload
  timestamps from the replay engine's own clock, so those three audit
  fields differ by design (see :meth:`RideEngine.apply`'s docstring).
- shoe state -- ``dealt``/``cycle``/``remaining`` equal, plus the
  shoe's open/closed state (``Shoe.is_closed``). The fresh shoe built
  from the stored ``rng_seed`` reproduces every deal, and the deal
  loop (not the ``shoe_reshuffle`` event) drives the reshuffle, so
  the replayed shoe lands at the same point; ``finish`` closes it and
  a replayed ``reopen`` opens it again, so the open/closed state
  matches too (E7.1.1: REOPENED corrections deal new cards).
- ``held_crossings()`` and ``state`` equal.
- ``elapsed()`` equal at the shared fake clock's final instant.

The store owns the shoe seed (``secrets.randbits``, spec section 4),
so the live engine must read it back from the ride row; the deal
sequence is random per example, which is fine -- the invariant holds
for every seed. The deterministic fixture (no Hypothesis) drives one
fixed scripted sequence covering every dispatch action and asserts the
same equivalence point-for-point.
"""

import random
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.cards import Card, Shoe
from rivercrossing.ride import CrossingResult, RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable

# Fixed literal seeds and a 52-card shoe throughout: the task's own
# "one shoe exhaustion" case must be reachable in one example, so the
# ride runs 1 deck x 0 jokers (spec section 4's default 8 x 2 would
# need 433 deals per example).
_REPLAY_SEED_BOUND = 1_000_000
_DECK_COUNT = 1
_JOKERS_PER_DECK = 0
_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive, like RideConfig's planned_start

_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "planned_start": _START,
    "planned_duration_s": 21600,
    "min_lap_s": 60,  # gaps below 60 s trigger the short-lap hold (R-34)
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
    "deck_count": _DECK_COUNT,
    "jokers_per_deck": _JOKERS_PER_DECK,
}


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


@dataclass
class _RideContext:
    """The live engine, its clock, roster, and the event persister.

    Bundles the state every driver helper touches so each helper stays
    within the project's three-parameter guidance
    (CODINGSTANDARDS-SIMPLECODE.md, max-args=3).
    """

    engine: RideEngine
    clock: _FakeClock
    roster: Roster
    store: Store
    ride_id: int

    def persist_new(self, start: int) -> None:
        """Append every event in ``engine.events[start:]`` to the store.

        One engine call may append several events (a ``ShoeEmpty``
        deal appends ``shoe_reshuffle`` before the mutation's own
        event), so a driver captures the count before the call and
        syncs everything after it.
        """
        for event in self.engine.events[start:]:
            self.store.append(self.ride_id, event)


def _config() -> RideConfig:
    """Build the replay ride's fixed config over a 52-card shoe."""
    return RideConfig(**_VALID_KWARGS)  # type: ignore[arg-type]


def _make_roster() -> Roster:
    """Build the replay ride's field: one solo, one pooled team."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[
            Rider(first_name="Sarah", last_name="", plate="45"),
            Rider(first_name="Priya", last_name="", plate="9"),
        ],
    )
    return roster


def _stored_seed(db_path: Path, ride_id: int) -> int:
    """Read the store-owned ``rng_seed`` back out of the file.

    The live engine must deal from the same seed the store persisted,
    or replay cannot reproduce the deals (spec section 4).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT rng_seed FROM ride WHERE id = ?", (ride_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(f"no ride row with id {ride_id}")
    return int(row[0])


def _make_ctx(db_path: Path) -> _RideContext:
    """Create one ride and its live engine over the store's own seed."""
    config = _config()
    roster = _make_roster()
    store = Store.open(db_path)
    ride_id = store.create_ride(config)
    clock = _FakeClock(_START)
    live = RideEngine(
        config=config,
        shoe=Shoe(
            decks=config.deck_count,
            jokers_per_deck=config.jokers_per_deck,
            seed=_stored_seed(db_path, ride_id),
        ),
        clock=clock,
        roster=roster,
    )
    return _RideContext(
        engine=live,
        clock=clock,
        roster=roster,
        store=store,
        ride_id=ride_id,
    )


def _replay(ctx: _RideContext) -> RideEngine:
    """Rebuild the engine from the persisted audit log, same clock."""
    return ctx.store.load_engine(ctx.ride_id, ctx.roster, clock=ctx.clock)


def _mutate(ctx: _RideContext, call: Callable[[], object]) -> None:
    """Run one engine mutation and persist the events it appended."""
    start = len(ctx.engine.events)
    call()
    ctx.persist_new(start)


def _crossing(ctx: _RideContext, plate: str, gap_s: float) -> CrossingResult:
    """Advance the clock, record one crossing, persist its events."""
    start = len(ctx.engine.events)
    ctx.clock.advance(gap_s)
    result = ctx.engine.record_crossing(plate)
    ctx.persist_new(start)
    return result


# The branch count is the event-action coverage space of the weighted
# walk, not a control-flow tangle: every branch drives a distinct
# replay action the equivalence must cover.
def _drive_random_ride(ctx: _RideContext, rng: random.Random) -> None:  # noqa: C901
    """Drive a random-but-seeded ride covering every replay action.

    Phase 1 forces the short-lap hold lifecycle (two held crossings,
    one confirmed, one voided); phase 2 is a weighted random walk of
    crossings, holds, undos, manual deals, stop/continue and a
    back-dated start; phase 3 crosses until cycle 1 exhausts into
    cycle 2; phase 4 finishes, reopens and finishes again.
    """
    plates = ("12", "45", "9")
    gaps = (20, 30, 45, 90, 600, 1200)
    reasons = ("replacement card", "missed crossing", "manual add")

    _mutate(ctx, ctx.engine.start)

    # Phase 1: force the hold lifecycle on the pooled team.
    _crossing(ctx, "45", 600)
    _crossing(ctx, "45", 30)
    _mutate(ctx, lambda: ctx.engine.confirm_held(ctx.engine.held_crossings()[0].crossing))
    _crossing(ctx, "9", 30)
    _mutate(ctx, lambda: ctx.engine.void_held(ctx.engine.held_crossings()[-1].crossing))

    # Phase 2: weighted random walk while RUNNING.
    stopped = False
    for _ in range(45):
        roll = rng.random()
        if roll < 0.45:
            _crossing(ctx, rng.choice(plates), rng.choice(gaps))
        elif roll < 0.52 and ctx.engine.held_crossings():
            _mutate(ctx, lambda: ctx.engine.confirm_held(ctx.engine.held_crossings()[0].crossing))
        elif roll < 0.59 and ctx.engine.held_crossings():
            _mutate(ctx, lambda: ctx.engine.void_held(ctx.engine.held_crossings()[-1].crossing))
        elif roll < 0.67 and ctx.engine.crossings:
            _mutate(ctx, ctx.engine.undo_last)
        elif roll < 0.75:
            _mutate(
                ctx,
                lambda: ctx.engine.deal_manual(rng.choice(plates), reason=rng.choice(reasons)),
            )
        elif roll < 0.80 and not stopped:
            _mutate(ctx, ctx.engine.stop)
            stopped = True
        elif roll < 0.85 and stopped:
            _mutate(ctx, ctx.engine.start)
            stopped = False
        elif roll < 0.89 and ctx.engine.state is RideStatus.RUNNING:
            _mutate(ctx, lambda: ctx.engine.set_start_time(ctx.clock() - timedelta(minutes=5)))
        else:
            _crossing(ctx, rng.choice(plates), rng.choice(gaps))

    # Phase 3: cross until cycle 1 exhausts into cycle 2 (spec §4).
    if stopped:
        _mutate(ctx, ctx.engine.start)
    while ctx.engine._shoe.cycle == 1:
        _crossing(ctx, "12", 600)

    # Phase 4: finish, reopen (dealing a correction card), finish again.
    _mutate(ctx, ctx.engine.finish)
    _mutate(ctx, ctx.engine.reopen)
    _mutate(ctx, lambda: ctx.engine.deal_manual("12", reason="replacement card"))
    _mutate(ctx, ctx.engine.finish)


def _assert_equivalent(live: RideEngine, replayed: RideEngine) -> None:
    """Assert the replay comparison contract (module docstring)."""
    assert replayed.state is live.state
    assert len(replayed.events) == len(live.events)
    assert [event.action for event in replayed.events] == [event.action for event in live.events]
    assert replayed.shoe_remaining == live.shoe_remaining
    assert replayed.shoe_total == live.shoe_total
    assert replayed._shoe.cycle == live._shoe.cycle
    assert replayed._shoe.dealt == live._shoe.dealt
    assert replayed._shoe.is_closed is live._shoe.is_closed
    assert replayed.held_crossings() == live.held_crossings()
    assert replayed.elapsed() == live.elapsed()
    assert replayed.snapshot() == live.snapshot()


def _drive_and_load(db_path: Path, seed: int) -> tuple[RideEngine, RideEngine]:
    """Drive a seeded random ride, persist each event, and replay it."""
    ctx = _make_ctx(db_path)
    try:
        _drive_random_ride(ctx, random.Random(seed))  # noqa: S311 -- seeded test fixture, not a security use
        replayed = _replay(ctx)
        return ctx.engine, replayed
    finally:
        ctx.store.close()


def _drive_random_corrections(ctx: _RideContext, rng: random.Random) -> None:
    """Drive a seeded ride of random audited corrections (E7.1.2).

    Seeds eight plain crossings, then fires 30 random corrections --
    edit, void, reassign, add-at-time, DNF, void-card -- each legal at
    the moment it runs (RUNNING, a live crossing/plate/card) and each
    appending exactly one event the store persists. Replay must rebuild
    the directly-corrected state: every correction resolves the same
    live identity from its payload (``(entry_id, seq)`` for edit/void,
    the ride-wide crossing ordinal for reassign, the card code for
    void-card), which is exactly what ``apply`` reproduces in order.
    """
    plates = ("12", "45", "9")
    _mutate(ctx, ctx.engine.start)
    for _ in range(8):
        _crossing(ctx, rng.choice(plates), 600)

    for _ in range(30):
        roll = rng.random()
        live = ctx.engine
        crossings = live.crossings
        if roll < 0.22 and crossings:
            crossing = crossings[rng.randrange(len(crossings))]
            _mutate(
                ctx,
                lambda c=crossing, engine=live: engine.edit_crossing(
                    c.entry_id, c.seq, ctx.clock(), reason="mis-keyed time"
                ),
            )
        elif roll < 0.38 and crossings:
            crossing = crossings[rng.randrange(len(crossings))]
            _mutate(
                ctx,
                lambda c=crossing, engine=live: engine.void_crossing(
                    c.entry_id, c.seq, reason="double entry"
                ),
            )
        elif roll < 0.54 and crossings:
            ordinal = rng.randrange(1, len(crossings) + 1)
            _mutate(
                ctx,
                lambda engine=live, n=ordinal: engine.reassign_crossing(
                    n, rng.choice(plates), reason="mis-keyed plate"
                ),
            )
        elif roll < 0.70:
            _mutate(
                ctx,
                lambda engine=live: engine.add_crossing_at(
                    rng.choice(plates), ctx.clock(), reason="missed crossing"
                ),
            )
        elif roll < 0.86:
            results = {result.plate: result for result in live.snapshot()}
            plates_with_cards = [plate for plate, result in results.items() if result.cards]
            if plates_with_cards:
                plate = rng.choice(plates_with_cards)
                card = results[plate].cards[0]
                _mutate(
                    ctx,
                    lambda engine=live, p=plate, c=card: engine.void_card(
                        p, c, reason="wrong card dealt"
                    ),
                )
        else:
            plate = rng.choice(plates)
            _mutate(
                ctx,
                lambda engine=live, p=plate: engine.mark_dnf(p, reason="mechanical failure"),
            )

    _mutate(ctx, ctx.engine.finish)


def _drive_corrections_and_load(db_path: Path, seed: int) -> tuple[RideEngine, RideEngine]:
    """Drive a seeded corrections ride, persist, and replay it."""
    ctx = _make_ctx(db_path)
    try:
        _drive_random_corrections(ctx, random.Random(seed))  # noqa: S311 -- seeded fixture, not a security use
        replayed = _replay(ctx)
        return ctx.engine, replayed
    finally:
        ctx.store.close()


@given(seed=st.integers(min_value=1, max_value=_REPLAY_SEED_BOUND))
@settings(max_examples=50, deadline=None)
def test_store_replay_equivalence_random_seeded_sequences(seed: int) -> None:
    """Random-but-seeded event sequences: live state equals replayed."""
    with tempfile.TemporaryDirectory() as tmp:
        live, replayed = _drive_and_load(Path(tmp) / "replay.db", seed)

    _assert_equivalent(live, replayed)


@given(seed=st.integers(min_value=1, max_value=_REPLAY_SEED_BOUND))
@settings(max_examples=50, deadline=None)
def test_store_replay_equivalence_corrected_history_random_seeded(seed: int) -> None:
    """Corrected history replayed equals the directly corrected ride.

    E7.1.2's own done-when: a ride driven with random audited
    corrections -- edit/void/reassign/add-at-time/DNF/void-card --
    persisted event by event and replayed from the audit log lands in
    the exact state the live engine reached by applying those same
    corrections directly.
    """
    with tempfile.TemporaryDirectory() as tmp:
        live, replayed = _drive_corrections_and_load(Path(tmp) / "replay_corrections.db", seed)

    _assert_equivalent(live, replayed)


def test_store_replay_equivalence_deterministic_fixture(tmp_path: Path) -> None:
    """A fixed scripted sequence replays point-for-point, all actions.

    Every dispatch action appears at least once (asserted at the end),
    so the fixture doubles as the per-branch attestation for the
    ``apply`` dispatch's full vocabulary.
    """
    ctx = _make_ctx(tmp_path / "replay_fixture.db")
    try:
        _mutate(ctx, ctx.engine.start)
        _crossing(ctx, "45", 600)
        _crossing(ctx, "45", 30)
        _mutate(ctx, lambda: ctx.engine.confirm_held(ctx.engine.held_crossings()[0].crossing))
        _crossing(ctx, "9", 30)
        _mutate(ctx, lambda: ctx.engine.void_held(ctx.engine.held_crossings()[-1].crossing))
        _mutate(ctx, ctx.engine.undo_last)
        for _ in range(4):
            _crossing(ctx, "12", 600)
        _mutate(ctx, lambda: ctx.engine.set_start_time(datetime(2026, 9, 20, 9, 55)))  # noqa: DTZ001
        _start = len(ctx.engine.events)
        manual = ctx.engine.deal_manual("12", reason="replacement card")
        ctx.persist_new(_start)
        # E7.1.2 corrections while RUNNING -- each appends one audit
        # event, and every one of them must replay identically. "45"
        # and "9" are riders on one team whose entry plate is "9"
        # (R-16), so every correction names the entry plate the
        # crossing/card actually carries.
        _mutate(
            ctx,
            lambda: ctx.engine.edit_crossing(
                "9",
                1,
                datetime(2026, 9, 20, 10, 1),  # noqa: DTZ001
                reason="mis-keyed time",
            ),
        )
        _mutate(
            ctx,
            lambda: ctx.engine.add_crossing_at(
                "12",
                datetime(2026, 9, 20, 10, 2),  # noqa: DTZ001
                reason="missed crossing",
            ),
        )
        _mutate(ctx, lambda: ctx.engine.reassign_crossing(2, "12", reason="mis-keyed plate"))
        _mutate(ctx, lambda: ctx.engine.void_crossing("12", 2, reason="double entry"))
        _mutate(ctx, lambda: ctx.engine.mark_dnf("45", reason="mechanical failure"))
        _mutate(
            ctx,
            lambda: ctx.engine.void_card(
                "12", Card.parse(str(manual.payload["card"])), reason="wrong card dealt"
            ),
        )
        _mutate(ctx, ctx.engine.stop)
        _mutate(ctx, ctx.engine.start)
        while ctx.engine._shoe.cycle == 1:
            _crossing(ctx, "12", 600)
        _mutate(ctx, ctx.engine.undo_last)
        _crossing(ctx, "12", 600)
        _mutate(ctx, ctx.engine.finish)
        _mutate(ctx, ctx.engine.reopen)
        _mutate(ctx, lambda: ctx.engine.deal_manual("12", reason="replacement card"))
        _mutate(
            ctx,
            lambda: ctx.engine.add_crossing_at("12", ctx.clock(), reason="missed crossing"),
        )
        _mutate(ctx, ctx.engine.finish)

        replayed = _replay(ctx)
    finally:
        ctx.store.close()

    _assert_equivalent(ctx.engine, replayed)
    actions = {event.action for event in ctx.engine.events}
    assert actions == {
        "start",
        "record_crossing",
        "confirm_held",
        "void_held",
        "undo",
        "deal_manual",
        "set_start_time",
        "stop",
        "continue",
        "shoe_reshuffle",
        "finish",
        "reopen",
        # E7.1.1 audited corrections (each replays through apply).
        "edit_crossing",
        "void_crossing",
        "add_crossing_at",
        "reassign",
        "dnf",
        "void_card",
    }
