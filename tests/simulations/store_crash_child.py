# SPDX-License-Identifier: GPL-3.0-only
"""Crash-injection child for the E5.1.3 crash-consistency suite (R-50).

This module *is* the child process's entire program for
``tests/simulations/test_store_crash_consistency.py``. Run as::

    python store_crash_child.py <db_path> <max_crossings> <seed>
                                [--die-mid-append]

it opens a real :class:`~rivercrossing.store.Store` at ``db_path``,
creates one ride, and appends one ``start`` event plus
``max_crossings`` ``record_crossing`` events -- each through the real
:class:`~rivercrossing.ride.RideEngine`, one
:meth:`~rivercrossing.store.Store.append` per event, each its own
committed transaction -- printing the parent's line protocol to
stdout as it goes (see the test module docstring):

- ``EVENT <json>``  -- the event about to be appended;
- ``CHECKPOINT <n>`` -- append ``n`` committed;
- ``IN_TX`` -- mid-append mode only: an append's transaction is open,
  between BEGIN and COMMIT, and the process is blocking there so the
  parent can kill it at a deterministic point;
- ``DONE <n>`` -- all ``n`` appends committed and the child exited.

The child never fsyncs by hand: ``synchronous=NORMAL`` (spec §2,
applied by ``Store.open``) is exactly the durability mode under test,
and adding an fsync would change the crash semantics the suite is
proving. A hard process kill (SIGKILL / ``TerminateProcess``) leaves
committed transactions in the kernel's page cache; WAL recovery on
reopen decides what survives -- which is the behavior under test.

Determinism: the payloads come from a fixed config and a fake clock
(``_START`` + 60 s per lap), and the shoe is seeded from ``<seed>``,
so every event is byte-for-byte reproducible while the *kill point*
varies (the parent chooses it). The events do not depend on the seed
-- a ``record_crossing`` payload carries plate/entry/lap/time, never
the dealt card -- but the suite passes a seed anyway so each child's
shoe is an independent, seeded deck (spec §4). The shoe is 1 deck, 0
jokers (52 cards) and ``max_crossings`` is capped at 50 by the test
suite, so the shoe never exhausts and no ``shoe_reshuffle`` event
ever interleaves the stream.

``--die-mid-append`` (the deterministic negative case) writes the
third event as an *uncommitted* audit row through a sibling raw
sqlite3 connection and then blocks, printing ``IN_TX`` so the parent
can kill at a deterministic point. A literal between-BEGIN-and-COMMIT
hook on :meth:`~rivercrossing.store.Store.append` is not injectable:
``sqlite3.Connection.commit`` is a read-only C-slot attribute, and a
test-only connection factory on the Store would be a speculative
parameter no production caller needs (CODINGSTANDARDS-SIMPLECODE.md
rule 1) -- the task brief's own fallback ("if that is not reliably
reproducible, document why and assert the weaker but real invariant").
The sibling row is byte-identical to the one ``Store.append`` would
write, so the on-disk state is exactly what a kill between an
append's INSERT and COMMIT leaves: an uncommitted frame in the WAL
that recovery must roll back while keeping the committed prefix.

Like the functional suite's scenario children, this child arms a
daemon ``threading.Timer`` that hard-exits (``os._exit(124)``) after
``_CHILD_BOUND_SECONDS``, so a hung child -- a deadlock in Store.open,
say -- terminates on its own and the parent's read loop ends with a
named failure instead of stalling the pass.
"""

import faulthandler
import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store

__all__ = ["main"]

# Must match the test module's _PLATE: load_engine takes the roster
# from the caller, so the parent replays with the same single plate.
_PLATE = "12"
# Naive local by design -- RideConfig.planned_start's own convention.
_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001
_GAP_S = 60.0
# Hard bound, mirroring scenario_runner.SCENARIO_CHILD_BOUND_SECONDS:
# a hung child must die before the parent would ever stall on it.
_CHILD_BOUND_SECONDS = 20
_EXPECTED_ARGC = 4  # script, db path, max crossings, seed
# Third event overall (start + crossing 1 committed first).
_MID_APPEND_BLOCK_AT = 2


@dataclass(frozen=True, slots=True)
class _Program:
    """One crash-child run's fixed parameters (module docstring)."""

    db_path: str
    max_crossings: int
    seed: int
    die_mid_append: bool


class _FakeClock:
    """An advanceable wall clock for the child's RideEngine."""

    def __init__(self, start: datetime) -> None:
        """Freeze the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


def _build_roster() -> Roster:
    """Build the crash ride's field: one solo entry on ``_PLATE``."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate=_PLATE)
    return roster


