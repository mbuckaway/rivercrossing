# SPDX-License-Identifier: GPL-3.0-only
"""Ride state machine (spec §3, R-36) and setup-time settings (§2).

Pure Python only -- no ``wx`` import may ever land in this module
(R-71); the "wx stays inside rivercrossing.ui" import-linter
contract in ``pyproject.toml`` enforces it.

:class:`RideConfig` is pre-created here for E3.5 (Ride Setup dialog),
next to :class:`RideStatus` -- the same "reserve the name ahead of its
first real consumer" precedent ``RideStatus`` itself set for the
state machine E4's ``RideEngine`` implements (module-skeletons.md S4:
``RideEngine.__init__(config: RideConfig, shoe: Shoe, clock: ...)``).
``entry_mode``/``plate_model`` are annotated as ``rivercrossing.
roster.EntryMode``/``PlateModel`` under ``TYPE_CHECKING`` only, never
imported at runtime: ``roster.py`` already imports ``RideStatus`` from
this module at import time, so a runtime import the other way would
be circular. Python 3.14's lazy annotation evaluation (PEP 649) makes
this safe -- neither field needs the real enum class at runtime, only
a caller (``roster.Roster``, a submitted setup form) ever needs to
have one already.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime
    from pathlib import Path

    from rivercrossing.roster import EntryMode, PlateModel

__all__ = [
    "DEFAULT_DECK_COUNT",
    "DEFAULT_JOKERS_PER_DECK",
    "DEFAULT_TIEBREAK_ORDER",
    "TIEBREAK_HIGH_CARD",
    "TIEBREAK_LAPS",
    "TIEBREAK_TOTAL_TIME",
    "RideConfig",
    "RideConfigError",
    "RideStatus",
]


class RideStatus(StrEnum):
    """A ride's lifecycle state (spec §3).

    A plain ``Enum`` would need callers to unwrap ``.value`` before
    comparing to or storing the spelling read from the database;
    ``StrEnum`` members already *are* that string, so
    ``RideStatus("running") == "running"`` and a member can be
    written straight into the ``ride.status`` column with no
    unwrapping step either side of the round trip.

    Values are the exact lowercase spellings stored in that column
    (spec §2), so ``RideStatus("running")`` round-trips a value read
    straight back out of the database.
    """

    DRAFT = "draft"
    RUNNING = "running"
    FINISHED = "finished"
    REOPENED = "reopened"


class RideConfigError(ValueError):
    """A :class:`RideConfig` field violates its own spec-defined bound.

    Subclasses ``ValueError`` (T-12's narrowest-exception spirit):
    every case this raises really is "the value given is out of
    range for this field," not a structural/logic error.
    """


# R-14's three named tie-break criteria, in xrc-windows.md's own
# ride_setup_dlg mock order ("① Most laps ② Total time ③ High-card
# draw"). No earlier module names a wire identifier for any of them --
# spec.md §5/R-14 name the *rule*, never a stored spelling -- so this
# is the first place one is needed, and EPIC 6's eventual
# ``rivercrossing.standings`` (spec.md §11's reserved name for the
# real ranking logic) should import these rather than re-invent them
# (this task's own doc-silence).
TIEBREAK_LAPS = "laps"
TIEBREAK_TOTAL_TIME = "total_time"
TIEBREAK_HIGH_CARD = "high_card"

DEFAULT_TIEBREAK_ORDER: tuple[str, str, str] = (
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
    TIEBREAK_HIGH_CARD,
)

# R-12's own 2..10 bound (also roster.py's MIN_TEAM_SIZE/
# MAX_TEAM_SIZE_LIMIT) -- duplicated as plain literals rather than
# imported: roster.py already imports RideStatus from this module at
# runtime, so importing back would be circular (this module's own
# docstring). The two modules share this bound by spec coincidence
# (R-12), not because either owns it.
_MIN_TEAM_SIZE = 2
_MAX_TEAM_SIZE_LIMIT = 10

# spec.md §4: "the shoe is deck_count x (52 + jokers_per_deck) cards
# (default 8 decks x 2 jokers = 432 for a 180-entry field ...) --
# the XRC canvas draws 2 decks, so the default is an open question
# owned by the ride-setup work in E3/E4: the XRC declares no value
# and the presenter supplies it." E3.5's own binding decision
# (2026-08-08) resolves that question to 8; the canvas's 2 is a mock
# artifact, not a competing default.
DEFAULT_DECK_COUNT = 8

# xrc-windows.md's ride_setup_dlg mock: jokers_2_radio is XRC's own
# checked default (setup.xrc's <value>1</value>), unlike decks_spin;
# recorded here too so RideConfig's own default never drifts from it.
DEFAULT_JOKERS_PER_DECK = 2


@dataclass(frozen=True, kw_only=True, slots=True)
class RideConfig:
    """One ride's setup-time settings (spec §2 ``ride`` row).

    Built by :class:`~rivercrossing.ui.presenters.setup.SetupPresenter`
    from ``ride_setup_dlg``'s submitted form (E3.5) --
    :class:`~rivercrossing.ui.presenters.riders.RiderFormValues` is
    this codebase's own precedent for a frozen, keyword-only input
    dataclass at a view/presenter boundary, mirrored here at the
    presenter/domain boundary instead. :class:`RideEngine` (E4) takes
    one of these as its own ``config`` constructor argument
    (module-skeletons.md:158).

    Deliberately excludes every DB-only or derived column spec §2's
    ``ride`` row also carries (``id``, ``actual_start``,
    ``finished_at``, ``status``, ``rng_seed``, ``created_at``,
    ``updated_at``, ``logo_png`` BLOB) -- none of those exist before a
    ride is actually created; ``RideEngine``/EPIC 5's Store supply
    them, never this dialog. ``logo_path`` carries the *picker's* own
    chosen file; converting it to the stored BLOB is EPIC 5's own
    concern, not this dataclass's.

    ``event_date``/``planned_start`` both round-trip the ``ride``
    table's own two separate columns (spec §2): ``ride_setup_dlg``
    itself has only one ``date_picker`` and one time-only
    ``start_time_picker``, so combining them into ``planned_start``
    (a full timestamp) is ``SetupPresenter``'s own job, not this
    dataclass's -- spec.md is silent on the exact combination, so
    this is recorded as this task's own doc-silence rather than
    invented and left unstated.
    """

    name: str
    event_date: date
    venue: str
    lap_km: float
    organizer: str
    scorer: str
    planned_start: datetime
    planned_duration_s: int
    min_lap_s: int
    entry_mode: EntryMode
    plate_model: PlateModel
    max_team_size: int = 4
    deck_count: int = DEFAULT_DECK_COUNT
    jokers_per_deck: int = DEFAULT_JOKERS_PER_DECK
    max_cards: int | None = None
    tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER
    logo_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate this config's own spec-defined bounds.

        Raises:
            RideConfigError: ``max_team_size`` is outside 2..10
                (R-12), ``deck_count`` is below 1 (spec §4), or
                ``planned_duration_s``/``min_lap_s`` is not positive
                (spec §2/§6).
        """
        if not _MIN_TEAM_SIZE <= self.max_team_size <= _MAX_TEAM_SIZE_LIMIT:
            msg = (
                f"max_team_size must be {_MIN_TEAM_SIZE}..{_MAX_TEAM_SIZE_LIMIT}, "
                f"got {self.max_team_size}"
            )
            raise RideConfigError(msg)
        if self.deck_count < 1:
            msg = f"deck_count must be >= 1, got {self.deck_count}"
            raise RideConfigError(msg)
        if self.planned_duration_s <= 0:
            msg = f"planned_duration_s must be positive, got {self.planned_duration_s}"
            raise RideConfigError(msg)
        if self.min_lap_s <= 0:
            msg = f"min_lap_s must be positive, got {self.min_lap_s}"
            raise RideConfigError(msg)
