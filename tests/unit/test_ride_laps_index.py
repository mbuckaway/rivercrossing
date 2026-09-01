# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for RideEngine's per-entry lap index (review fix).

Code-review finding: ``_laps_for`` filtered the whole ride-wide
``_crossings`` list and sorted it on every call -- O(C) per lookup --
so ``Store.load_engine`` replay (one lookup per crossing) was O(C^2)
and ``snapshot()`` paid it twice per entry. The fix keeps a parallel
``_laps`` dict (entry_id -> live crossings sorted by ``crossed_at``,
holding the SAME :class:`~rivercrossing.ride.Crossing` objects
``_crossings`` holds), maintained by the same private mutators
(``_insert_crossing``/``_remove_crossing``/``_replace_crossing``) so
the two structures cannot drift, and makes ``_laps_for`` a plain dict
read.

These tests pin the index's contract: (1) a characterization that a
battery of live crossings PLUS every correction (add-at-time,
edit re-time, undo, reassign, void) leaves ``_laps_for`` sorted by
``crossed_at`` and ``snapshot()``/``on_course`` unchanged; (2) the
structural/perf regression -- ``_laps_for`` must not iterate or index
``_crossings``, which fails against the old filter-and-sort
implementation; (3) the object-identity and same-instant tie-break
invariants the mutators must preserve; and (4) replay rebuilds the
same index.
"""

from datetime import date, datetime, timedelta

import pytest

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster

# The same minimal, always-valid kwarg set the other ride suites use
# (this repo keeps every test module self-contained).
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive by design, RideConfig's own convention
    "planned_duration_s": 21600,
    "min_lap_s": 1,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


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
    config: RideConfig | None = None,
) -> tuple[RideEngine, _FakeClock]:
    """Build a DRAFT engine over a valid config, shoe and roster."""
    config = config if config is not None else _config()
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    roster = roster if roster is not None else _roster_with_entries("12", "34")
    clock = _FakeClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    return engine, clock


def _engine_with_corrected_ride() -> RideEngine:
    """Build a ride exercising every mutator that touches the index.

    Live crossings plus add-at-time (out-of-order past), edit re-time,
    undo of an out-of-order crossing, reassign and void -- the mixed
    history the replay/correction suites drive at random, fixed here
    so the expected index order is readable.
    """
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 40))
    engine.add_crossing_at("12", _dt(10, 25), reason="missed crossing")
    engine.edit_crossing("12", 3, _dt(10, 22), reason="mis-keyed time")
    engine.undo_last()
    engine.record_crossing("12", at=_dt(10, 50))
    engine.record_crossing("34", at=_dt(10, 35))
    engine.reassign_crossing(3, "34", reason="mis-keyed plate")
    engine.void_crossing("34", 1, reason="double entry")
    return engine


class _ExplodingCrossings(list):
    """A ``_crossings`` stand-in that fails loudly if scanned.

    ``_laps_for`` must read the per-entry index, never the ride-wide
    list; any iteration or indexing here is the quadratic regression
    the review found.
    """

    def __iter__(self) -> object:
        raise AssertionError("_laps_for iterated _crossings")

    def __getitem__(self, index: int) -> object:
        raise AssertionError("_laps_for indexed _crossings")


# =================================================== characterization


def test_laps_for_mixed_corrections_returns_sorted_tuple_and_snapshot_matches() -> None:
    """The index agrees with the derived timing after every mutator."""
    engine = _engine_with_corrected_ride()

    laps_12 = engine._laps_for("12")
    laps_34 = engine._laps_for("34")

    assert [(c.seq, c.crossed_at) for c in laps_12] == [(1, _dt(10, 30)), (2, _dt(10, 40))]
    assert [(c.seq, c.crossed_at) for c in laps_34] == [(1, _dt(10, 50))]
    assert engine._laps_for("999") == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert (results["12"].laps, results["12"].total_time, results["12"].best_lap) == (
        2,
        2400.0,
        600.0,
    )
    assert (results["34"].laps, results["34"].total_time, results["34"].best_lap) == (
        1,
        3000.0,
        3000.0,
    )
    assert engine.on_course == 1


def test_laps_for_entry_without_crossings_returns_empty_tuple() -> None:
    """An entry with no laps reports an empty index entry."""
    engine, _ = _make_engine()

    assert engine._laps_for("34") == ()


def test_laps_for_undo_of_out_of_order_crossing_keeps_chronological_order() -> None:
    """undo_last pops the last record; the index stays time-sorted."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.add_crossing_at("12", _dt(10, 25), reason="missed crossing")
    engine.undo_last()

    laps = engine._laps_for("12")

    assert laps == (engine.crossings[0],)
    assert engine.lap_times("12") == (1800.0,)


