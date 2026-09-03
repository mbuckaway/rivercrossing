# SPDX-License-Identifier: GPL-3.0-only
"""Shared store-staging helpers for the subprocess-scenario suites.

The E5.2.2/E5.4.1/E9.2.1 scenarios all need a real Store file shaped
a particular way before a child process launches the app on it: a ride
row (with a saved roster), an event log that replays into the wanted
engine state, and an ``app_session`` row that reads as the wanted
previous-session state. These helpers were private to
``console_subprocess_scenarios.py``; the E9.2.1 full-race acceptance
test (``tests/acceptance/test_full_race.py``) is the second consumer,
so they move here instead of a fourth inline copy (CODINGSTANDARDS-
SIMPLECODE.md rule of three; the parent race test must never open a
``Store`` itself -- ``Store.open`` inserts an ``app_session`` row and
would corrupt the very session sequence the race asserts on, which is
why :func:`race_db_facts` reads the tables directly).

This module is wx-free: everything here is plain Store/sqlite logic,
unit-testable headless (``tests/unit/test_race_child.py``).
"""

import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import Store

__all__ = [
    "ResumeRideSpec",
    "append_ride_events",
    "create_library_ride",
    "create_resumed_ride",
    "library_ride_config",
    "library_roster",
    "race_db_facts",
    "resume_db_path",
    "resume_ride_config",
    "rich_race_roster",
    "running_ride_with_roster",
]


def resume_db_path(prefix: str) -> Path:
    """Return a fresh db file path under a temp dir named *prefix*."""
    return Path(tempfile.mkdtemp(prefix=prefix)) / "rides.db"


