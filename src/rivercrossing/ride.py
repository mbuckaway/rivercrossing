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

from bisect import insort
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from rivercrossing.cards import Card, RestitutionError, ShoeClosedError, ShoeEmpty
from rivercrossing.hands import best_hand
from rivercrossing.standings import EntryResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date
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
    "UnknownEventActionError",
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


class UnknownEventActionError(RideEngineError):
    """RideEngine.apply() met an event action it does not dispatch.

    Raised while replaying an event whose ``action`` names no ride
    mutation -- a corrupted or foreign ``audit`` row, never a valid
    event (task-briefs E5.1.2's negative case).
    """


def _payload_dt(event: Event, key: str) -> datetime:
    """Parse one ISO-8601 payload value back into a datetime.

    Args:
        event: The event being replayed.
        key: The payload key holding the ISO-8601 timestamp.

    Returns:
        The parsed naive-or-aware datetime.
    """
    return datetime.fromisoformat(str(event.payload[key]))


def _require_reason(reason: str) -> None:
    """Refuse an empty or blank correction reason (E7.1.1, R-33).

    Every audited correction command requires a non-empty *reason* so
    the audit trail always records why a fix happened; an empty string
    (or one that is only whitespace) is refused before any state or
    identity validation runs.

    Raises:
        ValueError: *reason* is empty or whitespace-only.
    """
    if not reason.strip():
        msg = "reason must not be empty"
        raise ValueError(msg)


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
      the caller while its ``Event`` lands in :attr:`events`. Each
      append also hands the event to :attr:`on_event` when a sink is
      attached (E9.1.3) -- the app's Store.append seam.
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
      :class:`ShoeClosedError` while the ride stays FINISHED.
      ``reopen()`` calls ``shoe.reopen()`` (spec §15), so REOPENED
      corrections deal new cards -- ``deal_manual`` and
      ``add_crossing_at`` both deal in REOPENED. ``undo_last`` stays
      legal in REOPENED (E4.2 pin): with the shoe re-opened its
      restitution returns the undone card to the front, so the next
      correction deal reproduces it.

    E5.1.2's own resolutions (event replay, task-briefs E5.1.2):

    - **Replay seam.** :meth:`apply` re-applies one previously-recorded
      :class:`Event` by dispatching on ``action`` to the matching
      mutation; :class:`~rivercrossing.store.Store.load_engine` calls
      it for every persisted event to rebuild this engine. Every
      payload field each branch needs already exists in the E4 events
      -- ``start``/``continue`` carry ``actual_start``,
      ``set_start_time`` carries the new ``actual_start``,
      ``record_crossing`` carries ``plate``+``crossed_at``,
      ``confirm_held``/``void_held`` carry ``entry_id``+``seq`` (the
      held crossing's identity), ``deal_manual`` carries
      ``plate``+``reason`` -- so no payload extension was needed
      (task-briefs E5.1.2's "where a payload is insufficient" did not
      trigger); the ``card`` fields are audit-only, since the seeded
      shoe reproduces every deal (spec §4, R-40).
    - **Clock-stamped events.** ``stop``/``finish``/``reopen`` re-stamp
      their payload timestamp from the engine's clock when replayed,
      so their audit bytes differ from the live original by design;
      replay equivalence compares event count/actions, not those three
      payload fields (recorded in the property's comparison contract).
    - **shoe_reshuffle is a no-op on replay.** The event is the audit
      record of a reshuffle the deal loop already performed; re-applying
      it would double-reshuffle the fresh shoe. The next deal
      reproduces the reshuffle when the shoe empties.

    E7.1.1's own resolutions (audited corrections, task-briefs E7.1):

    - **Correction gate.** Each of the six correction commands
      (``edit_crossing``, ``void_crossing``, ``add_crossing_at``,
      ``reassign_crossing``, ``mark_dnf``, ``void_card``) requires a
      non-empty *reason* (``_require_reason`` raises ``ValueError`` on
      an empty or whitespace-only one), writes exactly one
      :class:`Event` via ``_append``, and is legal only while RUNNING
      or REOPENED -- DRAFT and FINISHED raise
      :class:`IllegalStateError`. Unknown plates raise
      :class:`UnknownPlateError`: corrections fail loudly, unlike
      ``record_crossing``'s console refusal result.
    - **Void is a compensating write, never a delete.**
      ``void_crossing`` moves the crossing out of the live lap
      sequence into a private voided record (crossing + card) and
      voids its card -- the schema's ``crossing.voided`` and
      ``card.state`` columns in memory. It never restitutes the card:
      restitution stays ``undo_last``'s job.
    - **Renumber on removal.** ``void_crossing`` and
      ``reassign_crossing`` both remove one of an entry's laps; the
      entry's later live crossings renumber (seq - 1) so the per-entry
      seq stays contiguous 1..N, which keeps ``record_crossing``'s
      next-seq assignment collision-free.
    - **Card travels on reassign (ruling C).** ``reassign_crossing``
      moves the crossing *and its card*: a credited card leaves the
      source hand and joins the destination's, a held card stays held
      under the destination entry, a voided card stays voided.
    - **reassign seq is the ride-wide ordinal.** The command's *seq*
      parameter names the crossing's 1-based position in
      :attr:`crossings` (record order), not its per-entry lap number:
      unique across the ride, and replay reproduces the same list
      position exactly (method docstring).
    - **add_crossing_at credits directly, never holds.** A deliberate
      missed-crossing correction deals the next shoe card (like
      ``record_crossing``) but routes it straight into the hand,
      mirroring ``deal_manual`` -- R-34's hold surface is for live
      console entry, not corrections.
    - **DNF is a status, not a filter.** ``mark_dnf`` sets the entry's
      roster status (spec §2 ``entry.status``); ``snapshot()`` lists
      DNF entries with ``dnf=True`` and ``standings.rank`` owns
      placement/leaderboard exclusion -- never reimplemented here.
    - **undo reason label.** The ``undo`` event payload now carries
      ``reason="Undo last crossing"`` (fixed label, E7.1.1).
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
        # Per-entry lap index (review fix): entry_id -> its live
        # crossings sorted by crossed_at, holding the SAME Crossing
        # objects _crossings holds. _laps_for reads this instead of
        # scanning the ride-wide list, so Store.load_engine replay and
        # snapshot() stay linear; _insert_crossing/_remove_crossing/
        # _replace_crossing keep the two structures in lockstep.
        self._laps: dict[str, list[Crossing]] = {}
        self._dealt: dict[Crossing, Card] = {}
        self._held: dict[Crossing, Card] = {}
        self._hand: dict[str, list[Card]] = {}
        self._events: list[Event] = []
        # E9.1.3: the persistence sink. The app attaches
        # Store.append(ride_id, event) here AFTER load_engine's replay,
        # so every live mutation writes one audit row and the replayed
        # tail is never re-persisted (see _append).
        self.on_event: Callable[[Event], None] | None = None
        # E7.1.1: voided crossings (crossing + card, in void order) and
        # voided cards. Compensating writes, never deletes -- the live
        # lap sequence drops the crossing, the record keeps it.
        self._voided: list[tuple[Crossing, Card]] = []
        self._voided_cards: set[Card] = set()

    @property
    def state(self) -> RideStatus:
        """Return this ride's lifecycle state (spec §3)."""
        return self._state

    @property
    def clock(self) -> Callable[[], datetime]:
        """Return this ride's wall-clock source.

        Exposed read-only so the app's console-rebuild seam
        (``_switch_console_to_ride``) can carry an injected clock
        across a store reload -- the R-74 race injects a scripted
        clock at launch, and the CSV-import rebuild must not drop it
        or every typed lap would land milliseconds apart and flag.
        """
        return self._clock

    @property
    def stopped(self) -> bool:
        """Return whether plate entry is locked by Stop (R-35's guard).

        Stop is a UI guard, not a state: the ride stays RUNNING while
        ``_stopped`` is true, and :meth:`start` clears it on continue.
        Exposed read-only so the E7.2.1 menu binder can feed
        ``commands.RideState.ride_stopped`` (Start Ride's "or stopped
        RUNNING" clause) from the live engine.
        """
        return self._stopped

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
        self._insert_crossing(crossing)
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
        un-voided back into the shoe. When the card cannot return to
        the front -- the shoe is closed (REOPENED after Finish) or a
        later ``deal_manual`` put a different card there -- the undone
        card retires with the shoe instead, deterministically (E5.1.2
        replay reproduces the same shoe point). Legal while RUNNING or
        REOPENED.

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
        last = self._crossings[-1]
        self._remove_crossing(last)
        card = self._dealt.pop(last)
        self._held.pop(last, None)
        hand = self._hand.get(last.entry_id)
        if hand is not None and card in hand:
            hand.remove(card)
        self._voided_cards.discard(card)
        # REOPENED after Finish: the shoe is re-opened (spec §15), so
        # this restitution succeeds and returns the card to the front --
        # the next correction deal (deal_manual, add_crossing_at)
        # reproduces it, deterministic continuation (R-40). The retire
        # path still applies when a later manual deal put a different
        # card at the shoe front: that card is a deliberate credit and
        # must not be disturbed, so the undone crossing's card cannot
        # return to the front and retires instead. Both paths are
        # deterministic -- replay reproduces the identical shoe point
        # (E5.1.2).
        with suppress(ShoeClosedError, RestitutionError):
            self._shoe.restitute(card)
        return self._append(
            Event(
                action="undo",
                payload={
                    "entry_id": last.entry_id,
                    "seq": last.seq,
                    "crossed_at": last.crossed_at.isoformat(),
                    "card": card.code(),
                    "reason": "Undo last crossing",
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
        ``record_crossing``'s own deal does (R-40). REOPENED after
        Finish deals too: ``reopen()`` re-opens the shoe (spec §15),
        so the manual card comes off the same continuing deal order.
        The shoe is open in RUNNING and REOPENED; ``finish()`` closes
        it only while the ride stays FINISHED, which the state gate
        below refuses first.

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

    # --------------------------------- E7.1.1 audited corrections

    def edit_crossing(  # noqa: PLR0913, PLR0917 -- (entry, seq, crossed_at, reason): the correction's four fixed fields
        self, entry_id: str, seq: int, crossed_at: datetime, reason: str
    ) -> Event:
        """Re-time one crossing without re-dealing its card (E7.1.1).

        The operator's "wrong time on the clock" correction: the named
        crossing's ``crossed_at`` moves to *crossed_at* and, because
        lap times are derived (spec §6), every lap that depends on it
        recomputes automatically. The crossing's card is untouched --
        no new deal, no disposition change: a held card stays held, a
        credited card stays credited. RUNNING or REOPENED only.

        Args:
            entry_id: The entry whose crossing to edit.
            seq: The crossing's 1-based lap number within that entry.
            crossed_at: The corrected crossing instant.
            reason: Why the time was wrong; carried in the audit
                payload.

        Returns:
            The appended ``edit_crossing`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                no crossing matches *entry_id*/*seq*.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot edit crossing from {self._state}")
        crossing = self._require_crossing(entry_id, seq)
        replacement = Crossing(entry_id=crossing.entry_id, seq=crossing.seq, crossed_at=crossed_at)
        self._replace_crossing(crossing, replacement)
        return self._append(
            Event(
                action="edit_crossing",
                payload={
                    "entry_id": crossing.entry_id,
                    "seq": crossing.seq,
                    "previous_crossed_at": crossing.crossed_at.isoformat(),
                    "crossed_at": crossed_at.isoformat(),
                    "reason": reason,
                },
            )
        )

    def void_crossing(self, entry_id: str, seq: int, reason: str) -> Event:
        """Void one crossing and its card; later laps renumber (E7.1.1).

        The operator's "that lap never happened" correction: the
        crossing leaves the live lap sequence -- the entry's later laps
        renumber to close the gap -- and its card is voided
        (compensating write, never a delete: the voided crossing and
        card stay in the engine's voided record and the audit event).
        The card is *not* returned to the shoe: restitution is
        ``undo_last``'s job, never this command's. RUNNING or REOPENED
        only.

        Args:
            entry_id: The entry whose crossing to void.
            seq: The crossing's 1-based lap number within that entry.
            reason: Why the crossing was voided; carried in the audit
                payload.

        Returns:
            The appended ``void_crossing`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                no crossing matches *entry_id*/*seq*.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot void crossing from {self._state}")
        crossing = self._require_crossing(entry_id, seq)
        card = self._dealt.pop(crossing)
        self._held.pop(crossing, None)
        hand = self._hand.get(crossing.entry_id)
        if hand is not None and card in hand:
            hand.remove(card)
        self._voided_cards.add(card)
        self._remove_crossing(crossing)
        self._voided.append((crossing, card))
        self._renumber_later(crossing.entry_id, crossing.seq)
        return self._append(
            Event(
                action="void_crossing",
                payload={
                    "entry_id": crossing.entry_id,
                    "seq": crossing.seq,
                    "reason": reason,
                },
            )
        )

    def add_crossing_at(self, plate: str, crossed_at: datetime, reason: str) -> Event:
        """Record a missed crossing at an explicit past time (E7.1.1).

        The operator's "rider crossed and the entry field missed it"
        correction: records one crossing at *crossed_at* and deals the
        next shoe card, exactly like ``record_crossing``'s deal. The
        card credits straight into the entry's hand, never the held
        queue -- a deliberate correction is not live entry, mirroring
        ``deal_manual``'s direct-credit rule (R-34's hold surface is
        for the live console path). RUNNING or REOPENED only; REOPENED
        after Finish passes the state gate and deals too, because
        ``reopen()`` re-opens the shoe (spec §15) -- the missed
        crossing's card comes off the same continuing deal order.

        Args:
            plate: The recorded plate, resolved like a crossing's
                (R-16).
            crossed_at: The explicit past crossing instant.
            reason: Why the crossing was missed; carried in the audit
                payload.

        Returns:
            The appended ``add_crossing_at`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED.
            UnknownPlateError: *plate* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot add crossing from {self._state}")
        entry = self._roster.resolve_plate(plate)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {plate}")
        laps = self._laps_for(entry.plate)
        seq = len(laps) + 1
        card = self._deal_card()
        crossing = Crossing(entry_id=entry.plate, seq=seq, crossed_at=crossed_at)
        self._insert_crossing(crossing)
        self._dealt[crossing] = card
        self._hand.setdefault(entry.plate, []).append(card)
        self._roster.mark_has_data(entry)
        return self._append(
            Event(
                action="add_crossing_at",
                payload={
                    "plate": plate,
                    "entry_id": entry.plate,
                    "crossed_at": crossed_at.isoformat(),
                    "reason": reason,
                },
            )
        )

    def reassign_crossing(self, seq: int, new_plate: str, reason: str) -> Event:
        """Reattribute one crossing -- and its card -- to another entry.

        The operator's "wrong plate on the line" correction: the
        crossing named by *seq* -- its 1-based position in
        :attr:`crossings`, the ride-wide record order -- moves to the
        entry *new_plate* resolves to, taking its card along (ruling C:
        the card travels, never re-deals). The source entry's later
        laps renumber to close the gap; the destination appends the
        crossing as its next lap. RUNNING or REOPENED only.

        .. note::
           *seq* is the ride-wide ordinal, not the per-entry
           ``Crossing`` lap number (class docstring's E7.1.1
           resolution): the corrections dialog names one concrete
           crossing, and the payload must replay to the same crossing.
           A per-entry lap number would be ambiguous the moment two
           entries share a lap count; the record-order ordinal is
           unique and replay reproduces the same list position.

        Args:
            seq: The 1-based position of the crossing in
                :attr:`crossings`.
            new_plate: The destination plate, resolved like a
                crossing's (R-16).
            reason: Why the plate was wrong; carried in the audit
                payload.

        Returns:
            The appended ``reassign`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                *seq* names no crossing.
            UnknownPlateError: *new_plate* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot reassign crossing from {self._state}")
        if not 1 <= seq <= len(self._crossings):
            raise IllegalStateError(f"no crossing at ordinal {seq}")
        crossing = self._crossings[seq - 1]
        old_entry_id = crossing.entry_id
        old_seq = crossing.seq
        entry = self._roster.resolve_plate(new_plate)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {new_plate}")
        card = self._dealt[crossing]
        held = self._held.pop(crossing, None)
        hand = self._hand.get(old_entry_id)
        if hand is not None and card in hand:
            hand.remove(card)
        self._dealt.pop(crossing)
        self._remove_crossing(crossing)
        self._renumber_later(old_entry_id, old_seq)
        new_seq = len(self._laps_for(entry.plate)) + 1
        replacement = Crossing(entry_id=entry.plate, seq=new_seq, crossed_at=crossing.crossed_at)
        self._insert_crossing(replacement)
        self._dealt[replacement] = card
        if held is not None:
            self._held[replacement] = held
        elif card not in self._voided_cards:
            self._hand.setdefault(entry.plate, []).append(card)
        return self._append(
            Event(
                action="reassign",
                payload={
                    "seq": seq,
                    "old_entry_id": old_entry_id,
                    "new_entry_id": entry.plate,
                    "new_plate": new_plate,
                    "reason": reason,
                },
            )
        )

    def mark_dnf(self, entry_id: str, reason: str) -> Event:
        """Mark one entry DNF; laps and cards stay (E7.1.1, spec §6).

        The operator's "rider did not finish" correction: the entry's
        status becomes DNF (spec §2 ``entry.status``), keeping every
        recorded lap and card. :meth:`snapshot` reports the entry with
        ``dnf=True`` and ``standings.rank`` places DNF entries after
        every ACTIVE entry -- the engine never reimplements that
        ranking. Reversible semantics (un-DNF) are the dialog's
        concern, not the engine's: this command only sets the state.
        RUNNING or REOPENED only.

        Args:
            entry_id: The entry to mark (a plate, resolved like a
                crossing's -- R-16).
            reason: Why the entry is DNF; carried in the audit payload.

        Returns:
            The appended ``dnf`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED.
            UnknownPlateError: *entry_id* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot mark DNF from {self._state}")
        entry = self._roster.resolve_plate(entry_id)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {entry_id}")
        # Duck-typed status write: never import roster at runtime
        # (module docstring), and the member must be a real StrEnum --
        # the store's save_roster reads ``entry.status.value``.
        entry.status = type(entry.status)("dnf")
        return self._append(
            Event(action="dnf", payload={"entry_id": entry.plate, "reason": reason})
        )

    def void_card(self, entry_id: str, card: Card, reason: str) -> Event:
        """Void one dealt card; its crossing and lap stay (E7.1.1).

        The operator's "wrong card off the line" correction: *card*
        leaves the entry's credited hand and its state becomes voided
        (spec §2 ``card.state``), while the crossing that dealt it --
        and therefore the lap -- stays recorded. A held card is
        refused: the short-lap hold is the review surface's domain
        (``confirm_held``/``void_held``), never this command. RUNNING
        or REOPENED only.

        Args:
            entry_id: The entry whose card to void.
            card: The dealt card to void, as a :class:`Card`.
            reason: Why the card was voided; carried in the audit
                payload.

        Returns:
            The appended ``void_card`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            UnknownPlateError: *entry_id* resolves to no entry.
            IllegalStateError: the ride is not RUNNING or REOPENED,
                *card* is currently held, or *card* is not credited to
                *entry_id*.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot void card from {self._state}")
        entry = self._roster.resolve_plate(entry_id)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {entry_id}")
        for crossing, held_card in self._held.items():
            if crossing.entry_id == entry.plate and held_card == card:
                msg = "card is held; confirm or void it through the review panel"
                raise IllegalStateError(msg)
        hand = self._hand.get(entry.plate)
        if hand is None or card not in hand:
            raise IllegalStateError(f"no dealt card {card.code()} credited to {entry.plate}")
        hand.remove(card)
        self._voided_cards.add(card)
        return self._append(
            Event(
                action="void_card",
                payload={"entry_id": entry.plate, "card": card.code(), "reason": reason},
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
        locks every later deal while the ride stays FINISHED; the
        ``ShoeClosedError`` contract holds for a ride that is still
        FINISHED, and only ``reopen()`` opens the shoe again.
        ``undo_last`` of an existing crossing stays legal in REOPENED,
        where the re-opened shoe's restitution returns the undone card
        to the front.

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
        Re-opening also re-opens the shoe (``Shoe.reopen``, spec §15):
        the closed state from Finish is not sticky, so the corrections
        commands (``deal_manual``, ``add_crossing_at``) deal new cards
        from the continuing deal order.

        Raises:
            IllegalStateError: the ride is not FINISHED.
        """
        if self._state is not RideStatus.FINISHED:
            raise IllegalStateError(f"cannot reopen from {self._state}")
        self._state = RideStatus.REOPENED
        self._roster.status = RideStatus.REOPENED
        self._stopped = False
        self._shoe.reopen()
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
        """Return one standings result per entry, current state.

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
        entries (``mark_dnf``, E7.1.1) stay listed with ``dnf=True``;
        ``standings.rank`` places them after every ACTIVE entry and the
        leaderboards exclude them -- never reimplemented here.
        """
        results: list[EntryResult] = []
        for entry in self._roster.entries:
            dnf = entry.status.value == "dnf"  # spec §2's stored spelling
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
                    dnf=dnf,
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
        """Return *entry_id*'s live crossings, earliest crossing first.

        A plain read of the per-entry index: O(1), never a scan of the
        ride-wide record list. The index is kept time-sorted by the
        three private mutators below, so the derived lap sequence --
        and therefore every lap time -- follows the clock, not the
        append log: corrections can insert or move a crossing to an
        explicit past time (``add_crossing_at``/``edit_crossing``/
        ``reassign_crossing``). Two crossings at the same instant keep
        record order (``insort`` inserts after equal keys).
        """
        return tuple(self._laps.get(entry_id, ()))

    def _insert_crossing(self, crossing: Crossing) -> None:
        """Append *crossing* to the ride record and index it per entry.

        The record list keeps ride-wide append order; the per-entry
        index keeps ``crossed_at`` order so ``_laps_for`` is a dict
        read. ``insort`` (right) inserts after equal keys, matching
        the record-order tie-break the old stable sort gave.
        """
        self._crossings.append(crossing)
        laps = self._laps.setdefault(crossing.entry_id, [])
        insort(laps, crossing, key=lambda c: c.crossed_at)

    def _remove_crossing(self, crossing: Crossing) -> None:
        """Drop *crossing* from the ride record and its entry's index.

        The index key disappears with its last crossing, so entries
        with no live laps keep reporting an empty tuple.
        """
        self._crossings.remove(crossing)
        laps = self._laps[crossing.entry_id]
        laps.remove(crossing)
        if not laps:
            del self._laps[crossing.entry_id]

    def _require_crossing(self, entry_id: str, seq: int) -> Crossing:
        """Return the live crossing *entry_id*/*seq* names, or raise.

        E7.1.1's identity lookup for corrections: the live lap
        sequence (never a voided record), matched the same way
        :meth:`_crossing_from` matches a replayed event.

        Raises:
            IllegalStateError: no live crossing matches the pair.
        """
        for crossing in self._crossings:
            if crossing.entry_id == entry_id and crossing.seq == seq:
                return crossing
        raise IllegalStateError(f"no crossing with entry_id {entry_id} seq {seq}")

    def _replace_crossing(self, old: Crossing, new: Crossing) -> None:
        """Swap *old* for *new* in place, carrying its card and hold.

        Used by the corrections that re-key a crossing's identity
        (``edit_crossing`` re-times it, ``void_crossing``/
        ``reassign_crossing`` renumber later laps): the crossing's
        dealt card -- and its hold, when held -- travel to the
        replacement so the deal accounting never drifts. The per-entry
        index follows: a same-entry replacement takes *old*'s slot
        (stable re-sort only when the time changed, so tied instants
        keep record order); a cross-entry move re-indexes under the
        new entry.
        """
        index = self._crossings.index(old)
        self._crossings[index] = new
        card = self._dealt.pop(old)
        self._dealt[new] = card
        held = self._held.pop(old, None)
        if held is not None:
            self._held[new] = held
        laps = self._laps[old.entry_id]
        if old.entry_id == new.entry_id:
            position = laps.index(old)
            laps[position] = new
            if new.crossed_at != old.crossed_at:
                laps.sort(key=lambda c: c.crossed_at)
        else:
            # logic-coverage-exempt: T-3 -- defensive for a cross-entry
            # caller; today edit_crossing/_renumber_later are always
            # same-entry, so this arm is unreachable.
            laps.remove(old)
            if not laps:
                del self._laps[old.entry_id]
            self._insert_crossing(new)

    def _renumber_later(self, entry_id: str, after_seq: int) -> None:
        """Decrement every later live crossing's seq by one (E7.1.2).

        ``void_crossing`` and ``reassign_crossing`` both remove one of
        an entry's laps; the remaining later laps close up so the
        entry's seq stays contiguous 1..N, which is what keeps
        ``record_crossing``'s next-seq assignment collision-free.
        """
        for crossing in list(self._crossings):
            if crossing.entry_id == entry_id and crossing.seq > after_seq:
                self._replace_crossing(
                    crossing, Crossing(entry_id, crossing.seq - 1, crossing.crossed_at)
                )

    def _require_actual_start(self) -> datetime:
        """Return ``actual_start``, or raise if it was never set.

        Raises:
            IllegalStateError: ``actual_start`` was never set.
        """
        if self._actual_start is None:
            raise IllegalStateError("ride has not started")
        return self._actual_start

    def _append(self, event: Event) -> Event:
        """Append *event* to :attr:`events` and return it.

        Also hands *event* to :attr:`on_event` when a sink is attached
        (E9.1.3) -- the one seam every engine mutation persists
        through, whatever command produced it.
        """
        self._events.append(event)
        if self.on_event is not None:
            self.on_event(event)
        return event

    # ------------------------------------- E5.1.2 replay seam: apply

    # The replay dispatch is inherently one branch per action (17
    # mutations + the unknown-action guard); the cyclomatic count is
    # the event vocabulary's size, not a refactorable control-flow
    # tangle.
    def apply(self, event: Event) -> None:  # noqa: C901, PLR0912
        """Replay one previously-recorded event onto this engine.

        The store's replay seam: :class:`~rivercrossing.store.
        Store.load_engine` builds a fresh DRAFT engine and calls this
        for every persisted event, oldest first, to reach the exact
        live state. Dispatch calls the matching mutation with the
        payload's own values, so the re-appended event equals the
        original for every action that takes an explicit timestamp
        (``start``/``set_start_time``/``record_crossing`` and the
        identity-based holds); ``stop``/``finish``/``reopen`` re-stamp
        their payload timestamp from the engine's clock, so their
        audit bytes differ by design on replay (class docstring's
        E5.1.2 resolutions). The shoe's open/closed state is part of
        the reproduced state: ``finish`` closes the fresh shoe and a
        replayed ``reopen`` opens it again, exactly as live.

        Args:
            event: The event to re-apply, exactly as persisted.

        Raises:
            UnknownEventActionError: *event.action* is not a known
                ride mutation.
            RideEngineError: ``confirm_held``/``void_held`` name a
                crossing this engine never recorded (an inconsistent
                event stream).
        """
        action = event.action
        if action == "start":
            self.start(at=_payload_dt(event, "actual_start"))
        elif action == "continue":
            self.start()
        elif action == "set_start_time":
            self.set_start_time(_payload_dt(event, "actual_start"))
        elif action == "record_crossing":
            self.record_crossing(str(event.payload["plate"]), at=_payload_dt(event, "crossed_at"))
        elif action == "confirm_held":
            self.confirm_held(self._crossing_from(event))
        elif action == "void_held":
            self.void_held(self._crossing_from(event))
        elif action == "undo":
            self.undo_last()
        elif action == "deal_manual":
            self.deal_manual(str(event.payload["plate"]), reason=str(event.payload["reason"]))
        elif action == "edit_crossing":
            self.edit_crossing(
                str(event.payload["entry_id"]),
                int(str(event.payload["seq"])),
                _payload_dt(event, "crossed_at"),
                reason=str(event.payload["reason"]),
            )
        elif action == "void_crossing":
            self.void_crossing(
                str(event.payload["entry_id"]),
                int(str(event.payload["seq"])),
                reason=str(event.payload["reason"]),
            )
        elif action == "add_crossing_at":
            self.add_crossing_at(
                str(event.payload["plate"]),
                _payload_dt(event, "crossed_at"),
                reason=str(event.payload["reason"]),
            )
        elif action == "reassign":
            self.reassign_crossing(
                int(str(event.payload["seq"])),
                str(event.payload["new_plate"]),
                reason=str(event.payload["reason"]),
            )
        elif action == "dnf":
            self.mark_dnf(str(event.payload["entry_id"]), reason=str(event.payload["reason"]))
        elif action == "void_card":
            self.void_card(
                str(event.payload["entry_id"]),
                Card.parse(str(event.payload["card"])),
                reason=str(event.payload["reason"]),
            )
        elif action == "stop":
            self.stop()
        elif action == "finish":
            self.finish()
        elif action == "reopen":
            self.reopen()
        elif action == "shoe_reshuffle":
            # Deliberate no-op (class docstring, E5.1.2): the deal loop
            # reproduces the reshuffle when the fresh shoe empties.
            pass
        else:
            raise UnknownEventActionError(f"cannot apply unknown event action: {action}")

    def _crossing_from(self, event: Event) -> Crossing:
        """Return the recorded crossing an event's entry/seq names.

        ``confirm_held``/``void_held`` events identify their held
        crossing by ``entry_id`` + ``seq`` (the ``crossing`` table's
        own uniqueness, spec §2); the engine locates that crossing in
        its own current state rather than trusting the payload's card.

        Raises:
            RideEngineError: no recorded crossing matches the event's
                entry/seq -- an inconsistent event stream.
        """
        entry_id = str(event.payload["entry_id"])
        seq = int(str(event.payload["seq"]))
        for crossing in self._crossings:
            if crossing.entry_id == entry_id and crossing.seq == seq:
                return crossing
        raise RideEngineError(f"no crossing with entry_id {entry_id} seq {seq} for {event.action}")
