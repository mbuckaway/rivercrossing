# SPDX-License-Identifier: GPL-3.0-only
"""Rider Simulator presenter -- one generated field, one scripted race.

``SimulatorPresenter`` drives the future Rider Simulator window: it
generates placeholder entries and riders from the app's own roster
primitives, then replays one fixed crossing order lap after lap through
a real :class:`~rivercrossing.ride.RideEngine`. No new business logic
lands here -- every mutation goes through the shipped ``Roster`` and
``RideEngine`` methods, so a simulated ride is the same ride the
console records, and one seed reproduces a whole run.

``generate_riders`` serves MIXED rides (solo entries plus teams);
``generate_solo_riders`` serves SOLO-only rides, where every generated
rider is their own entry. A solo-only roster refuses team creation
itself, so ``generate_riders`` lets the roster's own
``SoloOnlyRideError`` out rather than inventing a second rule.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from rivercrossing.ride import StartBlockedError
from rivercrossing.roster import (
    MIN_TEAM_SIZE,
    EntryType,
    PlateModel,
    Rider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Entry, Roster

__all__ = ["SimOutcome", "SimulatorPresenter"]

# The two sexes a generated rider is given; ``Rider.sex`` carries no
# third value.
_SEXES = ("M", "F")


@dataclass(frozen=True, slots=True)
class SimOutcome:
    """The result of one simulated race.

    ``blocked`` holds ``StartBlockedError.reasons`` -- one string per
    blocking issue -- when the ride refused to start, and ``None`` when
    the simulation ran. ``recorded`` counts only the crossings the
    engine accepted; ``cancelled`` is True when the caller's own cancel
    check ended the run early.
    """

    cancelled: bool
    recorded: int
    blocked: tuple[str, ...] | None = None


def _team_entries(roster: Roster) -> list[Entry]:
    """Return *roster*'s team entries, in creation order."""
    return [entry for entry in roster.entries if entry.type is EntryType.TEAM]


def _crossing_plate(roster: Roster, entry: Entry, rider: Rider) -> str:
    """Return the plate one rider's crossing is recorded under.

    A relay rider has no plate of their own, so the entry's plate
    crosses for them; a solo or pooled rider crosses on their own.
    """
    if roster.plate_model is PlateModel.TEAM_RELAY:
        return entry.plate
    return cast("str", rider.plate)


def _crossing_order(roster: Roster, rng: random.Random) -> list[tuple[str, Rider]]:
    """Return one shuffled (plate, rider) pair per roster rider."""
    order = [
        (_crossing_plate(roster, entry, rider), rider)
        for entry in roster.entries
        for rider in entry.riders
    ]
    rng.shuffle(order)
    return order


def _actual_start(engine: RideEngine) -> datetime:
    """Return the instant ``RideEngine.start()`` just recorded.

    ``RideEngine`` exposes no ``actual_start`` property, so this reads
    the engine's public event log: ``start()`` appends its own
    ``start`` or ``continue`` event last, and that payload carries the
    instant every lap time derives from.
    """
    event = engine.events[-1]
    return datetime.fromisoformat(str(event.payload["actual_start"]))