def resume_ride_config() -> RideConfig:
    """Return the store ride config the resume scenarios use."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


@dataclass(frozen=True, slots=True)
class ResumeRideSpec:
    """What a resume scenario's store ride must look like.

    ``quit_cleanly`` chooses the previous session's bookkeeping (a
    clean quit-keep-running, or a crash). ``ended_at`` pins the copy's
    time in that session row -- ``closed_at`` for a quit,
    ``heartbeat_at`` for a crash. ``start_at`` appends the ride's
    start event (the elapsed proof), and ``finish_and_reopen`` appends
    finish + reopen so the replay lands in REOPENED.
    """

    quit_cleanly: bool
    ended_at: datetime | None = None
    start_at: datetime | None = None
    finish_and_reopen: bool = False


def append_ride_events(store: Store, ride_id: int, spec: ResumeRideSpec) -> None:
    """Append start (and finish+reopen) events for the replay state.

    The events are produced by a real engine over an empty roster with
    the ride's own shape, exactly as ``store.roster_for`` will rebuild
    it at launch -- the store persists only the events, and
    ``load_engine`` reproduces the state by replaying them (E5.1.2).
    """
    if spec.start_at is None:
        return
    config = resume_ride_config()
    roster = Roster(
        entry_mode=config.entry_mode,
        plate_model=config.plate_model,
        max_team_size=config.max_team_size,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(config=config, shoe=shoe, clock=lambda: spec.start_at, roster=roster)
    store.append(ride_id, engine.start(at=spec.start_at))
    if spec.finish_and_reopen:
        store.append(ride_id, engine.finish())
        store.append(ride_id, engine.reopen())


def create_resumed_ride(db_path: Path, spec: ResumeRideSpec) -> int:
    """Create a store ride and a previous session that warrants resume.

    The previous session records the ride as running at exit: closed
    cleanly (``spec.quit_cleanly`` -- a quit-keep-running) or left
    open (a crash). ``spec.ended_at`` pins the copy's time in the
    session row -- ``closed_at`` for a quit, ``heartbeat_at`` for a
    crash -- so the scenario can assert the exact HH:MM in the wording.
    """
    boot = Store.open(db_path)
    try:
        ride_id = boot.create_ride(resume_ride_config())
        append_ride_events(boot, ride_id, spec)
    finally:
        boot.close()

    session = Store.open(db_path, active_ride_id=ride_id)
    if spec.quit_cleanly:
        session.close_session()
    session.close()

    if spec.ended_at is not None:
        column = "closed_at" if spec.quit_cleanly else "heartbeat_at"
        with sqlite3.connect(str(db_path)) as conn:  # commits on exit
            conn.execute(
                f"UPDATE app_session SET {column} = ?"  # noqa: S608 -- column is a fixed literal, never input
                " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
                (int(spec.ended_at.timestamp()),),
            )
    return ride_id


def library_ride_config(name: str) -> RideConfig:
    """Return the store ride config the E5.4.1 scenarios use."""
    return RideConfig(
        name=name,
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


def library_roster() -> Roster:
    """Build the MIXED rider_pooled roster the E5.4.1 scenarios persist.

    One solo entry plus one team of two riders with their own plates
    -- the roster shape ``Store.duplicate_ride`` must copy verbatim
    and ``Store.roster_for`` must rebuild identically.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def rich_race_roster() -> Roster:
    """Build the MIXED rider_pooled roster the E9.2.1 race stages.

    Four solo entries plus two teams -- one of two riders, one of
    three -- all ACTIVE (DNF is marked at runtime, not staged). The
    six plates round-trip verbatim through ``Store.save_roster`` and
    ``Store.roster_for``.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in ("1", "2", "3", "4"):
        roster.create_solo_entry(first_name=f"Solo {plate}", last_name="", plate=plate)
    roster.create_team_entry(
        display_name="Team A",
        riders=[
            Rider(first_name="Rider", last_name="11", plate="11"),
            Rider(first_name="Rider", last_name="12", plate="12"),
        ],
    )
    roster.create_team_entry(
        display_name="Team B",
        riders=[
            Rider(first_name="Rider", last_name="21", plate="21"),
            Rider(first_name="Rider", last_name="22", plate="22"),
            Rider(first_name="Rider", last_name="23", plate="23"),
        ],
    )
    return roster


def create_library_ride(path: Path, *, name: str, running: bool) -> int:
    """Create a store ride with a saved roster; timing when *running*.

    ``running`` appends start + one crossing so the ride reads RUNNING
    with a recorded lap -- the timing data ``duplicate_ride`` must
    leave out of the copy.
    """
    boot = Store.open(path)
    try:
        ride_id = boot.create_ride(library_ride_config(name))
        boot.save_roster(ride_id, library_roster())
        if running:
            boot.append(
                ride_id,
                Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
            )
            boot.append(
                ride_id,
                Event(
                    action="record_crossing",
                    payload={
                        "plate": "12",
                        "entry_id": "12",
                        "lap": 1,
                        "crossed_at": "2026-09-20T10:02:00",
                    },
                ),
            )
    finally:
        boot.close()
    return ride_id


def running_ride_with_roster(  # noqa: PLR0913 -- (path, actual_start, rng_seed) + the E9.2.1 rich-roster seam
    path: Path,
    *,
    actual_start: datetime | None = None,
    rng_seed: int | None = None,
    roster: Roster | None = None,
) -> int:
    """Create a running store ride and a quit-keep-running session.

    The previous session records the ride as running at a clean exit,
    so the launch shows resume_dlg and Continue sets the context's
    ``active_ride_id`` -- what File ▸ Duplicate Ride… reads (E5.4.1),
    and what the E9.2.1 full-race children resume. ``actual_start``
    overrides the pinned staged start (the race test stages a recent
    start so the resumed clock reads a positive elapsed); the default
    keeps the E5-era pinned 10:00 start for the existing scenarios.
    ``rng_seed`` pins the ride's shoe seed (E9.2.2/R-77): the nightly
    acceptance race owns its seed -- it injects it here and files it
    on failure; ``None`` keeps the DB-owned random seed (spec §4).
    ``roster`` replaces the saved roster (E9.2.1 stages its rich six-
    entry roster here); ``None`` keeps the E5-era
    :func:`library_roster` default.
    """
    start_iso = actual_start.isoformat() if actual_start is not None else "2026-09-20T10:00:00"
    boot = Store.open(path)
    try:
        ride_id = boot.create_ride(library_ride_config("GORBA EPIC 2026"), rng_seed=rng_seed)
        boot.save_roster(ride_id, roster if roster is not None else library_roster())
        boot.append(ride_id, Event(action="start", payload={"actual_start": start_iso}))
    finally:
        boot.close()
    session = Store.open(path, active_ride_id=ride_id)
    session.close_session()
    session.close()
    return ride_id


def race_db_facts(path: Path) -> dict[str, Any]:
    """Return read-only facts about *path* without opening a Store.

    The race test's verification read: ``Store.open`` inserts an
    ``app_session`` row (its launch bookkeeping), which would corrupt
    the very session sequence the kill/quit/relaunch test asserts on,
    so this reads the tables directly over a plain sqlite3 connection.

    The recorded-crossing proof is the ``audit`` trail -- the one
    channel ``Store.append`` persists (E9.1.3). The spec §2
    ``crossing`` table is only populated by EPIC 5's engine-sync
    writer, so it is deliberately not the fact this reports.

    Returns:
        A dict with ``rides`` (id/name/status rows), ``sessions``
        (id/closed_at/active_ride_id rows),
        ``record_crossing_count`` (``audit`` rows whose action is
        ``record_crossing``) and ``audit_actions`` (every action in
        append order).
    """
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rides = [
            dict(row) for row in conn.execute("SELECT id, name, status FROM ride ORDER BY id")
        ]
        sessions = [
            dict(row)
            for row in conn.execute(
                "SELECT id, closed_at, active_ride_id FROM app_session ORDER BY id"
            )
        ]
        record_crossing_count = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action = 'record_crossing'"
        ).fetchone()[0]
        audit_actions = [
            row["action"] for row in conn.execute("SELECT action FROM audit ORDER BY id")
        ]
    return {
        "rides": rides,
        "sessions": sessions,
        "record_crossing_count": record_crossing_count,
        "audit_actions": audit_actions,
    }
