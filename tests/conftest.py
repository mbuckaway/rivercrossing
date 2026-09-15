# SPDX-License-Identifier: GPL-3.0-only
"""Root pytest configuration for the RiverCrossing test suite.

Shared fixtures (tmp DB, frozen clock, seeded shoes -- per
module-skeletons.md S5) land here as the modules that need them are
built. The helpers that had been hand-copied across test modules also
live here once -- :func:`gorba_config`, and the byte-identical
:func:`_records` (four copies), :func:`_roster_with_entries` (four)
and :func:`_pooled_team_roster` (three) -- so every caller imports the
one copy (``from conftest import ...``, pytest's root-conftest seam).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from rivercrossing.ride import RideConfig
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster

if TYPE_CHECKING:
    from pathlib import Path

_GORBA_EVENT_DAY = date(2026, 9, 20)
_GORBA_PLANNED_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive local, Store's own contract


def gorba_config(*, min_lap_s: int = 1080, hold_short_laps: bool = True) -> RideConfig:
    """Return the canonical GORBA EPIC ride config.

    One shared builder replaces the repeated 13-field ``RideConfig``
    literal whose copies drifted across the test suite. Callers pass
    only the fields they vary: ``gorba_config(min_lap_s=1)`` builds a
    config whose laps are never flagged unless deliberately short, the
    default 1080 s is the real GORBA min-lap the store scenarios
    persist, and ``hold_short_laps`` defaults to the product default
    -- W4's hold-for-review path, which a fresh setup dialog submits --
    so a scenario reaches always-deal only by passing
    ``hold_short_laps=False`` explicitly.
    """
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=_GORBA_EVENT_DAY,
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=_GORBA_PLANNED_START,
        planned_duration_s=21600,
        min_lap_s=min_lap_s,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        hold_short_laps=hold_short_laps,
    )


def _records(path: Path) -> list[dict[str, object]]:
    """Return the NDJSON records at *path* (none when it is absent)."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _roster_with_entries(*plates: str) -> Roster:
    """Build a MIXED rider_pooled roster of one solo entry per plate."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(first_name=f"Rider {plate}", last_name="", plate=plate)
    return roster


def _pooled_team_roster() -> Roster:
    """Build a rider_pooled team roster: Sarah (45), Priya (9)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_team_entry(
        display_name="Dirt Dynamos",
        riders=[Rider(first_name="Sarah", plate="45"), Rider(first_name="Priya", plate="9")],
    )
    return roster