class SimulatorPresenter:
    """Presenter for the Rider Simulator window.

    Holds the engine and roster it drives and nothing else -- the
    window needs no view here, because :meth:`validate` returns its
    refusal message for the view to show.
    """

    def __init__(self, engine: RideEngine, roster: Roster) -> None:
        """Store the collaborators this simulator drives.

        Args:
            engine: The ride engine the simulated crossings record
                into.
            roster: The in-memory roster this presenter generates into.
        """
        self.engine = engine
        self.roster = roster
        # Set by every generate_* method, so the app knows whether the
        # callers' roster edits still need persisting when the window
        # closes (the rider/team editors' own roster_changed precedent).
        self.roster_changed = False
        # The seed the generator was last handed, reused to shuffle the
        # crossing order, so one seed reproduces a whole run.
        self._seed: int | None = None

    def generate_teams(self, count: int) -> None:
        """Create *count* empty teams named ``TEAM-0001`` upward.

        Args:
            count: How many empty teams to create.
        """
        for index in range(1, count + 1):
            self.roster.create_empty_team(display_name=f"TEAM-{index:04d}")
        self.roster_changed = True

    def generate_riders(  # noqa: PLR0913 -- (total, teams, solo) + the seed
        self, total: int, teams: int, solo: int, *, seed: int | None = None
    ) -> None:
        """Generate *total* riders: *solo* soloists, the rest on teams.

        The solo entries come first, so their plates sit below every
        team rider's. The team riders attach round-robin, so each team
        ends with 2..``max_team_size`` riders whenever the caller's own
        bounds hold. Names run one shared counter, never a random
        suffix, so every generated name is unique. Teams are created
        first when the roster holds none.

        Args:
            total: How many riders to generate.
            teams: How many teams to create when the roster has none.
            solo: How many of *total* riders ride solo.
            seed: The generator's seed; ``None`` starts fresh. The seed
                is kept and reused by :meth:`run_simulation`, so one
                seed reproduces a whole run.

        Raises:
            ValueError: *total*, *teams*, *solo*, or the resulting team
                riders fall outside their own bounds.
        """
        self._require_generator_inputs(total, teams, solo)
        rng = random.Random(seed)  # noqa: S311 -- placeholder data
        self._seed = seed
        entries = _team_entries(self.roster)
        if not entries:
            self.generate_teams(teams)
            entries = _team_entries(self.roster)
        counter = 0
        for _ in range(solo):
            counter += 1
            self._create_solo_entry(counter, rng)
        for index in range(total - solo):
            counter += 1
            self._add_team_rider(entries[index % len(entries)], counter, rng)
        self.roster_changed = True

    def generate_solo_riders(self, total: int, *, seed: int | None = None) -> None:
        """Generate *total* solo entries for a SOLO-only ride.

        The solo-only twin of :meth:`generate_riders`: no teams exist
        to fill, so every generated rider becomes a solo entry. Names,
        plates and sexes follow the same pattern, so the two generators
        produce interchangeable placeholders.

        Args:
            total: How many solo entries to create.
            seed: The generator's seed; ``None`` starts fresh. The seed
                is kept and reused by :meth:`run_simulation`, so one
                seed reproduces a whole run.

        Raises:
            ValueError: *total* is below 1.
        """
        if total < 1:
            msg = "total must be at least 1"
            raise ValueError(msg)
        rng = random.Random(seed)  # noqa: S311 -- placeholder data
        self._seed = seed
        for counter in range(1, total + 1):
            self._create_solo_entry(counter, rng)
        self.roster_changed = True

    def validate(self, *, laps: int, interval_minutes: int) -> str | None:
        """Return why *laps*/*interval_minutes* cannot run, else None.

        Args:
            laps: The number of laps to simulate.
            interval_minutes: The minutes between one lap and the next.

        Returns:
            The joined refusal, one clause per below-floor value, or
            ``None`` when both values are usable.
        """
        problems: list[str] = []
        if laps < 1:
            problems.append("laps must be at least 1")
        if interval_minutes < 1:
            problems.append("interval must be at least 1 minute")
        if problems:
            return "; ".join(problems)
        return None

    def run_simulation(  # noqa: PLR0913 -- (laps, interval) + the two view hooks
        self,
        laps: int,
        interval_minutes: int,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> SimOutcome:
        """Replay one scripted race of *laps* laps through the engine.

        The crossing order is built once and reused for every lap, so
        lap ``L``'s crossing ``i`` lands at ``actual_start + L *
        interval + offset_i``, with the offsets one sorted draw. Every
        later lap of an entry is therefore exactly one interval longer
        than the one before it. A refusal to start -- an empty roster,
        an incomplete setup, a team below the floor -- comes back as
        ``blocked``, never a raise.

        Args:
            laps: How many laps to simulate.
            interval_minutes: The minutes between one lap and the next.
            on_progress: Called after every recorded crossing, with the
                crossings done and the run's total.
            is_cancelled: Called after every recorded crossing; a True
                verdict ends the run early. ``None`` never cancels.

        Returns:
            The run's outcome: the accepted crossings, whether the
            caller cancelled, and the start refusal when blocked.
        """
        try:
            self.engine.start()
        except StartBlockedError as exc:
            return SimOutcome(cancelled=False, recorded=0, blocked=tuple(exc.reasons))
        start = _actual_start(self.engine)
        rng = random.Random(self._seed)  # noqa: S311 -- replays one seed's run
        order = _crossing_order(self.roster, rng)
        interval = timedelta(minutes=interval_minutes)
        offsets = sorted(rng.random() * interval.total_seconds() for _ in range(len(order)))
        total = laps * len(order)
        recorded = 0
        completed = 0
        cancelled = False
        for lap in range(1, laps + 1):
            lap_start = start + interval * lap
            for index, (plate, _rider) in enumerate(order):
                instant = lap_start + timedelta(seconds=offsets[index])
                if self.engine.record_crossing(plate, at=instant).accepted:
                    recorded += 1
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break
            if cancelled:
                break
        self.engine.stop()
        return SimOutcome(cancelled=cancelled, recorded=recorded, blocked=None)

    def _require_generator_inputs(self, total: int, teams: int, solo: int) -> None:
        """Refuse generator inputs outside their own bounds.

        Raises:
            ValueError: *total* is below 1, *teams* is below 1, *solo*
                falls outside ``0..total``, or the team riders fall
                outside ``MIN_TEAM_SIZE * teams`` ..
                ``max_team_size * teams``.
        """
        if total < 1:
            msg = "total must be at least 1"
            raise ValueError(msg)
        if teams < 1:
            msg = "teams must be at least 1"
            raise ValueError(msg)
        if not 0 <= solo <= total:
            msg = f"solo must be between 0 and {total}"
            raise ValueError(msg)
        team_riders = total - solo
        lowest = MIN_TEAM_SIZE * teams
        highest = self.roster.max_team_size * teams
        if not lowest <= team_riders <= highest:
            msg = f"team riders must be between {lowest} and {highest}, got {team_riders}"
            raise ValueError(msg)

    def _add_team_rider(self, entry: Entry, counter: int, rng: random.Random) -> None:
        """Attach the next generated rider to *entry*.

        A pooled ride gives every rider their own plate; a relay ride
        leaves them plateless, because the entry owns the plate (S1).
        """
        pooled = self.roster.plate_model is PlateModel.RIDER_POOLED
        rider = Rider(
            first_name=f"FIRSTNAME-{counter:04d}",
            last_name=f"LASTNAME-{counter:04d}",
            plate=self.roster.next_free_plate() if pooled else None,
            sex=rng.choice(_SEXES),
        )
        self.roster.add_rider_to_team(rider, to_entry=entry)

    def _create_solo_entry(self, counter: int, rng: random.Random) -> None:
        """Create the ``counter``-th generated solo entry.

        Shared by the MIXED generator's soloists and the SOLO-only
        generator, so both name, plate and sex every placeholder the
        same way.
        """
        self.roster.create_solo_entry(
            first_name=f"FIRSTNAME-{counter:04d}",
            last_name=f"LASTNAME-{counter:04d}",
            plate=self.roster.next_free_plate(),
            sex=rng.choice(_SEXES),
        )
