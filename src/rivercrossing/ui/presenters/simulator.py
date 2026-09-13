# SPDX-License-Identifier: GPL-3.0-only
"""Rider Simulator presenter -- one generated field, one scripted race.

``SimulatorPresenter`` drives the future Rider Simulator window: it
generates placeholder entries and riders from the app's own roster
primitives, then replays one fixed crossing order lap after lap through
a real :class:`~rivercrossing.ride.RideEngine`. The order holds one
plate per *entry* on either plate model: a relay team crosses once a
lap under the entry's own plate, and a pooled team crosses once a lap
under the plate of the one rider on course that lap (rotating
round-robin), so a team's lap count equals a solo's. No new business
logic lands here -- every mutation goes through the shipped ``Roster``
and ``RideEngine`` methods, so a simulated ride is the same ride the
console records, and one seed reproduces a whole run.

``generate_riders`` serves MIXED rides (solo entries plus teams);
``generate_solo_riders`` serves SOLO-only rides, where every generated
rider is their own entry. A solo-only roster refuses team creation
itself, so ``generate_riders`` lets the roster's own
``SoloOnlyRideError`` out rather than inventing a second rule.

The three pure helpers above the presenter are the dialog's own
derivations: :func:`resolve_solo` (the solo field follows from the
rider and team counts), :func:`default_interval_minutes` (GO's opening
interval follows from the live ride's lap length and the operator's
average speed) and :func:`check_message` (the Check button's
explanation, which names the numbers).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from rivercrossing.ride import StartBlockedError
from rivercrossing.roster import (
    DEFAULT_MAX_TEAM_SIZE,
    MIN_TEAM_SIZE,
    EntryType,
    PlateModel,
    Rider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Entry, Roster

__all__ = [
    "SimOutcome",
    "SimulatorPresenter",
    "check_message",
    "default_interval_minutes",
    "resolve_solo",
]

# The two sexes a generated rider is given; ``Rider.sex`` carries no
# third value.
_SEXES = ("M", "F")

# simulation.xrc's interval_spin authored range (1..240 minutes):
# default_interval_minutes clamps to it, so the seeded spin can never
# open outside the control's own bounds.
INTERVAL_MIN_MINUTES = 1
INTERVAL_MAX_MINUTES = 240

# One lap at the average speed, plus this buffer: GO's opening interval
# leaves the field room to complete the lap before the next one starts
# (the demo 8 km / 12 km/h ride opens on 40 + 5 = 45 minutes).
_INTERVAL_BUFFER_MINUTES = 5


def _team_rider_bounds(teams: int, *, min_team_size: int, max_team_size: int) -> tuple[int, int]:
    """Return the lowest and highest team riders *teams* may hold."""
    return (min_team_size * teams, max_team_size * teams)


def resolve_solo(  # noqa: PLR0913 -- (riders, teams) + the two team-size bounds
    riders: int,
    teams: int,
    *,
    min_team_size: int = MIN_TEAM_SIZE,
    max_team_size: int = DEFAULT_MAX_TEAM_SIZE,
) -> int | None:
    """Return the solo count *riders* and *teams* imply, else None.

    The dialog's auto-fill rule, mirroring the generator's own bound
    check: the team riders (``riders - solo``) must land in
    ``min_team_size * teams .. max_team_size * teams``. Teams fill
    first, so a field large enough for full teams leaves the remainder
    solo (the 175-rider / 40-team defaults leave 15), a field that
    fits on teams alone leaves none, and a field below the floor has
    no valid solo count at all.

    Args:
        riders: The total number of riders.
        teams: How many teams the riders split across.
        min_team_size: The smallest legal team size.
        max_team_size: The ride's own team ceiling.

    Returns:
        The solo count, or ``None`` when *riders* cannot fill *teams*.
    """
    lowest, highest = _team_rider_bounds(
        teams, min_team_size=min_team_size, max_team_size=max_team_size
    )
    if riders >= highest:
        return riders - highest
    if riders >= lowest:
        return 0
    return None


def default_interval_minutes(  # noqa: PLR0913 -- (lap, speed) + the spin's two bounds
    lap_km: float,
    avg_speed_kmh: float,
    *,
    minimum: int = INTERVAL_MIN_MINUTES,
    maximum: int = INTERVAL_MAX_MINUTES,
) -> int:
    """Return GO's opening minutes between one lap and the next.

    One lap at the field's average speed, rounded to the whole minute,
    plus a five-minute buffer -- so a longer or slower lap opens on a
    proportionally wider gap. The caller's own spin bounds clamp the
    result.

    Args:
        lap_km: The live ride's lap length (``engine.config.lap_km``).
        avg_speed_kmh: The operator's average rider speed, in km/h.
        minimum: The smallest interval the spin accepts.
        maximum: The largest interval the spin accepts.

    Returns:
        The clamped whole-minute default.
    """
    minutes = round(lap_km / avg_speed_kmh * 60) + _INTERVAL_BUFFER_MINUTES
    return min(max(minutes, minimum), maximum)


def check_message(  # noqa: PLR0913 -- (riders, teams) + the two team-size bounds
    riders: int,
    teams: int,
    *,
    min_team_size: int = MIN_TEAM_SIZE,
    max_team_size: int = DEFAULT_MAX_TEAM_SIZE,
) -> str:
    """Return the Check button's riders/teams/solo explanation.

    Says what the relationship is, names the bounds the current counts
    imply, and -- when the counts cannot work -- how to fix them, in
    the two field names the dialog shows.

    Args:
        riders: The current "Number of riders" value.
        teams: The current "Number of teams" value.
        min_team_size: The smallest legal team size.
        max_team_size: The ride's own team ceiling.

    Returns:
        The message, one relationship line per paragraph.
    """
    lowest, highest = _team_rider_bounds(
        teams, min_team_size=min_team_size, max_team_size=max_team_size
    )
    riders_word = "rider" if riders == 1 else "riders"
    teams_word = "team" if teams == 1 else "teams"
    # RUF001: the multiplication, en-dash and minus glyphs are this
    # message's intended display spelling.
    head = (
        "Team riders = teams × riders per team "  # noqa: RUF001 -- display glyph
        f"({min_team_size}–{max_team_size} per team). "  # noqa: RUF001 -- display glyph
        "Solo riders = riders − team riders."  # noqa: RUF001 -- display glyph
    )
    with_counts = (
        f"With {riders} {riders_word} and {teams} {teams_word}, team riders must be "
        f"between {lowest} and {highest}"
    )
    solo = resolve_solo(riders, teams, min_team_size=min_team_size, max_team_size=max_team_size)
    if solo is None:
        fixes = [f"Increase Number of riders to at least {lowest}"]
        if riders >= min_team_size:
            fixes.append(f"or reduce Number of teams to {riders // min_team_size}")
        return "\n".join(
            (
                head,
                f"{with_counts}, but the field has only {riders} {riders_word}.",
                " ".join(fixes) + ".",
            )
        )
    return "\n".join(
        (
            head,
            (
                f"{with_counts}; this field fills the teams to {riders - solo}, so "
                f"solo riders = {solo}. Ready to generate."
            ),
        )
    )


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


def _plates_to_record(roster: Roster, lap: int = 0) -> list[str]:
    """Return the plates one simulated lap records, one per entry.

    The console's own rule (S1): a relay ride types the entry's plate
    once per entry, so an N-rider team crosses once a lap; a pooled
    ride types one rider's own plate per entry -- a solo rider's, or,
    for a team, the plate of the rider on course that lap. One
    crossing per entry per lap is what keeps a team's lap count equal
    to a solo's.

    Args:
        roster: The field to read.
        lap: The zero-based lap whose team representatives cross.
    """
    if roster.plate_model is PlateModel.TEAM_RELAY:
        return [entry.plate for entry in roster.entries]
    return [_pooled_plate(entry, lap) for entry in roster.entries]


def _pooled_plate(entry: Entry, lap: int) -> str:
    """Return *entry*'s crossing plate on *lap* under RIDER_POOLED.

    A solo entry crosses under its own plate; a team sends one rider
    per lap, rotating round-robin through its members, so every team
    rider's plate appears for roughly ``laps / team_size`` laps. A
    riderless team -- refused at start, so reachable only through the
    DRAFT-time zero-rider team -- falls back to the entry's own
    provisional plate claim.
    """
    if entry.type is EntryType.SOLO or not entry.riders:
        return entry.plate
    return cast("str", entry.riders[lap % len(entry.riders)].plate)


def _lap_offsets(entry_count: int, interval_minutes: int) -> list[int]:
    """Return each entry position's whole-minute offset inside a lap.

    The offsets spread evenly across ``0 .. interval_minutes - 1``: the
    last position crosses one minute before the next lap's first, so
    the field always has a minute's gap. A one-entry lap sits at its
    own start instead -- the gap to the next lap is then the whole
    interval, which is still at least a minute.
    """
    if entry_count <= 1:
        return [0] * entry_count
    span = interval_minutes - 1
    last = entry_count - 1
    return [index * span // last for index in range(entry_count)]


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

        The entry order is built once and reused for every lap: lap 1's
        crossings land at ``actual_start + offset``, lap ``L``'s at
        ``actual_start + (L - 1) * interval + offset``, so every later
        lap of an entry is exactly one interval after the one before
        it. Each offset is a whole number of minutes spread across
        ``0 .. interval_minutes - 1`` (:func:`_lap_offsets`), so a
        lap's last crossing sits one minute before the next lap's
        first. The plate each entry crosses under comes from that
        lap's own :func:`_plates_to_record`, reproducing a pooled
        team's rotating representative. A refusal to start -- an empty
        roster, an incomplete setup, a team below the floor -- comes
        back as ``blocked``, never a raise.

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
        order = list(range(len(self.roster.entries)))
        rng.shuffle(order)
        interval = timedelta(minutes=interval_minutes)
        offsets = _lap_offsets(len(order), interval_minutes)
        total = laps * len(order)
        recorded = 0
        completed = 0
        cancelled = False
        for lap in range(laps):
            lap_start = start + interval * lap
            plates = _plates_to_record(self.roster, lap)
            for index, position in enumerate(order):
                instant = lap_start + timedelta(minutes=offsets[index])
                if self.engine.record_crossing(plates[position], at=instant).accepted:
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
        lowest, highest = _team_rider_bounds(
            teams, min_team_size=MIN_TEAM_SIZE, max_team_size=self.roster.max_team_size
        )
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