def _build_config() -> RideConfig:
    """Build the crash ride's fixed config (mirrored in the test)."""
    return RideConfig(
        name="Crash Consistency",
        event_date=date(2026, 9, 20),
        venue="Crash Venue",
        lap_km=8.0,
        organizer="TDD",
        scorer="TDD",
        planned_start=_START,
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        deck_count=1,
        jokers_per_deck=0,
    )


def _emit(event: Event) -> None:
    """Print the *event* as one ``EVENT`` protocol line, flushed."""
    print(  # noqa: T201 -- the child's entire contract with its parent
        "EVENT "
        + json.dumps({"action": event.action, "payload": dict(event.payload)}, sort_keys=True),
        flush=True,
    )


def _checkpoint(n: int) -> None:
    """Print the committed-floor ``CHECKPOINT n`` line, flushed."""
    print(f"CHECKPOINT {n}", flush=True)  # noqa: T201 -- protocol line


def _open_mid_append_transaction(db_path: str, ride_id: int, event: Event) -> None:
    """Write *event* uncommitted, then block; never returns.

    The deterministic mid-append crash (module docstring): a sibling
    raw connection performs exactly the INSERT ``Store.append`` would
    perform, but never commits -- so the WAL holds the row as an
    uncommitted frame, the state a kill between an append's INSERT and
    COMMIT leaves. Prints ``IN_TX`` once the row is written (not
    committed) so the parent knows the crash point is armed.
    """
    crossed = datetime.fromisoformat(str(event.payload["crossed_at"]))
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT INTO audit (ride_id, at, action, payload_json) VALUES (?, ?, ?, ?)",
        (
            ride_id,
            int(crossed.timestamp()),
            event.action,
            json.dumps(dict(event.payload)),
        ),
    )
    print("IN_TX", flush=True)  # noqa: T201 -- protocol line
    while True:
        time.sleep(1)


def _run(program: _Program) -> None:
    """Open *program.db_path*; create a ride; append the event stream.

    Every append is one committed transaction. The loop emits each
    event, then (in mid-append mode) blocks inside append number
    ``_MID_APPEND_BLOCK_AT``'s transaction, then commits and
    checkpoints; on reaching the end it prints ``DONE``.
    """
    config = _build_config()
    clock = _FakeClock(_START)
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=program.seed)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=_build_roster())
    store = Store.open(program.db_path)
    try:
        ride_id = store.create_ride(config)
        start_event = engine.start()
        _emit(start_event)
        store.append(ride_id, start_event)
        _checkpoint(1)
        for i in range(1, program.max_crossings + 1):
            clock.advance(_GAP_S)
            result = engine.record_crossing(_PLATE)
            if not result.accepted:
                # logic-coverage-exempt: T-3 -- defensive harness guard.
                # The True branch (a refused crossing) is unreachable
                # while the engine and roster are correct; it exists to
                # fail loudly if a future edit breaks the plate/config
                # coupling, per SIMPLECODE rule 15.
                raise RuntimeError(f"crossing {i} refused: {result.reason}")
            event = engine.events[-1]
            _emit(event)
            if program.die_mid_append and i == _MID_APPEND_BLOCK_AT:
                _open_mid_append_transaction(program.db_path, ride_id, event)
            store.append(ride_id, event)
            _checkpoint(i + 1)
        print(f"DONE {program.max_crossings + 1}", flush=True)  # noqa: T201 -- protocol line
    finally:
        store.close()


def main(argv: list[str]) -> int:
    """Run the crash child; exit 0 only after ``DONE`` was printed.

    ``faulthandler.enable()`` runs first, before anything else, so a
    native-level crash later in this process still writes a Python
    traceback to stderr instead of leaving the parent with a bare
    non-zero exit code to diagnose. The hard bound is a daemon timer:
    it fires only when the process is genuinely still alive -- i.e.
    hung -- and ``os._exit`` does not need the GIL, so a deadlocked
    child still dies (124 mirrors the functional rerun wrapper's own
    timed-out exit code).
    """
    faulthandler.enable()
    bound = threading.Timer(_CHILD_BOUND_SECONDS, os._exit, args=(124,))
    bound.daemon = True
    bound.start()
    if len(argv) < _EXPECTED_ARGC:
        print(  # noqa: T201 -- the child's entire contract with its parent
            "usage: store_crash_child.py <db_path> <max_crossings> <seed> [--die-mid-append]",
            flush=True,
        )
        return 2
    db_path = argv[1]
    max_crossings = int(argv[2])
    seed = int(argv[3])
    die_mid_append = "--die-mid-append" in argv[4:]
    if die_mid_append and max_crossings < _MID_APPEND_BLOCK_AT:
        raise ValueError("--die-mid-append needs at least 2 crossings")
    _run(
        _Program(
            db_path=db_path, max_crossings=max_crossings, seed=seed, die_mid_append=die_mid_append
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
