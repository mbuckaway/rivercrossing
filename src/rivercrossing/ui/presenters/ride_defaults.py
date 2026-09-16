# SPDX-License-Identifier: GPL-3.0-only
"""Test-ride defaults -- the seeded "GORBA Test Ride #N" setup.

One source of truth for the ride an operator gets when a throwaway or
demonstration ride has to be up quickly: the generated
"GORBA Test Ride #N" name (:func:`next_test_ride_name`), the GORBA
reference ride's own venue/organizer/scorer and timing, and the shoe
and scoring shape that ride runs with
(:func:`build_test_ride_config`).

Pure Python -- no ``wx`` import may ever land here (R-71). The
buildable side is a plain :class:`~rivercrossing.ride.RideConfig`, so
every value is checked by that dataclass's own bounds the moment it is
built, and the fixed ones live here as module constants rather than as
literals repeated at each call site.

The shoe, lap-length and tie-break defaults are deliberately *not*
duplicated here: :data:`~rivercrossing.ride.DEFAULT_DECK_COUNT`,
:data:`~rivercrossing.ride.DEFAULT_JOKERS_PER_DECK`,
:data:`~rivercrossing.ride.DEFAULT_JOKERS_MODE`,
:data:`~rivercrossing.ride.DEFAULT_LAP_KM` and
:data:`~rivercrossing.ride.DEFAULT_TIEBREAK_ORDER` are imported and
passed straight through, so the test ride and a hand-opened ride setup
dialog cannot drift apart.
"""

from typing import TYPE_CHECKING

from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_JOKERS_MODE,
    DEFAULT_JOKERS_PER_DECK,
    DEFAULT_LAP_KM,
    DEFAULT_TIEBREAK_ORDER,
    RideConfig,
)
from rivercrossing.roster import EntryMode, PlateModel

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime

__all__ = [
    "DEFAULT_MAX_TEAM_SIZE",
    "DEFAULT_MIN_LAP_S",
    "DEFAULT_ORGANIZER",
    "DEFAULT_PLANNED_DURATION_S",
    "DEFAULT_SCORER",
    "DEFAULT_VENUE",
    "TEST_RIDE_NAME_PREFIX",
    "build_test_ride_config",
    "next_test_ride_name",
]

# Every generated name starts with this exact prefix; the numbering in
# next_test_ride_name keys off it and nothing else.
TEST_RIDE_NAME_PREFIX = "GORBA Test Ride #"

# The GORBA reference ride's own identity (spec.md §6's 8 km Sea to Sky
# loop is the same source as ride.DEFAULT_LAP_KM).
DEFAULT_VENUE = "Sea to Sky Gondola"
DEFAULT_ORGANIZER = "GORBA"
DEFAULT_SCORER = "K. Singh"

# Six hours planned over an 18-minute lap floor: the same timings the
# bootstrap ride in ui.app uses.
DEFAULT_PLANNED_DURATION_S = 21600
DEFAULT_MIN_LAP_S = 1080

# R-12's 2..10 bound leaves room for five; the test ride seats five.
DEFAULT_MAX_TEAM_SIZE = 5


def _suffix_number(name: str) -> int | None:
    """Return the test-ride number *name* carries, or ``None``.

    Only an exact :data:`TEST_RIDE_NAME_PREFIX` prefix followed by one
    or more ASCII decimal digits counts. Requiring ``isascii()`` strips
    the non-ASCII decimal digits ``str.isdecimal()`` also accepts, so
    the tail always converts with :func:`int` -- and a name whose tail
    is in another script or carries sign/space padding reads as
    unnumbered rather than as a number.
    """
    if not name.startswith(TEST_RIDE_NAME_PREFIX):
        return None
    suffix = name[len(TEST_RIDE_NAME_PREFIX) :]
    if not (suffix.isascii() and suffix.isdecimal()):
        return None
    return int(suffix)


def next_test_ride_name(existing_names: Iterable[str]) -> str:
    """Return the next free ``"GORBA Test Ride #N"`` name.

    The highest number already in use among *existing_names* decides
    the next one, so a deleted test ride's number is reused rather than
    skipped; a name that does not carry :data:`TEST_RIDE_NAME_PREFIX`
    exactly, or whose tail is not one or more ASCII decimal digits, is
    ignored. With nothing used -- or nothing recognisable -- the count
    starts at ``#1``.

    Args:
        existing_names: The ride names already in use.

    Returns:
        ``"{prefix}{highest + 1}"`` -- e.g. ``"GORBA Test Ride #4"``
        when ``"GORBA Test Ride #3"`` is taken.
    """
    suffixes = (_suffix_number(name) for name in existing_names)
    highest = max((suffix for suffix in suffixes if suffix is not None), default=0)
    return f"{TEST_RIDE_NAME_PREFIX}{highest + 1}"


def build_test_ride_config(*, name: str, event_date: date, planned_start: datetime) -> RideConfig:
    """Build the fixed test-ride config under the given identity.

    Only the identity varies: the ride's own venue, organizer, scorer,
    loop, timings, shoe and scoring shape are the module's defaults, so
    two test rides built a week apart differ in name and date alone.

    Args:
        name: The ride's display name --
            :func:`next_test_ride_name`'s own output at the call site.
        event_date: The ride's calendar date.
        planned_start: The planned gun time.

    Returns:
        The ready-to-run :class:`~rivercrossing.ride.RideConfig`: MIXED
        with rider-pooled plates, five to a team, the GORBA identity
        and 8 km loop over six hours planned and an 18-minute lap
        floor, an 8-deck one-joker total-mode shoe with no card cap,
        the default tie-break order, short laps held for review, and no
        logo.
    """
    return RideConfig(
        name=name,
        event_date=event_date,
        venue=DEFAULT_VENUE,
        lap_km=DEFAULT_LAP_KM,
        organizer=DEFAULT_ORGANIZER,
        scorer=DEFAULT_SCORER,
        planned_start=planned_start,
        planned_duration_s=DEFAULT_PLANNED_DURATION_S,
        min_lap_s=DEFAULT_MIN_LAP_S,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
        deck_count=DEFAULT_DECK_COUNT,
        jokers_per_deck=DEFAULT_JOKERS_PER_DECK,
        jokers_mode=DEFAULT_JOKERS_MODE,
        max_cards=None,
        tiebreak_order=DEFAULT_TIEBREAK_ORDER,
        logo_path=None,
        hold_short_laps=True,
    )