def test_laps_index_tie_at_same_instant_keeps_record_order() -> None:
    """Two crossings at the same instant stay in record order."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    first, second = engine.crossings

    laps = engine._laps_for("12")

    assert laps == (first, second)


def test_laps_index_renumber_keeps_tied_record_order() -> None:
    """Renumbering laps that share an instant keeps record order."""
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))

    engine.void_crossing("12", 1, reason="double entry")

    laps = engine._laps_for("12")
    assert [(c.seq, c.crossed_at) for c in laps] == [
        (1, _dt(10, 30)),
        (2, _dt(10, 30)),
        (3, _dt(10, 32)),
    ]
    assert all(a is b for a, b in zip(laps, engine.crossings, strict=True))


def test_laps_index_edit_to_tie_with_later_crossing_keeps_record_order() -> None:
    """Re-time onto a later lap keeps record-order tie-break.

    The index replacement re-sorts stably when the time changes, so the
    re-timed lap keeps its record slot ahead of the later-record lap
    that now shares the instant; a naive remove-and-reinsert would
    drop it behind.
    """
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 31))
    engine.record_crossing("12", at=_dt(10, 35))

    engine.edit_crossing("12", 2, _dt(10, 35), reason="mis-keyed time")

    laps = engine._laps_for("12")
    assert [(c.seq, c.crossed_at) for c in laps] == [
        (1, _dt(10, 30)),
        (2, _dt(10, 35)),
        (3, _dt(10, 35)),
    ]
    assert all(a is b for a, b in zip(laps, engine.crossings, strict=True))


# ============================================== object identity


@pytest.mark.parametrize("entry_id", ["12", "34"])
def test_laps_index_holds_the_same_objects_as_crossings(entry_id: str) -> None:
    """The index aliases _crossings' objects, never copies them."""
    engine = _engine_with_corrected_ride()
    expected = [c for c in engine.crossings if c.entry_id == entry_id]

    laps = engine._laps_for(entry_id)

    assert laps == tuple(expected)
    assert all(a is b for a, b in zip(laps, expected, strict=True))


# ============================================ no-scan regression


def test_laps_for_returns_correct_laps_without_iterating_crossings() -> None:
    """_laps_for is O(1): it must not touch the ride-wide list.

    This is the structural/perf pin for the review fix: the old
    implementation filtered ``self._crossings`` on every call, so
    swapping in a list that raises on iteration/indexing must fail
    against it. The index-backed implementation reads ``_laps`` only.
    """
    engine, _ = _make_engine(config=_config(min_lap_s=1))
    engine.start()
    engine.record_crossing("12", at=_dt(10, 30))
    engine.record_crossing("12", at=_dt(10, 32))
    engine.record_crossing("34", at=_dt(10, 34))
    expected = (engine._crossings[0], engine._crossings[1])
    engine._crossings = _ExplodingCrossings(engine._crossings)

    laps = engine._laps_for("12")

    assert laps == expected


# ================================================= replay rebuild


def test_laps_index_replay_of_corrected_ride_matches_live_index() -> None:
    """Replaying the audit log rebuilds the same per-entry index."""
    live = _engine_with_corrected_ride()

    replayed, _ = _make_engine(config=_config(min_lap_s=1))
    for event in live.events:
        replayed.apply(event)

    assert replayed._laps_for("12") == live._laps_for("12")
    assert replayed._laps_for("34") == live._laps_for("34")
    assert replayed.snapshot() == live.snapshot()
    assert replayed.on_course == live.on_course
