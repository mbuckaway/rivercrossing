# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's store-to-engine event wiring (E9.1.3).

:func:`rivercrossing.ui.app._wire_store_append` attaches a store's
``append`` as the engine's ``on_event`` sink, so every engine mutation
-- crossings, undo, corrections, lifecycle -- persists one ``audit``
row per event, not just plate entry. This module proves the seam
headless with a fake store and a real engine: the exact ``(ride_id,
event)`` calls land on the fake, in order, and events recorded before
the wiring was attached never land at all (the load_engine replay tail
is not re-persisted). A failed append (a locked or unwritable
database) is caught inside the sink and surfaced through its
``notify`` seam -- never an unguarded raise into the engine mutation,
which a wx handler would swallow -- while the mutation itself stays
complete in memory.
"""

import sqlite3
from datetime import date, datetime

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module


class _FakeStore:
    """Record every append call; nothing else is needed."""

    def __init__(self) -> None:
        """Start with an empty append log."""
        self.appends: list[tuple[int, Event]] = []

    def append(self, ride_id: int, event: Event) -> None:
        """Record one (ride_id, event) append call."""
        self.appends.append((ride_id, event))


class _FailingAppendStore:
    """Raise a DB error on every append call (the disk/DB failure)."""

    def append(self, _ride_id: int, _event: Event) -> None:
        """Refuse the write like a locked database."""
        raise sqlite3.OperationalError("database is locked")


def _engine(plate: str = "12") -> RideEngine:
    """Build a RUNNING-capable engine: one solo entry, fixed clock."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name=f"Rider {plate}", last_name="", plate=plate)
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive by design
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    clock = lambda: datetime(2026, 9, 20, 10, 1)  # noqa: E731, DTZ001 -- a fixed fake instant
    return RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)


def test_wire_store_append_persists_the_crossing_to_the_fake_store() -> None:
    """A recorded crossing appends exactly (ride_id, event)."""
    store = _FakeStore()
    engine = _engine()
    engine.start()
    notices: list[str] = []

    app_module._wire_store_append(engine, store, ride_id=7, notify=notices.append)
    engine.record_crossing("12")

    assert store.appends == [
        (
            7,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:01:00",
                },
            ),
        )
    ]
    assert notices == []


def test_wire_store_append_never_reappends_the_replay_tail() -> None:
    """Events before wiring stay silent; only later mutations land."""
    store = _FakeStore()
    engine = _engine()
    engine.start()
    engine.record_crossing("12")
    notices: list[str] = []

    app_module._wire_store_append(engine, store, ride_id=7, notify=notices.append)
    engine.undo_last()

    assert [event.action for _, event in store.appends] == ["undo"]
    assert store.appends[0][0] == 7
    assert notices == []


def test_wire_store_append_given_a_failing_store_posts_a_notice_and_keeps_the_crossing() -> None:
    """A refused append surfaces; the in-memory mutation stays complete.

    The sink runs inside the engine's mutation (``ride._append``), so
    an unguarded store error would abort ``record_crossing`` mid-way
    -- the crossing exists in memory but the presenter's caller never
    finishes, and the wx handler swallows the raise with zero signal
    (EPIC3-SESSION-SUMMARY.md's measured note).
    """
    engine = _engine()
    engine.start()
    notices: list[str] = []

    app_module._wire_store_append(engine, _FailingAppendStore(), ride_id=7, notify=notices.append)
    engine.record_crossing("12")

    assert len(engine.crossings) == 1
    assert notices == ["Could not save event: database is locked"]
