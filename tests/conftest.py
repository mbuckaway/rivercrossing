# SPDX-License-Identifier: GPL-3.0-only
"""Root pytest configuration for the RiverCrossing test suite.

Shared fixtures (tmp DB, frozen clock, seeded shoes -- per
module-skeletons.md S5) land here as the modules that need them are
built; nothing under test yet requires one.
"""

from datetime import date, datetime

from rivercrossing.ride import RideConfig
from rivercrossing.roster import EntryMode, PlateModel

_GORBA_EVENT_DAY = date(2026, 9, 20)
_GORBA_PLANNED_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive local, Store's own contract


def gorba_config(*, min_lap_s: int = 1080, hold_short_laps: bool = False) -> RideConfig:
    """Return the canonical GORBA EPIC ride config.

    One shared builder replaces the repeated 13-field ``RideConfig``
    literal whose copies drifted across the test suite. Callers pass
    only the fields they vary: ``gorba_config(min_lap_s=1)`` builds a
    config whose laps are never flagged unless deliberately short, the
    default 1080 s is the real GORBA min-lap the store scenarios
    persist, and ``hold_short_laps=True`` opts a scenario into R-34's
    hold-for-review path (W4's always-deal default is False).
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
