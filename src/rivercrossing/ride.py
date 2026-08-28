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

:class:`RideEngine` (E4.1) implements the spec §3 state machine over
that config: DRAFT -> RUNNING -> FINISHED <-> REOPENED, wall-clock
timing (spec §6, R-30), the minimal crossing path, and the standings
snapshot. E4.2 completes the crossing path here: one shoe deal per
accepted crossing (R-40) with a mid-ride reshuffle audit, the
short-lap hold/confirm/void surface (R-34), and the compensating-write
undo (R-33). It imports ``hands``/``standings``/``cards`` (below it in
the S3 dependency graph) and never ``roster`` at runtime -- the
roster is duck-typed through the constructor's ``roster`` parameter,
whose type is imported under ``TYPE_CHECKING`` only (same cycle
reason as above); ``RideEngine``'s own docstring records the full
doc-silence list.
"""

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rivercrossing.cards import Card, ShoeClosedError, ShoeEmpty
from rivercrossing.hands import best_hand
from rivercrossing.standings import EntryResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date, datetime
    from pathlib import Path

    from rivercrossing.cards import Shoe
    from rivercrossing.roster import EntryMode, PlateModel, Roster

__all__ = [
    "DEFAULT_DECK_COUNT",
    "DEFAULT_JOKERS_PER_DECK",
    "DEFAULT_TIEBREAK_ORDER",
    "TIEBREAK_HIGH_CARD",
    "TIEBREAK_LAPS",
    "TIEBREAK_TOTAL_TIME",
    "Crossing",
    "CrossingResult",
    "Event",
    "HeldCrossing",
    "IllegalStateError",
    "RideConfig",
    "RideConfigError",
    "RideEngine",
    "RideEngineError",
    "RideStatus",
    "StartBlockedError",
    "UnknownPlateError",
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


# ==================================================== E4.1 engine


class RideEngineError(Exception):
    """Base for every ride engine invariant violation."""


class IllegalStateError(RideEngineError):
    """A method was called in a ride state that forbids it (spec §3).

    Raised by the transition methods (``start``/``finish``/``reopen``/
    ``stop``/``set_start_time``) for transitions the state machine
    forbids, and by ``elapsed``/``remaining`` before the ride starts.
    """


class StartBlockedError(RideEngineError):
    """``start()`` refused because the roster is not ready to start.

    The engine consults ``Roster.validate_for_start()`` (R-12's team
    floor) as the start gate; any violation blocks the transition.
    """


class UnknownPlateError(RideEngineError):
    """``deal_manual()`` could not resolve *plate* to any entry.

    A corrections command fails loudly, unlike ``record_crossing``'s
    console path, which returns a refusal result so the entry field
    can flash its cue; E7's manual-deal dialog surfaces this as the
    error for a mistyped plate.
    """


@dataclass(frozen=True)
class Event:
    """One ride-level audit event (spec §2 ``audit`` row shape).

    Mirrors ``roster.AuditEvent`` exactly (action + payload); every
    :class:`RideEngine` mutation appends the :class:`Event` it returns
    to :attr:`RideEngine.events`, which EPIC 5's Store persists and
    replays to rebuild the engine. ``payload`` is JSON-ready: datetimes
    are stored as ISO-8601 strings.
    """

    action: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class Crossing:
    """One recorded lap crossing (spec §2 ``crossing`` row, minus ids).

    ``seq`` is 1-based per entry; ``entry_id`` is the entry's plate in
    this store-less model (doc-silence, ``RideEngine``). Lap times are
    never stored -- spec §6 derives them from the entry's previous
    crossing (or ``actual_start`` for lap 1), so a ``set_start_time``
    retro-fix recomputes lap-1 automatically.
    """

    entry_id: str
    seq: int
    crossed_at: datetime


@dataclass(frozen=True)
class CrossingResult:
    """The caller-facing outcome of one ``record_crossing`` call.

    ``accepted`` False with a ``reason`` signals a refusal -- the ride
    is not running, the ride is stopped (E4.1.3), or the plate is
    unknown (``reason="unknown_plate"``, E4.2.4) -- without raising;
    the error cue itself (shake, red border, buzz) is E4.4's UI
    concern. On success: ``entry_id``/``entry_name`` name the resolved
    entry, ``lap`` is the credited lap number, ``lap_time`` is spec
    §6's derived lap time, ``card`` is the shoe card dealt for this
    lap (R-40) and ``flagged`` is True when the lap fell under
    ``config.min_lap_s`` -- the card is then *held*
    (:meth:`RideEngine.held_crossings`), not credited (R-34).
    """

    accepted: bool
    plate: str
    entry_id: str | None = None
    entry_name: str | None = None
    lap: int = 0
    lap_time: float = 0.0
    reason: str | None = None
    card: Card | None = None
    flagged: bool = False


@dataclass(frozen=True)
class HeldCrossing:
    """One short-lap crossing whose card awaits confirm or void (R-34).

    The review surface :meth:`RideEngine.held_crossings` returns these
    so E4.4's review panel can show which entry/lap and which card is
    held without reaching into the engine's internals. ``card`` is
    deliberately not part of the credited hand -- it stays in limbo
    until the operator confirms or voids it.
    """

    crossing: Crossing
    card: Card


class RideEngine:
    """The ride state machine, timing core and minimal crossing path.

    Implements spec §3's transitions -- DRAFT -> RUNNING (``start``),
    RUNNING -> FINISHED (``finish``), FINISHED -> REOPENED (``reopen``)
    and REOPENED -> FINISHED (``finish`` again) -- with every other
    transition raising :class:`IllegalStateError`. Timing is
    wall-clock (spec §6, R-30): ``elapsed()``/``remaining()`` derive
    ``now - actual_start`` from the injected ``clock``, never a stored
    timer, so quit, crash or stop costs no time.

    Doc-silence resolutions recorded here (module-skeletons.md S4's
    skeleton is binding; these settle the gaps it leaves):

    - **roster parameter.** The skeleton's ``__init__(config, shoe,
      clock)`` gains ``roster`` because the engine needs plate->entry
      resolution, the start gate and ``mark_has_data``. The roster is
      annotated under ``TYPE_CHECKING`` and duck-typed at runtime:
      ``roster.py`` imports ``RideStatus`` from this module, so a
      runtime import back would be circular (module docstring).
    - **Event shape.** Every mutation appends and returns an
      ``Event(action, payload)`` mirroring ``roster.AuditEvent``;
      ``record_crossing`` additionally returns ``CrossingResult`` for
      the caller while its ``Event`` lands in :attr:`events`.
    - **CrossingResult shape.** ``accepted`` + ``reason`` carry the
      refusal; ``entry_id``/``entry_name``/``lap``/``lap_time`` carry
      the success. The skeleton's ``card``/``ShortLapFlagged`` fields
      arrive with E4.2/E4.3's dealing and min-lap work.
    - **stop/continue semantics.** ``stop()`` is a UI guard, not a
      state (spec §3, R-35): it locks plate entry while the ride stays
      RUNNING; ``start()`` on a RUNNING ride continues it, unlocking
      entry with ``actual_start`` unchanged ("Continue ride?").
    - **entry_id.** The in-memory roster assigns no numeric ids, and
      one plate namespace spans the ride (R-20), so the engine uses
      the entry's plate as ``entry_id`` until EPIC 5's Store assigns
      real ids.
    - **on_course.** Spec §6 names the counter without defining it; a
      loop timing's natural reading is an ACTIVE entry whose lap count
      is odd (out on the loop, not yet back).

    E4.2's own resolutions:

    - **Crossing card.** Every accepted crossing deals one card from
      the shoe (``_deal_card``; R-40); a ``ShoeEmpty`` mid-ride
      reshuffles (seed+1) and appends an audit ``Event`` named
      ``"shoe_reshuffle"`` with payload ``{"cycle": N}`` before the
      crossing's own ``record_crossing`` event. The per-deal card rides
      on ``CrossingResult.card``; the ``record_crossing`` event payload
      stays E4.1-pinned (no card field) -- deal auditability is the
      seeded shoe's replay guarantee (R-40), and E5's Store persists
      the card row with its own ``shoe_index``.
    - **Held cards.** A lap under ``config.min_lap_s`` is flagged
      short: the lap still records, but its card is dealt into a
      *held* state (spec §4, R-34) -- tracked in ``_held``, exposed by
      ``held_crossings()`` -- and never credited until ``confirm_held``
      releases it into the entry's hand or ``void_held`` discards it.
      ``confirm_held``/``void_held`` are gated only by the card being
      held, never by ride state: the review surface stays usable while
      RUNNING, and FINISHED's corrections flow routes through REOPENED
      for timing changes (undo), not card disposition.
    - **Undo.** ``undo_last()`` is a full compensating write (R-33):
      the last crossing's lap is removed, its card returns to the shoe
      front via ``shoe.restitute`` (so the next deal reproduces the
      same card), and an ``undo`` audit event lands. Whatever the
      card's disposition -- credited, currently held, or already
      voided -- undo reverses it completely. Legal only while RUNNING
      or REOPENED; zero crossings or any other state raises
      ``IllegalStateError``.
    - **unknown_plate spelling.** E4.2.4's machine-readable refusal
      reason is ``"unknown_plate"`` (underscore), superseding E4.1's
      provisional ``"unknown plate"``; the E4.1 pin test was updated
      to match. The sibling refusals (``"ride is not running"`` /
      ``"ride is stopped"``) keep their E4.1 spellings untouched.

    E4.3's own resolutions:

    - **Card cap X (R-13).** ``config.max_cards`` slices
      ``EntryResult.cards``/``hand`` at ``snapshot()`` to the first
      ``max_cards`` *credited* cards; laps past the cap still count
      for laps/time, and later cards still deal from the shoe (deal
      accounting unchanged) but never improve the hand. Held or
      voided cards never enter the credited sequence, so they never
      consume cap headroom; a ``deal_manual`` card appends to that
      same sequence, so a manual card past the cap is dealt but
      non-scoring. ``max_cards=None`` (the default) scores everything.
    - **Manual deal (spec §4).** ``deal_manual(plate, reason)``
      is the engine path E7.2.1's dialog wires to: one shoe deal
      credited directly into the entry's hand (never the held queue --
      a manual deal is a deliberate credit), ``mark_has_data``, and an
      audit ``Event`` carrying the reason. An unresolvable plate
      raises :class:`UnknownPlateError`; the ride must be RUNNING or
      REOPENED.
    - **Shoe close on Finish (spec §4, task-briefs E2.2.1).**
      ``finish()`` calls ``shoe.close()``; every later deal raises
      :class:`ShoeClosedError` -- including ``deal_manual`` once
      REOPENED reopens corrections, since the shoe stays closed.
      ``undo_last`` stays legal in REOPENED (E4.2 pin) and tolerates
      the closed shoe: the undone card retires with it instead of
      returning to the front.
    """

    def __init__(  # noqa: PLR0913, PLR0917 -- frozen S4 API (config, shoe, clock, roster)
        self,
        config: RideConfig,
        shoe: Shoe,
        clock: Callable[[], datetime],
        roster: Roster,
    ) -> None:
        """Build a DRAFT engine over config, shoe, clock, roster.

        Args:
            config: This ride's setup-time settings.
            shoe: The seeded shoe E4.3's dealing will draw from.
            clock: Wall-clock source; must return consistent
                naive-or-aware UTC datetimes.
            roster: This ride's entries/riders, duck-typed at runtime
                (never imported -- module docstring).
        """
        self._config = config
        self._shoe = shoe
        self._clock = clock
        self._roster = roster
        self._state = RideStatus.DRAFT
        self._actual_start: datetime | None = None
        self._stopped = False
        self._crossings: list[Crossing] = []
        self._dealt: dict[Crossing, Card] = {}
        self._held: dict[Crossing, Card] = {}
        self._hand: dict[str, list[Card]] = {}
        self._events: list[Event] = []

    @property
    def state(self) -> RideStatus:
        """Return this ride's lifecycle state (spec §3)."""
        return self._state

    @property
    def events(self) -> tuple[Event, ...]:
        """Return every ride event, oldest first, read-only.

        EPIC 5's Store persists these and rebuilds an engine by
        replaying them (module-skeletons.md S4).
        """
        return tuple(self._events)

    @property
    def on_course(self) -> int:
        """Return the count of ACTIVE entries currently out on the loop.

        An entry is on course when its lap count is odd (crossed the
        line once, not yet back) -- doc-silence record in the class
        docstring.
        """
        return sum(
            1
            for entry in self._roster.entries
            if entry.status.value == "active" and len(self._laps_for(entry.plate)) % 2 == 1
        )

    # E4.4.1 console read accessors. The console's ``EngineDataSource``
    # (rivercrossing.ui.presenters.data_source) builds its feed and
    # counters from these; each is a read-only projection over state
    # this engine already owns, never a new mutation path.

    @property
    def config(self) -> RideConfig:
        """Return this ride's frozen setup-time config (spec §2)."""
        return self._config

    @property
    def crossings(self) -> tuple[Crossing, ...]:
        """Return every recorded crossing, oldest first, read-only.

        Undo removes the reversed crossing, so this always reflects
        the current live state -- the console feed's source of truth.
        """
        return tuple(self._crossings)

    def card_for(self, crossing: Crossing) -> Card:
        """Return the shoe card dealt for *crossing* (R-40).

        Args:
            crossing: A crossing this engine recorded (see
                :attr:`crossings`).

        Returns:
            The card dealt for *crossing*, held or credited.

        Raises:
            KeyError: *crossing* was never dealt by this engine.
        """
        return self._dealt[crossing]

    @property
    def shoe_remaining(self) -> int:
        """Count of undealt cards left in the shoe's current cycle."""
        return self._shoe.remaining

    @property
    def shoe_total(self) -> int:
        """Total cards in the current shoe cycle (decks+jokers)."""
        return self._shoe.remaining + self._shoe.dealt

    def start(self, at: datetime | None = None) -> Event:
        """Start the ride, or continue a stopped one (spec §3, R-30).

        From DRAFT: the roster's start gate
        (:meth:`Roster.validate_for_start`) must be clear, then
        ``actual_start`` is *at* or ``clock()`` and the state becomes
        RUNNING. From RUNNING: continue -- unlock plate entry, keep
        ``actual_start`` unchanged ("Continue ride?"). From FINISHED
        or REOPENED: raise.

        Args:
            at: The start instant; omit to use the injected clock.

        Returns:
            The appended ``start``/``continue`` event.

        Raises:
            StartBlockedError: DRAFT and the roster is not ready.
            IllegalStateError: the state is FINISHED or REOPENED.
        """
        if self._state is RideStatus.RUNNING:
            started_at = self._require_actual_start()
            self._stopped = False
            return self._append(
                Event(action="continue", payload={"actual_start": started_at.isoformat()})
            )
        if self._state is not RideStatus.DRAFT:
            raise IllegalStateError(f"cannot start from {self._state}")
        violations = self._roster.validate_for_start()
        if violations:
            reasons = "; ".join(
                f"{violation.entry.plate}: {violation.reason}" for violation in violations
            )
            raise StartBlockedError(f"roster is not ready to start: {reasons}")
        started_at = at if at is not None else self._clock()
        self._actual_start = started_at
        self._state = RideStatus.RUNNING
        self._roster.status = RideStatus.RUNNING
        self._stopped = False
        return self._append(
            Event(action="start", payload={"actual_start": started_at.isoformat()})
        )

    def set_start_time(self, at: datetime) -> Event:
        """Back-date ``actual_start`` and recompute lap-1 (spec §3, 3d).

        The gun-missed correction: ``actual_start`` moves to *at* and,
        because lap times derive from it (spec §6), lap-1 lap times
        recompute automatically -- later laps, which derive from their
        own previous crossing, are untouched. Logged to the audit
        trail. RUNNING only.

        Raises:
            IllegalStateError: the ride is not RUNNING.
        """
        if self._state is not RideStatus.RUNNING:
            raise IllegalStateError(f"cannot set start time from {self._state}")
        previous = self._require_actual_start()
        self._actual_start = at
        return self._append(
            Event(
                action="set_start_time",
                payload={
                    "actual_start": at.isoformat(),
                    "previous_start": previous.isoformat(),
                },
            )
        )

    def record_crossing(self, plate: str, at: datetime | None = None) -> CrossingResult:
        """Record one completed lap for *plate* (spec §6, E4.2.1).

        Resolves *plate* to its entry via the roster (an entry's own
        plate, or a rider_pooled rider's plate, credits the entry --
        uncapped, R-16), appends one lap with a timestamp, marks the
        entry has_data, and deals one card from the shoe (R-40). A lap
        under ``config.min_lap_s`` is flagged short: the lap still
        records but its card is held, not credited (R-34). Refusals
        come back as ``accepted=False`` results, never raises: not
        RUNNING, stopped (E4.1.3), or an unknown plate
        (``reason="unknown_plate"``, E4.2.4 -- the error cue is E4.4's
        UI concern).

        Args:
            plate: The recorded plate.
            at: The crossing instant; omit to use the injected clock.

        Returns:
            The credited lap with its dealt card, or a refusal result.
        """
        if self._state is not RideStatus.RUNNING:
            return CrossingResult(accepted=False, plate=plate, reason="ride is not running")
        if self._stopped:
            return CrossingResult(accepted=False, plate=plate, reason="ride is stopped")
        entry = self._roster.resolve_plate(plate)
        if entry is None:
            return CrossingResult(accepted=False, plate=plate, reason="unknown_plate")
        crossed_at = at if at is not None else self._clock()
        start = self._require_actual_start()
        laps = self._laps_for(entry.plate)
        seq = len(laps) + 1
        card = self._deal_card()
        crossing = Crossing(entry_id=entry.plate, seq=seq, crossed_at=crossed_at)
        self._crossings.append(crossing)
        self._dealt[crossing] = card
        self._roster.mark_has_data(entry)
        previous = laps[-1].crossed_at if laps else start
        lap_time = (crossed_at - previous).total_seconds()
        flagged = lap_time < self._config.min_lap_s
        if flagged:
            self._held[crossing] = card
        else:
            self._hand.setdefault(entry.plate, []).append(card)
        self._append(
            Event(
                action="record_crossing",
                payload={
                    "plate": plate,
                    "entry_id": entry.plate,
                    "lap": seq,
                    "crossed_at": crossed_at.isoformat(),
                },
            )
        )
        return CrossingResult(
            accepted=True,
            plate=plate,
            entry_id=entry.plate,
            entry_name=entry.display_name,
            lap=seq,
            lap_time=lap_time,
            card=card,
            flagged=flagged,
        )

    def held_crossings(self) -> tuple[HeldCrossing, ...]:
        """Return every held crossing's crossing + card, oldest first.

        E4.4's review surface: short-lap crossings whose card awaits
        confirm (:meth:`confirm_held`) or void (:meth:`void_held`)
        (R-34). Held cards are dealt but never credited until released.
        """
        return tuple(
            HeldCrossing(crossing=crossing, card=card) for crossing, card in self._held.items()
        )

    def confirm_held(self, crossing: Crossing) -> Event:
        """Release *crossing*'s held card into its entry's hand (R-34).

        The operator's "that short lap was real" action: the card moves
        from the hold queue into the entry's credited hand and the
        standings hand improves. Audited. Gated only by the card being
        held -- ride state is irrelevant to card disposition.

        Args:
            crossing: A crossing currently in :meth:`held_crossings`.

        Returns:
            The appended ``confirm_held`` audit event.

        Raises:
            IllegalStateError: *crossing*'s card is not currently held.
        """
        card = self._held.pop(crossing, None)
        if card is None:
            raise IllegalStateError("crossing's card is not held")
        self._hand.setdefault(crossing.entry_id, []).append(card)
        return self._append(
            Event(
                action="confirm_held",
                payload={
                    "entry_id": crossing.entry_id,
                    "seq": crossing.seq,
                    "card": card.code(),
                },
            )
        )

    def void_held(self, crossing: Crossing) -> Event:
        """Discard *crossing*'s held card; never credited (R-34).

        The operator's "that short lap was a double-entry" action: the
        card is voided out of the system -- not returned to the shoe,
        not added to any hand. The lap itself stays recorded. Audited.
        Gated only by the card being held.

        Args:
            crossing: A crossing currently in :meth:`held_crossings`.

        Returns:
            The appended ``void_held`` audit event.

        Raises:
            IllegalStateError: *crossing*'s card is not currently held.
        """
        card = self._held.pop(crossing, None)
        if card is None:
            raise IllegalStateError("crossing's card is not held")
        return self._append(
            Event(
                action="void_held",
                payload={
                    "entry_id": crossing.entry_id,
                    "seq": crossing.seq,
                    "card": card.code(),
                },
            )
        )

    def undo_last(self) -> Event:
        """Undo the most recent crossing: a compensating write (R-33).

        Removes the last crossing's lap and timestamp, returns its card
        to the shoe front via ``shoe.restitute`` -- the next deal
        reproduces the same card -- and appends an ``undo`` audit
        event. Undo is a full reversal whatever the card's disposition:
        a credited card leaves the hand, a currently-held card drops
        out of the hold queue (never credited), and a voided card is
        un-voided back into the shoe. Legal while RUNNING or REOPENED.

        Returns:
            The appended ``undo`` audit event.

        Raises:
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                there are no crossings to undo.
        """
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot undo from {self._state}")
        if not self._crossings:
            raise IllegalStateError("no crossings to undo")
        last = self._crossings.pop()
        card = self._dealt.pop(last)
        self._held.pop(last, None)
        hand = self._hand.get(last.entry_id)
        if hand is not None and card in hand:
            hand.remove(card)
        # REOPENED after Finish: the shoe is closed (E4.3), so the
        # card retires with it -- there is no next deal to reproduce,
        # and E4.2 pins undo-in-REOPENED.
        with suppress(ShoeClosedError):
            self._shoe.restitute(card)
        return self._append(
            Event(
                action="undo",
                payload={
                    "entry_id": last.entry_id,
                    "seq": last.seq,
                    "crossed_at": last.crossed_at.isoformat(),
                    "card": card.code(),
                },
            )
        )

    def deal_manual(self, plate: str, reason: str) -> Event:
        """Deal one shoe card to *plate*'s entry by hand (spec §4).

        The operator's "manual add" correction from the entry detail:
        one card comes off the shoe and credits straight into the
        entry's hand -- never the held queue, whose short-lap path
        (R-34) a deliberate manual deal must not bypass -- the entry
        is marked has_data, and an audit ``Event`` carrying *reason*
        lands. The card joins the credited sequence, so it obeys
        ``config.max_cards`` exactly like a crossing's card: a manual
        card past the cap is dealt but non-scoring (R-13). A
        ``ShoeEmpty`` mid-deal reshuffles and audits it, exactly as
        ``record_crossing``'s own deal does (R-40).

        Args:
            plate: The recorded plate, resolved like a crossing's (an
                entry's own plate, or a rider_pooled rider's plate,
                credits the entry -- R-16).
            reason: Why the card was dealt by hand; carried in the
                audit payload.

        Returns:
            The appended ``deal_manual`` audit event.

        Raises:
            IllegalStateError: the ride is not RUNNING or REOPENED.
            UnknownPlateError: *plate* resolves to no entry.
            ShoeClosedError: the shoe is closed (the ride finished).
        """
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot deal manually from {self._state}")
        entry = self._roster.resolve_plate(plate)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {plate}")
        card = self._deal_card()
        self._hand.setdefault(entry.plate, []).append(card)
        self._roster.mark_has_data(entry)
        return self._append(
            Event(
                action="deal_manual",
                payload={
                    "plate": plate,
                    "entry_id": entry.plate,
                    "card": card.code(),
                    "reason": reason,
                },
            )
        )

    def stop(self) -> Event:
        """Lock plate entry; the ride stays RUNNING (spec §3, R-35).

        Stop is a UI guard, not a state: it refuses further
        ``record_crossing`` calls (as results), and ``start()``
        continues with ``actual_start`` unchanged. RUNNING only, and
        only once.

        Raises:
            IllegalStateError: the ride is not RUNNING, or already
                stopped.
        """
        if self._state is not RideStatus.RUNNING:
            raise IllegalStateError(f"cannot stop a {self._state} ride")
        if self._stopped:
            raise IllegalStateError("ride is already stopped")
        self._stopped = True
        return self._append(
            Event(action="stop", payload={"stopped_at": self._clock().isoformat()})
        )

    def finish(self) -> Event:
        """Finish the ride: RUNNING or REOPENED to FINISHED (spec §3).

        Closing the shoe here (spec §4, task-briefs E2.2.1)
        locks every later deal: ``deal_manual`` raises
        :class:`ShoeClosedError` once REOPENED reopens corrections,
        because the shoe stays closed. ``undo_last`` of an existing
        crossing stays legal in REOPENED, with its card retired by
        the closed shoe rather than returned to it.

        Raises:
            IllegalStateError: the ride is DRAFT or already FINISHED.
        """
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot finish from {self._state}")
        self._state = RideStatus.FINISHED
        self._roster.status = RideStatus.FINISHED
        self._stopped = False
        self._shoe.close()
        return self._append(
            Event(action="finish", payload={"finished_at": self._clock().isoformat()})
        )

    def reopen(self) -> Event:
        """Reopen a finished ride for corrections (spec §3, R-64).

        REOPENED is corrections-only, not RUNNING: the clock stays
        closed and live plate entry stays off; ``finish()`` re-locks.

        Raises:
            IllegalStateError: the ride is not FINISHED.
        """
        if self._state is not RideStatus.FINISHED:
            raise IllegalStateError(f"cannot reopen from {self._state}")
        self._state = RideStatus.REOPENED
        self._roster.status = RideStatus.REOPENED
        self._stopped = False
        return self._append(
            Event(action="reopen", payload={"reopened_at": self._clock().isoformat()})
        )

    def elapsed(self) -> float:
        """Return seconds since ``actual_start``, from the clock (R-30).

        Wall-clock only: ``now - actual_start`` with ``now`` from the
        injected clock, so there is no stored timer to lose.

        Raises:
            IllegalStateError: the ride has not started.
        """
        start = self._require_actual_start()
        return (self._clock() - start).total_seconds()

    def remaining(self) -> float:
        """Return seconds until ``planned_duration_s`` elapses (R-30).

        ``planned_duration_s - elapsed()``; may go negative once the
        planned duration passes (the UI clamps for display, this does
        not).

        Raises:
            IllegalStateError: the ride has not started.
        """
        return self._config.planned_duration_s - self.elapsed()

    def lap_times(self, entry_id: str) -> tuple[float, ...]:
        """Return each lap's derived time for *entry_id* (spec §6).

        Lap 1 is ``crossed_at - actual_start``; every later lap is
        ``crossed_at - previous crossing``. Derived, never stored, so
        :meth:`set_start_time` recomputes lap 1 automatically.
        """
        laps = self._laps_for(entry_id)
        if not laps:
            return ()
        previous: datetime = self._require_actual_start()
        times: list[float] = []
        for lap in laps:
            times.append((lap.crossed_at - previous).total_seconds())
            previous = lap.crossed_at
        return tuple(times)

    def snapshot(self) -> list[EntryResult]:
        """Return one standings result per ACTIVE entry, current state.

        Before crossings an entry is laps=0, cards=(), hand from
        ``hands.best_hand(())``; after crossings ``laps``,
        ``total_time`` and ``best_lap`` reflect the derived timing, and
        ``cards`` pools every credited card -- normal deals plus
        confirmed-held releases (R-16/R-34) -- with the best hand
        evaluated from them. ``config.max_cards`` (R-13) slices both
        ``cards`` and ``hand`` to the first ``max_cards`` credited
        cards: laps past the cap still count, and later cards still
        deal from the shoe but never improve the hand. Held
        (unconfirmed) and voided cards never reach the hand. DNF
        entries are excluded (mark_dnf is E7).
        """
        results: list[EntryResult] = []
        for entry in self._roster.entries:
            if entry.status.value != "active":  # spec §2's stored spelling
                continue
            laps = self._laps_for(entry.plate)
            times = self.lap_times(entry.plate)
            hand_cards = self._hand.get(entry.plate)
            cards = tuple(hand_cards) if hand_cards else ()
            if self._config.max_cards is not None:
                cards = cards[: self._config.max_cards]
            results.append(
                EntryResult(
                    entry_id=entry.plate,
                    plate=entry.plate,
                    name=entry.display_name,
                    kind=entry.type.value,
                    laps=len(laps),
                    total_time=self._total_time(laps),
                    best_lap=min(times) if times else 0.0,
                    cards=cards,
                    hand=best_hand(cards),
                    dnf=False,
                )
            )
        return results

    def _total_time(self, laps: tuple[Crossing, ...]) -> float:
        """Return the last crossing minus ``actual_start`` (spec §6)."""
        if not laps:
            return 0.0
        return (laps[-1].crossed_at - self._require_actual_start()).total_seconds()

    def _deal_card(self) -> Card:
        """Deal the next shoe card, reshuffling + auditing on ShoeEmpty.

        spec §4/R-40: an empty shoe reshuffles (seed+1) and the caller
        writes the reshuffle's own audit entry -- here that entry lands
        before the crossing's own ``record_crossing`` event.
        """
        try:
            card, _deal_index = self._shoe.deal()
        except ShoeEmpty:
            self._shoe.reshuffle()
            self._append(Event(action="shoe_reshuffle", payload={"cycle": self._shoe.cycle}))
            card, _deal_index = self._shoe.deal()
        return card

    def _laps_for(self, entry_id: str) -> tuple[Crossing, ...]:
        """Return *entry_id*'s crossings, oldest first."""
        return tuple(crossing for crossing in self._crossings if crossing.entry_id == entry_id)

    def _require_actual_start(self) -> datetime:
        """Return ``actual_start``, or raise if it was never set.

        Raises:
            IllegalStateError: ``actual_start`` was never set.
        """
        if self._actual_start is None:
            raise IllegalStateError("ride has not started")
        return self._actual_start

    def _append(self, event: Event) -> Event:
        """Append *event* to :attr:`events` and return it."""
        self._events.append(event)
        return event
