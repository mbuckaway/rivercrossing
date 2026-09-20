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

import math
from bisect import insort
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import combinations
from typing import TYPE_CHECKING

from rivercrossing.cards import (
    Card,
    RestitutionError,
    ShoeClosedError,
    ShoeEmpty,
    high_card_draw,
)
from rivercrossing.hands import best_hand, compare
from rivercrossing.standings import EntryResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date
    from pathlib import Path

    from rivercrossing.cards import Shoe
    from rivercrossing.roster import Entry, EntryMode, PlateModel, Rider, Roster

__all__ = [
    "DEFAULT_DECK_COUNT",
    "DEFAULT_JOKERS_MODE",
    "DEFAULT_JOKERS_PER_DECK",
    "DEFAULT_TIEBREAK_ORDER",
    "FAR_TOO_MANY",
    "JOKERS_MODES",
    "JOKERS_MODE_PER_DECK",
    "JOKERS_MODE_TOTAL",
    "MAX_JOKERS_PER_DECK",
    "NOT_ENOUGH",
    "OK",
    "REPLAY_ACTIONS",
    "TIEBREAK_HIGH_CARD",
    "TIEBREAK_LAPS",
    "TIEBREAK_TOTAL_TIME",
    "CardCheck",
    "Crossing",
    "CrossingResult",
    "Event",
    "HeldCrossing",
    "IllegalStateError",
    "PendingMiss",
    "RideConfig",
    "RideConfigError",
    "RideEngine",
    "RideEngineError",
    "RideStatus",
    "StartBlockedError",
    "UnknownEventActionError",
    "UnknownPlateError",
    "check_card_sufficiency",
    "estimate_cards_needed",
    "setup_minimum_violations",
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

# Phase 3's stored default leads with the venue's high-card draw: the
# finish itself draws one card per tied entry from a fresh deck (R-14,
# ``_record_tiebreak_draws``), so standings orders a fully drawn tie
# group by ``cards.draw_key`` and only a group the draw still cannot
# separate -- no card held, or more tied entries than the 52-card deck
# holds -- is flagged "draw required" (R-43). Any criterion after the
# draw is unreachable either way. The order is set (and reordered with
# tiebreak_list's own Up/Down buttons) in ride_setup_dlg, R-14; a live
# (unfinished) board auto-ranks by laps/time instead
# (standings.LIVE_TIEBREAK_ORDER).
DEFAULT_TIEBREAK_ORDER: tuple[str, str, str] = (
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
)

# The tie-break draw's own seed salt (R-14, R-40). The venue's draw
# runs on a *fresh* deck (``cards.high_card_draw``), so its seed is
# derived by XORing this constant onto the shoe's stored seed rather
# than reusing it: the draw replays from the one number a ride stores,
# yet no draw order can ever coincide with the shoe's own Fisher-Yates
# sequence and the shoe's deals are untouched by the draw. The value
# stays well inside the 63-bit range the store draws seeds from
# (``secrets.randbits(63)``, spec §4).
_TIEBREAK_DRAW_SEED_XOR = 0x5F3A_6D1C_9E2B_0478

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
# artifact, not a competing default. Phase 1 (2026-09-13) re-binds
# the *jokers* half of that quoted "8 decks x 2 jokers = 432" default
# to 1, so the shipped default shoe is 8 x 53 = 424 (the spec's own
# text is updated in a later phase, not rewritten here).
DEFAULT_DECK_COUNT = 8

# setup.xrc's jokers_spin (a wxSpinCtrl over 0..10, opening on 1) is the
# dialog half of this default; Phase 1 (2026-09-13) re-bound it from the
# retired jokers_2_radio's 2 to 1, and Phase 5 kept the same 1 when the
# jokers_choice dropdown became the spinner. Recorded here so
# RideConfig's own default never drifts from the authored control.
DEFAULT_JOKERS_PER_DECK = 1

# The two jokers_mode spellings (the ``ride.jokers_mode`` column,
# spec §2 Phase 5). Per-deck re-deals jokers_per_deck jokers every
# cycle; total spends that many jokers once, across the whole ride
# (Shoe's own jokers_total mode).
JOKERS_MODE_PER_DECK = "per_deck"
JOKERS_MODE_TOTAL = "total"
JOKERS_MODES: tuple[str, str] = (JOKERS_MODE_PER_DECK, JOKERS_MODE_TOTAL)

# setup.xrc's jokers_total_radio is the checked default in the jokers
# radio pair, so a fresh dialog builds a total-mode ride.
DEFAULT_JOKERS_MODE = JOKERS_MODE_TOTAL

# setup.xrc's jokers_spin declares the same 0..10 bound (xrc-windows.md
# section B); recorded so the dialog's control and RideConfig's own
# validation cannot drift.
MAX_JOKERS_PER_DECK = 10

# The canvas's lap_km_spin draws "8.0" (the GORBA reference ride's own
# 8 km loop, spec.md §6) but XRC declares no <value>, so a fresh
# dialog would sit at 0.0 and refuse every submit ("lap length must be
# positive"). W4 binds the presenter to push this default on load --
# the same decks_spin/DEFAULT_DECK_COUNT seam -- recorded here so the
# view seam and the dialog never drift from one value.
DEFAULT_LAP_KM = 8.0


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
    concern, not this dataclass's. ``hold_short_laps`` is the one
    field that *does* persist (the ``ride.hold_short_laps`` column,
    part of the flattened v1 baseline, W4): the setup dialog's
    short-lap card policy, default True = "hold short-lap cards for
    review" -- a lap under ``min_lap_s`` deals its card into the held
    state for confirm/void -- with False the operator's explicit
    always-deal choice, crediting the card to the hand like any other
    (:meth:`RideEngine.record_crossing`).

    ``jokers_per_deck``/``jokers_mode`` are the shoe's joker
    configuration (spec §4, Phase 5's Cards controls). Both persist
    (the ``ride.jokers_per_deck``/``ride.jokers_mode`` columns);
    ``jokers_mode`` is :data:`JOKERS_MODE_PER_DECK` (every cycle
    re-deals that many jokers per deck) or :data:`JOKERS_MODE_TOTAL`
    (that many jokers for the whole ride, the dialog's checked
    default), and the Store builds the ride's :class:`~rivercrossing.
    cards.Shoe` with ``jokers_total=(jokers_mode == JOKERS_MODE_TOTAL)``
    -- :func:`check_card_sufficiency` reads the same mode for the
    shoe's capacity.

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
    jokers_mode: str = DEFAULT_JOKERS_MODE
    max_cards: int | None = None
    tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER
    logo_path: Path | None = None
    hold_short_laps: bool = True

    def __post_init__(self) -> None:
        """Validate this config's own spec-defined bounds.

        Raises:
            RideConfigError: ``max_team_size`` is outside 2..10
                (R-12), ``deck_count`` is below 1 (spec §4),
                ``jokers_per_deck`` is outside 0..10 (Phase 5's
                jokers_spin), ``jokers_mode`` is neither
                :data:`JOKERS_MODE_PER_DECK` nor
                :data:`JOKERS_MODE_TOTAL`, or
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
        if not 0 <= self.jokers_per_deck <= MAX_JOKERS_PER_DECK:
            msg = f"jokers_per_deck must be 0..{MAX_JOKERS_PER_DECK}, got {self.jokers_per_deck}"
            raise RideConfigError(msg)
        if self.jokers_mode not in JOKERS_MODES:
            msg = f"jokers_mode must be one of {JOKERS_MODES}, got {self.jokers_mode!r}"
            raise RideConfigError(msg)
        if self.planned_duration_s <= 0:
            msg = f"planned_duration_s must be positive, got {self.planned_duration_s}"
            raise RideConfigError(msg)
        if self.min_lap_s <= 0:
            msg = f"min_lap_s must be positive, got {self.min_lap_s}"
            raise RideConfigError(msg)


# -------------------------------------------- minimum-setup rule


def setup_minimum_violations(config: RideConfig) -> list[str]:
    """Return why *config* fails the minimum ride-setup rule.

    The setup readiness floor a DRAFT ride's start refuses to skip:
    ``name``, ``venue``, ``organizer`` and ``scorer`` must each be
    non-blank once whitespace is stripped, and ``lap_km`` must be
    positive. ``planned_duration_s``/``min_lap_s`` are deliberately
    not re-checked -- :meth:`RideConfig.__post_init__` already
    enforces their positivity at construction, so neither field can
    reach this function invalid.

    Args:
        config: The ride-setup settings to audit.

    Returns:
        One human-readable reason per missing or invalid field, in
        the order above; ``[]`` when *config* is complete.
    """
    violations: list[str] = []
    if not config.name.strip():
        violations.append("name is required")
    if not config.venue.strip():
        violations.append("venue is required")
    if not config.organizer.strip():
        violations.append("organizer is required")
    if not config.scorer.strip():
        violations.append("scorer is required")
    if config.lap_km <= 0:
        violations.append("lap length must be positive")
    return violations


# --------------------------------- card-sufficiency check (plan §10)


# The three verdicts check_card_sufficiency returns. The operator sees
# a rendered sentence, but the machine-readable spelling is what a
# caller keys on, so these are frozen strings rather than a StrEnum: no
# module stores one, unlike RideStatus/PlateModel's persisted values.
NOT_ENOUGH = "not_enough"
OK = "ok"
FAR_TOO_MANY = "far_too_many"


@dataclass(frozen=True, slots=True)
class CardCheck:
    """One ride's shoe size against its estimated card demand.

    ``shoe_cards`` is the shoe's own capacity at the ride's jokers mode
    (spec §4): ``deck_count x (52 + jokers_per_deck)`` per deck, or
    ``deck_count x 52 + jokers_per_deck`` when
    :attr:`RideConfig.jokers_mode` spends the jokers once. ``expected``
    is the crossing count :func:`estimate_cards_needed` predicts for the
    field. ``verdict`` is one of :data:`NOT_ENOUGH` (the shoe runs dry
    before the field stops drawing), :data:`OK`, or :data:`FAR_TOO_MANY`
    (the shoe holds more than twice the demand).
    """

    shoe_cards: int
    expected: int
    verdict: str


def estimate_cards_needed(config: RideConfig, roster: Roster, avg_speed_kmh: float) -> int | None:
    """Estimate how many cards *roster*'s field will draw (plan §10).

    One card per accepted crossing (R-40), so the estimate is the
    crossing count an average-speed field of this size is expected to
    record: the planned duration divided by the seconds one lap takes
    at *avg_speed_kmh*, rounded up (a partial lap still deals), times
    the number of draw units -- every rider on a ``rider_pooled`` ride,
    every entry on a ``team_relay`` one (S1's one-card-per-plate-per-lap
    rule).

    The plate model is compared by its stored ``.value`` rather than
    against ``PlateModel``: importing ``roster`` here at runtime would
    close the import cycle the module docstring records, and
    ``.value``'s spelling is the same persisted one
    :meth:`RideEngine.on_course` already reads.

    Args:
        config: The ride's setup-time settings (lap length, duration).
        roster: The field whose entries/riders are counted.
        avg_speed_kmh: The operator's average rider speed in km/h.

    Returns:
        The estimated card count, or ``None`` when no estimate is
        possible -- a non-positive ``lap_km``/``avg_speed_kmh``/
        ``planned_duration_s``, or an empty roster.
    """
    if (
        config.lap_km <= 0
        or avg_speed_kmh <= 0
        or config.planned_duration_s <= 0
        or not roster.entries
    ):
        return None
    lap_seconds = config.lap_km / avg_speed_kmh * 3600
    laps_per_entry = math.ceil(config.planned_duration_s / lap_seconds)
    draw_units = (
        sum(len(entry.riders) for entry in roster.entries)
        if config.plate_model.value == "rider_pooled"
        else len(roster.entries)
    )
    return draw_units * laps_per_entry


def check_card_sufficiency(
    config: RideConfig, roster: Roster, avg_speed_kmh: float
) -> CardCheck | None:
    """Judge the ride's shoe against its estimated demand (plan §10).

    The "Check for Rider Issues…" card-sufficiency line: the shoe's
    own capacity is compared to :func:`estimate_cards_needed`'s
    prediction at the 1x boundary (demand above capacity is
    :data:`NOT_ENOUGH`) and the 2x boundary (capacity above twice the
    demand is :data:`FAR_TOO_MANY`); everything between is
    :data:`OK`. The capacity itself follows the ride's jokers mode
    (Phase 5): per-deck counts ``deck_count x (52 + jokers_per_deck)``,
    total counts ``deck_count x 52 + jokers_per_deck`` -- the shoe the
    Store actually builds from that config. ``max_cards`` (R-13) is
    deliberately not consulted -- it caps what an entry's hand scores,
    not how many cards the shoe must hold.

    Args:
        config: The ride's setup-time settings.
        roster: The field whose entries/riders are counted.
        avg_speed_kmh: The operator's average rider speed in km/h.

    Returns:
        The :class:`CardCheck`, or ``None`` when the estimate itself
        is impossible (:func:`estimate_cards_needed` returned ``None``).
    """
    expected = estimate_cards_needed(config, roster, avg_speed_kmh)
    if expected is None:
        return None
    if config.jokers_mode == JOKERS_MODE_TOTAL:
        shoe_cards = config.deck_count * 52 + config.jokers_per_deck
    else:
        shoe_cards = config.deck_count * (52 + config.jokers_per_deck)
    if expected > shoe_cards:
        verdict = NOT_ENOUGH
    elif shoe_cards > 2 * expected:
        verdict = FAR_TOO_MANY
    else:
        verdict = OK
    return CardCheck(shoe_cards=shoe_cards, expected=expected, verdict=verdict)


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
    """``start()`` refused because the ride is not ready to start.

    DRAFT's start gate refuses an empty roster ("roster has no
    riders"), a setup missing a minimum field
    (:func:`setup_minimum_violations`), and every
    ``Roster.validate_for_start()`` violation (R-12's team floor);
    any of these blocks the transition.

    ``reasons`` carries that same refusal as one string per blocking
    issue -- a setup violation verbatim, a roster violation prefixed
    with its plate -- so a caller can list them one per line (the
    console's blocked-start issues dialog) instead of re-parsing
    ``str(self)``, which keeps the joined message every caller reads.
    """

    def __init__(self, message: str, *, reasons: tuple[str, ...]) -> None:
        """Store the joined *message* and its per-issue *reasons*."""
        super().__init__(message)
        self.reasons = reasons


class UnknownPlateError(RideEngineError):
    """A caller-named plate, or a move's own target, names nothing.

    ``deal_manual()`` could not resolve *plate* to any entry; the
    pooled live move could not resolve its rider plate to a rider (or,
    for an extraction, to one on a pooled team) or its *to_team* to a
    team. A corrections command fails loudly, unlike
    ``record_crossing``'s console path, which returns a refusal result
    so the entry field can flash its cue; E7's manual-deal dialog
    surfaces this as the error for a mistyped plate. Replay's own
    ``_require_entry_by_key`` raises it too, for a payload key this
    roster no longer holds.
    """


# The event actions :meth:`RideEngine.apply` dispatches -- and so the
# only audit rows a replay may hand it. ``Store.load_engine`` filters on
# this set (plan §8): the ``audit`` table also carries display-only
# history such as roster plate changes, which a rebuild must skip rather
# than trip ``apply``'s unknown-action guard.
REPLAY_ACTIONS: frozenset[str] = frozenset(
    {
        "start",
        "continue",
        "set_start_time",
        "record_crossing",
        "confirm_held",
        "void_held",
        "return_to_held",
        "undo",
        "deal_manual",
        "edit_crossing",
        "void_crossing",
        "add_crossing_at",
        "record_miss",
        "assign_plate_to_miss",
        "reassign",
        "dnf",
        "void_card",
        "move_rider",
        "extract_rider_to_solo",
        "stop",
        "finish",
        "reopen",
        "tiebreak_draw",
        "shoe_reshuffle",
    }
)


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


def _payload_int(event: Event, key: str) -> int:
    """Parse one integer payload value back into an int.

    The corrections' ``seq``/``miss_seq`` keys are ints live but JSON
    numbers-or-strings on disk, so the replay path reads them through
    the same int-coercion every live call site used.

    Args:
        event: The event being replayed.
        key: The payload key holding the integer.

    Returns:
        The parsed integer.
    """
    return int(str(event.payload[key]))


def _payload_strings(event: Event, key: str) -> tuple[str, ...]:
    """Read one optional string-list payload value back into a tuple.

    E6.4.3's ``self_test_failed_checks`` is written only for a finish
    that overrode a red self-test, so a missing key is the ordinary
    clean-finish case -- every row persisted before the field existed
    replays through here. Anything but a list reads as absent.

    Args:
        event: The event being replayed.
        key: The payload key holding the list of strings.

    Returns:
        Every entry of the list, as a string; empty when the key is
        absent or does not hold a list.
    """
    value = event.payload.get(key, ())
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(part) for part in value)


def _payload_draws(event: Event) -> dict[str, Card]:
    """Read a ``tiebreak_draw`` payload's entry/card rows.

    The payload is JSON-ready, so each row's card code parses back
    through :meth:`Card.parse` and the ride's drawn cards rebuild
    exactly as the live draw made them. Anything but a list of rows
    reads as no draws at all, mirroring :func:`_payload_strings`'s
    tolerance of a stored value of the wrong shape.

    Args:
        event: The event being replayed.

    Returns:
        The payload's draws, keyed by entry id; empty when the payload
        holds no row list.
    """
    rows = event.payload.get("draws", ())
    if not isinstance(rows, (list, tuple)):
        return {}
    return {str(row["entry_id"]): Card.parse(str(row["card"])) for row in rows}


def _format_elapsed(seconds: float) -> str:
    """Render *seconds* as the session's h:mm:ss reading (scope 6d).

    The audit trail's own elapsed formatter: ``0:00:00`` at the gun,
    ``1:02:05`` past an hour. Unlike
    ``ui.presenters.data_source.format_duration`` this keeps the sign
    -- a back-dated gun (``set_start_time``) reads ``"-0:05:00"`` -- so
    the two formatters are deliberately separate: one renders a display
    clock (never negative), this one renders an audit reading. A
    fractional second truncates toward zero, the recording instants
    being whole seconds.
    """
    total = int(seconds)
    sign = "-" if total < 0 else ""
    hours, remainder = divmod(abs(total), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{sign}{hours}:{minutes:02d}:{secs:02d}"


def _rider_name_for(entry: Entry, plate: str) -> str:
    """Return the name of *entry*'s rider whose own plate is *plate*.

    The ``record_crossing`` reason's attribution (scope 6d), mirroring
    ``ui.presenters.data_source._rider_name_for``: under
    ``PlateModel.RIDER_POOLED`` each team member carries their own
    plate, so the plate the operator typed names the rider who
    actually crossed. ``""`` covers every case with no such rider --
    a ``team_relay`` ride's riders carry no plate at all (S1), and a
    team's own plate belongs to no member -- so the caller falls back
    to the typed plate.
    """
    for rider in entry.riders:
        if rider.plate == plate:
            return rider.full_name
    return ""


def _crossing_reason(entry: Entry, plate: str) -> str:
    """Return the ``record_crossing`` reason for *entry* at *plate*.

    ``"{rider} · {team|solo}"`` (scope 6d): the rider whose own plate
    the operator typed, then the entry's own identity -- its
    ``display_name`` for a TEAM entry, the word "solo" otherwise (the
    same word every rider list shows, ``ui.rider_columns.
    SOLO_TEAM_TEXT``, which this module cannot import: no ``wx`` or UI
    dependency may land below ``rivercrossing.ui``). The kind test
    reads ``entry.type.value`` rather than ``EntryType.TEAM`` for the
    same reason the module never imports ``roster`` at runtime -- it
    is duck-typed (module docstring). A plate no rider owns falls back
    to the plate itself, so the cell is never blank.
    """
    rider_name = _rider_name_for(entry, plate) or plate
    team = entry.display_name if entry.type.value == "team" else "solo"
    return f"{rider_name} · {team}"


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


def _require_lap_after(entry_label: str, crossed_at: datetime, previous: datetime) -> None:
    """Refuse a lap instant at or before *previous* (Phase 3).

    A lap that takes no time is not a lap, so the two correction
    commands that can date a crossing explicitly -- ``edit_crossing``
    and ``add_crossing_at`` -- refuse a zero or negative lap time
    before writing anything. *previous* is the instant the lap would be
    measured from: the entry's preceding lap, or ``actual_start`` when
    there is none. *entry_label* is the operator-facing name for the
    entry (its plate -- never the internal stable key, whose uuid would
    mean nothing at the console).

    Raises:
        ValueError: *crossed_at* is at or before *previous*.
    """
    if crossed_at <= previous:
        msg = (
            f"lap time must be positive for entry {entry_label}: "
            f"{crossed_at.isoformat()} is not after {previous.isoformat()}"
        )
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

    ``seq`` is 1-based per entry; ``entry_id`` is the entry's stable
    :attr:`~rivercrossing.roster.Entry.key` -- the surrogate no
    re-plating can invalidate (E3.1.2's pooled-live-move seam), where
    the entry's *plate* is derived from its riders and re-derived by a
    mid-ride move. Lap times are
    never stored -- spec §6 derives them from the entry's previous
    crossing (or ``actual_start`` for lap 1), so a ``set_start_time``
    retro-fix recomputes lap-1 automatically.

    ``rider_plate`` is the plate the operator actually typed for this
    crossing (J1). Under ``rider_pooled`` that is a team member's own
    plate, which is what the console feed attributes the lap to; on a
    solo or ``team_relay`` ride it is the entry's own plate. It is
    appended last with a ``None`` default so every positional
    construction still compiles.
    """

    entry_id: str
    seq: int
    crossed_at: datetime
    rider_plate: str | None = None


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
    ``config.min_lap_s`` *and* ``config.hold_short_laps`` -- the card
    is then *held* (:meth:`RideEngine.held_crossings`), not credited
    (R-34). Under the W4 default (``hold_short_laps`` True) that is
    the ordinary short-lap path; with it False (the operator's
    explicit always-deal choice) a short lap never flags for review:
    the card is credited to the hand and ``flagged`` stays False.
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


@dataclass(frozen=True)
class PendingMiss:
    """One recorded miss whose number was not captured (K, spec §13).

    A miss is a passing whose plate the scorer did not catch: typing one
    of the miss symbols instead of a plate records it here, *outside*
    :attr:`RideEngine._crossings`. It is deliberately not a
    :class:`Crossing` -- so it never enters ``card_for``/``undo_last``/
    ``reassign_crossing``/the per-entry lap index, and deals no card.
    ``miss_seq`` is the ride-wide 1-based ordinal in recording order
    (like ``Crossing.seq``, but independent of any entry);
    ``crossed_at`` is the instant the operator signalled the miss.
    :meth:`RideEngine.assign_plate_to_miss` later removes it and records
    the real crossing at that same instant.
    """

    miss_seq: int
    crossed_at: datetime


def _shoe_structure(config: RideConfig) -> tuple[int, int, str]:
    """Return *config*'s shoe structure: (decks, jokers per deck, mode).

    The three fields Edit Ride… locks past DRAFT, compared as one
    value so :meth:`RideEngine.update_config` rebuilds the shoe only
    when one of them really changed.
    """
    return (config.deck_count, config.jokers_per_deck, config.jokers_mode)


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
      one plate namespace spans the ride (R-20), but a plate is
      *mutable*: a ``rider_pooled`` team's plate is derived from its
      lowest-numbered member and re-derived by ``Roster.move_rider``,
      so it can never be what a recorded lap is filed under. The
      engine keys ``_laps``/``_hand``/the held queue and each
      ``Crossing.entry_id`` by :attr:`~rivercrossing.roster.Entry.key`
      -- the per-entry surrogate the entry table persists -- while a
      plate stays the resolution and display value (E3.1.2's
      pooled-live-move seam).
    - **on_course.** Spec §6 names the counter without defining it; a
      loop timing's natural reading is an ACTIVE entry whose lap count
      is odd (out on the loop, not yet back).

    E4.2's own resolutions:

    - **Crossing card.** Every accepted crossing deals one card from
      the shoe (``_deal_card``; R-40); a ``ShoeEmpty`` mid-ride
      reshuffles (seed+1) and appends an audit ``Event`` named
      ``"shoe_reshuffle"`` with payload ``{"cycle": N, "jokers_added":
      M, "reason": "M jokers added"}`` before the crossing's own
      ``record_crossing`` event. The per-deal card rides
      on ``CrossingResult.card``; the ``record_crossing`` event payload
      stays E4.1-pinned but for its ``reason`` (scope 6d: the rider who
      crossed and the entry they crossed for) -- deal auditability is
      the seeded shoe's replay guarantee (R-40), and E5's Store
      persists the card row with its own ``shoe_index``.
    - **Held cards.** A lap under ``config.min_lap_s`` is flagged
      short: the lap still records, but its card is dealt into a
      *held* state (spec §4, R-34) -- tracked in ``_held``, exposed by
      ``held_crossings()`` -- and never credited until ``confirm_held``
      releases it into the entry's hand or ``void_held`` discards it.
      ``confirm_held``/``void_held`` are gated only by the card being
      held, never by ride state: the review surface stays usable while
      RUNNING, and FINISHED's corrections flow routes through REOPENED
      for timing changes (undo), not card disposition.
      ``return_to_held`` is the disposition seam back: a credited card
      re-enters the hold queue under that same state-irrelevant gate,
      while a voided card -- retired by ``void_card`` or discarded by
      ``void_held`` -- stays voided and the crossing is dealt a fresh
      card instead, which needs the shoe open (RUNNING or REOPENED).
    - **Short-lap policy (W4).** ``RideConfig.hold_short_laps`` gates
      whether that hold path runs at all. The True default is the W4
      product decision -- hold short-lap cards for review -- so the
      flag-and-hold path above is what a ride gets unless the operator
      explicitly picks always-deal in the setup dialog: with
      ``hold_short_laps`` False a short lap's card is credited to the
      hand like any other and nothing lands in ``_held`` or
      ``held_crossings()``, so the review surface stays empty and the
      result's ``flagged`` stays False (flagging *means* "held for
      review"; the downstream feed derives its flagged rows from the
      hold queue). The setup dialog's
      ``hold_short_radio``/``always_deal_radio`` pair owns the value
      (setup.xrc, W4), and the store persists it per ride
      (``ride.hold_short_laps``) so replay reproduces the same
      disposition.
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
    - **High-card draw at the finish (R-14).** ``finish()`` records the
      venue's tie-break draw once the finish instant is in: every group
      of two or more ACTIVE entries holding exactly equal hands
      (``hands.compare``) draws one card per entry from *one* fresh deck
      -- ``cards.high_card_draw`` under the shoe's stored seed salted
      with :data:`_TIEBREAK_DRAW_SEED_XOR`, so the draw replays from the
      stored ``rng_seed`` (R-40) without dealing from, or otherwise
      disturbing, the shoe. The cards are handed out in group
      first-appearance and then roster order, ``snapshot()`` carries
      each entry's card as ``tiebreak_card``, and a ``tiebreak_draw``
      event records the rows plus a summary naming them.
      ``reopen()`` and ``start()``'s continue branch clear the draws, so
      a corrected ride redraws at its next finish rather than carrying a
      stale card forward; a replayed ``tiebreak_draw`` restores them
      from its payload.

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
    - **Clock-stamped events.** ``stop`` re-stamps its payload
      timestamp from the engine's clock when replayed, so its audit
      bytes differ from the live original by design; ``finish`` and
      ``reopen`` (C3) instead re-apply their persisted
      ``finished_at``/``reopened_at``, because a replay must reproduce
      the recorded finish instant -- ``closed_elapsed()`` would
      otherwise read the replay time. Replay equivalence compares
      event count/actions, not payload fields (recorded in the
      property's comparison contract).
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
      restitution stays ``undo_last``'s job. The voided-card registry
      is keyed by the card's *identity* (``id(card)``), never by
      value: ``Card`` is a frozen value dataclass and the shoe builds
      a distinct-but-value-equal ``Card`` per deck, so under the
      default of eight decks two physical cards share one code and a
      value key would alias them -- a void on one entry could suppress
      a same-code re-credit on another. One residual remains, recorded
      in ``__init__``: a void of two same-code cards credited to the
      *same* entry cannot name a physical card (``_hand`` carries no
      crossing id), so the engine voids the first value match; live
      and replay pick the same one, so equivalence holds.
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

    Phase 3's own resolutions (minimum lap time, duplicate detection):

    - **No zero or negative lap time.** ``edit_crossing``'s replacement
      instant must be strictly after the lap it would be measured from
      (the entry's preceding lap, ``actual_start`` for the earliest
      one) and ``add_crossing_at``'s explicit instant strictly after
      the entry's latest crossing (``actual_start`` when it has none);
      both raise ``ValueError`` before writing anything, so a refused
      correction touches no crossing, deals no card and appends no
      event. The refusal is the derived-lap-time rule itself
      (``lap_times``), not ``config.min_lap_s`` -- a short lap is still
      a lap and still flags (R-34).
    - **The gate holds on replay.** ``apply`` re-applies a persisted
      ``edit_crossing``/``add_crossing_at`` through the public command
      -- never a private ungated half -- so a replayed event is judged
      by the same zero/negative-lap gate as a live correction. A legal
      live command already satisfied that gate when it ran, so
      replaying it still rebuilds the ride exactly. The app is
      unreleased and its databases are erased, so no pre-gate row
      exists to preserve (E5.1.2).
    - **Duplicates are a projection, never a mutation.**
      ``duplicate_crossings`` names every live pair sharing one entry
      and one identical instant, oldest first; it changes nothing and
      nothing is auto-removed. The console's Needs Review tab lists
      exactly those pairs (``FeedRow.duplicate``) so the operator opens
      Crossing Detail and deletes one deliberately.

    K's own resolution (record a miss):

    - **A miss is not a Crossing.** ``record_miss`` records a passing
      whose plate the scorer did not catch into ``_pending_misses``, a
      separate queue, never ``_crossings`` -- so ``card_for``/
      ``undo_last``/``reassign_crossing``/the ``_laps`` index/
      ``feed_rows`` counters and ``snapshot()`` never see it, and no
      card is dealt. ``assign_plate_to_miss`` removes the pending miss
      and records (and deals) the real crossing at the miss's original
      instant, so the card is assigned at edit time, never at record
      time.

    The pooled live move's own resolutions (E3.1.2, R-17):

    - **The move gate.** ``move_rider``/``extract_rider_to_solo`` are
      legal only while the ride is stopped RUNNING (R-35's Stop guard,
      so the clock is locked before a re-shuffle) or REOPENED, and
      only on a ``rider_pooled`` ride -- the same cell
      ``roster.can_move_rider`` opens, read off the stored enum
      ``.value`` because ``roster`` is never imported here. The live
      RUNNING refusal is exactly "Stop the ride first" (the console's
      own instruction); DRAFT and FINISHED name their state. The
      engine carries these refusals as ``IllegalStateError`` -- the
      roster's own ``LockedError`` can never be imported from below
      it.
    - **Keys, never entries, name the two sides.** The move's payload
      records ``from_key``/``to_key`` (the entries' stable
      :attr:`~rivercrossing.roster.Entry.key`) rather than plates:
      a pooled move re-derives both teams' plates, and a solo source
      is *dissolved* by the move, so a replayed payload must be able
      to name an entry the rebuilt roster no longer holds. A
      same-team move is refused outright: it can be neither a
      membership change nor a re-attribution, and re-keying an entry
      onto itself would collide its own lap seqs.
    - **Replay never re-applies the roster.** ``apply`` re-runs only
      ``_reattribute_rider`` for a replayed move: the replayed roster
      is already final (``Store.load_engine`` rebuilds it from the
      entry/rider tables, never from the event log), so the recorded
      laps re-key onto the destination exactly as they did live.
    - **A dissolved source entry is retired, so its key survives.**
      The ``record_crossing`` rows recorded before a solo-sourced move
      carry the *dissolved* solo entry's key, and ``apply`` resolves
      those by key -- so a dissolve keeps an entry that carries
      recorded data instead of discarding it (``Roster``'s retired
      collection, persisted by ``Store.save_roster`` as
      ``entry.retired = 1`` and restored by ``Store._load_roster``).
      A reloaded ride therefore still resolves the pre-move rows, and
      a replayed ``mark_has_data`` on the retired entry is a known
      entry, not a foreign one. An entry dissolved with no recorded
      data is still discarded outright: nothing names its key.
    - **A voided lap of the moved rider is re-interpreted, not
      restored.** The pre-void held/credited disposition is not
      stored, so the restored lap's short-lap disposition is
      re-derived against its new predecessor (R-34) -- a short lap
      re-enters the hold queue, every other lap credits. Card-level
      voids (``void_card``/``void_held``) are untouched: they are
      cards on still-live laps.
    - **The source renumbers last, highest gap first.** The moved
      rider's live laps leave in record order while the source's
      remaining laps keep their seqs until every removal has landed;
      ``_renumber_later`` then runs from the highest removed seq
      down. Each call closes the one gap above the seq it names, so
      descending is the only order that leaves the survivors
      contiguous 1..N.
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
        # C3: the finish instant ``finish()`` records and ``reopen()``
        # preserves, so a closed ride's final elapsed is the recorded
        # value, never the live clock. Cleared on continue.
        self._finished_at: datetime | None = None
        self._crossings: list[Crossing] = []
        # K: pending misses -- passes whose number the scorer did not
        # catch. Kept OUT of _crossings so no crossing-shaped consumer
        # (card_for/undo_last/reassign_crossing/_laps/feed counters, all
        # of which iterate _crossings) ever sees one, and no card is
        # dealt until assign_plate_to_miss records the real crossing.
        self._pending_misses: list[PendingMiss] = []
        self._miss_counter = 0
        # Per-entry lap index (review fix): entry_id -> its live
        # crossings sorted by crossed_at, holding the SAME Crossing
        # objects _crossings holds. _laps_for reads this instead of
        # scanning the ride-wide list, so Store.load_engine replay and
        # snapshot() stay linear; _insert_crossing/_remove_crossing/
        # _replace_crossing keep the two structures in lockstep.
        self._laps: dict[str, list[Crossing]] = {}
        self._dealt: dict[Crossing, Card] = {}
        self._held: dict[Crossing, Card] = {}
        # Credited cards, keyed by entry plate, each tagged with the
        # plate the operator actually typed for the crossing that dealt
        # it (``Crossing.rider_plate``; a manual/correction deal's own
        # plate). The tag is what makes a per-rider DNF possible: a
        # pooled team's forfeit takes only the DNF rider's cards, not
        # the whole hand.
        self._hand: dict[str, list[tuple[Card, str | None]]] = {}
        # Per-rider DNF marks, replayable only from the ``dnf`` events
        # (the persisted rider table has no dnf column): a pooled team
        # is out of the results when every one of its riders is here.
        self._dnf_riders: set[str] = set()
        # R-14's high-card draws: entry id -> the card that entry
        # drew for the venue's tie-break. Written by finish() (and a
        # replayed ``tiebreak_draw``), read by snapshot()'s
        # ``tiebreak_card``, and cleared by reopen()/continue -- a
        # corrected ride redraws at its next finish rather than
        # carrying a stale card forward.
        self._tiebreak: dict[str, Card] = {}
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
        # Voided cards, keyed by id(card) -- object *identity*, never
        # value: Card is a frozen value dataclass, the shoe builds a
        # distinct-but-equal Card per deck, and the default of eight
        # decks therefore holds eight physical cards per code. Keying
        # by value aliased them -- a void on one entry could suppress a
        # same-code re-credit on another. The dict's *values* keep every
        # registered card alive, so no later object can inherit a
        # retired id (a bare set[int] would be unsafe). One residual:
        # _hand carries no crossing id, so a void of two same-code
        # cards credited to one entry cannot name a physical card --
        # _discard_credited removes the first value match and its own
        # object is what gets marked. Live and replay pick the same
        # one, so replay equivalence holds.
        self._voided_cards: dict[int, Card] = {}

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
    def self_test_unverified(self) -> bool:
        """Return whether the closed ride's results are unverified.

        E6.4.3: ``finish(self_test_failed_checks=...)`` writes the
        failed BLOCKING self-test checks into the finish event's own
        payload, so a ride the operator chose to close over a red
        evaluator self-test says so for as long as that finish stands.
        The LAST finish event decides: a ride that has re-finished
        cleanly after a reopen is verified again. The read is gated on
        FINISHED -- a reopened or continued ride has no published
        result to qualify, so both read ``False``.

        Read-only and non-raising: a ride that never finished, a
        pre-E6.4.3 finish row without the key, and a malformed stored
        value (:func:`_payload_strings`'s tolerance) all read
        ``False``.
        """
        if self._state is not RideStatus.FINISHED:
            return False
        for event in reversed(self._events):
            if event.action == "finish":
                return bool(_payload_strings(event, "self_test_failed_checks"))
        return False

    @property
    def events(self) -> tuple[Event, ...]:
        """Return every ride event, oldest first, read-only.

        EPIC 5's Store persists these and rebuilds an engine by
        replaying them (module-skeletons.md S4).
        """
        return tuple(self._events)

    @property
    def actual_start(self) -> datetime | None:
        """Return the gun instant, or ``None`` before the ride starts.

        The origin every elapsed value derives from: the console's
        elapsed clock (:meth:`elapsed`) and the crossings feed's Time
        column both measure from here. ``set_start_time`` moves it, so
        the feed's own elapsed column follows a back-dated gun
        automatically. Read-only and non-raising, unlike
        :meth:`_require_actual_start` -- a DRAFT ride legitimately has
        no start yet.
        """
        return self._actual_start

    @property
    def dnf_riders(self) -> frozenset[str]:
        """Return the plates of every rider marked DNF, read-only.

        The per-rider half of the DNF surface (``mark_dnf``): a pooled
        team stays in the results until every one of its riders is in
        this set, and each marked rider's own crossings are what the
        console's feed marks. An entry-level DNF (a solo rider, a relay
        team) is the roster entry's own status instead -- see
        :meth:`entry_is_dnf`.
        """
        return frozenset(self._dnf_riders)

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
            if entry.status.value == "active" and len(self._laps_for(entry.key)) % 2 == 1
        )

    @property
    def entry_count(self) -> int:
        """Return how many entries the roster holds (W5's stop gate).

        The console Stop flow (``ConsolePresenter.on_stop_requested``)
        reads this before offering its confirm: a riderless ride --
        reachable after a store replay against a drifted roster, where
        ``start``'s own readiness gate no longer applies -- gets a
        warning instead of a Stop dialog. Read-only and live, like
        :attr:`on_course`: the shared roster can grow after
        construction, and the count always reflects it.
        """
        return len(self._roster.entries)

    # E4.4.1 console read accessors. The console's ``EngineDataSource``
    # (rivercrossing.ui.presenters.data_source) builds its feed and
    # counters from these; each is a read-only projection over state
    # this engine already owns, never a new mutation path.

    @property
    def config(self) -> RideConfig:
        """Return this ride's frozen setup-time config (spec §2)."""
        return self._config

    def update_config(self, config: RideConfig) -> None:
        """Replace this ride's setup config in place (D2's Edit Ride).

        Edit Ride… corrects the *settings* of the ride that is already
        open -- its name, date, start, venue, organizer, scorer and (in
        DRAFT) its structure. The ride itself is untouched: the roster
        and the event log stay exactly as they are, so editing a
        started ride never rewinds it, re-deals a card or invalidates
        the audit trail. Structural gating is the setup dialog's (it
        locks those controls past DRAFT).

        A DRAFT ride's shoe-structure change is the one exception: the
        live shoe is rebuilt from its own stored seed under the new
        ``deck_count``/``jokers_per_deck``/``jokers_mode``, so the
        stored config and the shoe can never silently diverge. Past
        DRAFT the shoe is never touched -- those controls are locked,
        and a FINISHED ride's shoe is closed.

        Args:
            config: The edited configuration to hold from now on.
        """
        previous = self._config
        self._config = config
        if self._state is RideStatus.DRAFT and _shoe_structure(config) != _shoe_structure(
            previous
        ):
            self._shoe.reconfigure(
                config.deck_count,
                config.jokers_per_deck,
                jokers_total=config.jokers_mode == JOKERS_MODE_TOTAL,
            )

    @property
    def crossings(self) -> tuple[Crossing, ...]:
        """Return every recorded crossing, oldest first, read-only.

        Undo removes the reversed crossing, so this always reflects
        the current live state -- the console feed's source of truth.
        """
        return tuple(self._crossings)

    def duplicate_crossings(self) -> tuple[tuple[Crossing, Crossing], ...]:
        """Return each live pair sharing one entry and one instant.

        Phase 3's double-entry detector: the same plate recorded twice
        at the identical instant -- a mis-key, a CSV/store import, or a
        miss assigned onto a crossing already on the line -- leaves two
        laps the operator must delete one of. Identity is
        (``entry_id``, ``crossed_at``) with exact datetime equality, so
        two entries crossing together are never a pair, and two laps of
        one entry a second apart are not either.

        Read-only and side-effect free (the console's Needs Review tab
        is a pure projection of it). Pairs are (older, newer) in record
        order, and the pairs themselves are oldest instant first.

        Returns:
            One ``(Crossing, Crossing)`` tuple per duplicate pair,
            empty when the ride holds none.
        """
        by_instant: dict[tuple[str, datetime], list[Crossing]] = {}
        for crossing in self._crossings:
            by_instant.setdefault((crossing.entry_id, crossing.crossed_at), []).append(crossing)
        ordered = sorted(by_instant, key=lambda key: (key[1], key[0]))
        return tuple(pair for key in ordered for pair in combinations(by_instant[key], 2))

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

        From DRAFT: the roster must hold at least one entry and the
        config must clear the minimum-setup rule
        (:func:`setup_minimum_violations`); then the team-size gate
        (:meth:`Roster.validate_for_start`) must be clear; then
        ``actual_start`` is *at* or ``clock()`` and the state becomes
        RUNNING. From RUNNING: continue -- unlock plate entry, keep
        ``actual_start`` unchanged ("Continue ride?").

        From REOPENED (C2): continue riding -- the corrections state
        offers Start, so the engine moves back to RUNNING with the
        recorded ``actual_start`` kept, the stop guard cleared and the
        roster back on RUNNING; the closed finish instant is discarded
        (the clock is live again). From FINISHED: raise.

        Args:
            at: The start instant; omit to use the injected clock.

        Returns:
            The appended ``start``/``continue`` event.

        Raises:
            StartBlockedError: DRAFT and the ride is not ready --
                the roster has no entries, the setup misses a minimum
                field, or a team sits below the size floor. Its
                ``reasons`` holds one entry per blocking issue.
            IllegalStateError: the state is FINISHED.
        """
        if self._state in (RideStatus.RUNNING, RideStatus.REOPENED):
            started_at = self._require_actual_start()
            self._state = RideStatus.RUNNING
            self._roster.status = RideStatus.RUNNING
            self._stopped = False
            self._finished_at = None
            # R-14: the draws belong to the finish that is being ridden
            # away from, so continuing discards them -- the next finish
            # draws afresh.
            self._tiebreak.clear()
            return self._append(
                Event(
                    action="continue",
                    payload={
                        "actual_start": started_at.isoformat(),
                        # A continue records no new instant, so the
                        # clock it reports is still the gun (scope 6d).
                        "reason": _format_elapsed(0.0),
                    },
                )
            )
        if self._state is not RideStatus.DRAFT:
            raise IllegalStateError(f"cannot start from {self._state}")
        if not self._roster.entries:
            raise StartBlockedError("roster has no riders", reasons=("roster has no riders",))
        setup_violations = setup_minimum_violations(self._config)
        if setup_violations:
            joined = "; ".join(setup_violations)
            raise StartBlockedError(
                f"ride setup is incomplete: {joined}", reasons=tuple(setup_violations)
            )
        violations = self._roster.validate_for_start()
        if violations:
            # One tuple, joined for the message: the refusal's two
            # views cannot drift apart.
            issue_reasons = tuple(
                f"{violation.entry.plate}: {violation.reason}" for violation in violations
            )
            raise StartBlockedError(
                f"roster is not ready to start: {'; '.join(issue_reasons)}",
                reasons=issue_reasons,
            )
        return self._begin_start(at if at is not None else self._clock())

    def _begin_start(self, started_at: datetime) -> Event:
        """Record the DRAFT -> RUNNING start transition.

        Shared by :meth:`start` -- which calls it only after its
        readiness gates clear -- and the replay seam
        (:meth:`apply`, E5.1.2): ``actual_start`` is fixed, the ride
        and its roster move to RUNNING, plate entry unlocks and the
        ``start`` audit row appends. Replay calls it directly, never
        through :meth:`start`'s gates: a persisted ``start`` row
        already cleared them when it ran live, so re-judging them on
        a rebuilt DRAFT engine would refuse rides whose stored state
        predates a gate -- e.g. the E9.2.2 sim's deliberately empty
        TEAM_RELAY shell, or a running ride resumed after an upgrade.

        Args:
            started_at: The recorded start instant.

        Returns:
            The appended ``start`` audit event.
        """
        self._actual_start = started_at
        self._state = RideStatus.RUNNING
        self._roster.status = RideStatus.RUNNING
        self._stopped = False
        return self._append(
            Event(
                action="start",
                payload={
                    "actual_start": started_at.isoformat(),
                    # The gun's own reading against itself: the ride
                    # clock starts here (scope 6d).
                    "reason": _format_elapsed(0.0),
                },
            )
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
                    # How far the gun moved (scope 6d): a back-date
                    # moves it earlier, so the reading is negative
                    # ("-0:05:00" for the canonical 10:00 -> 09:55
                    # correction).
                    "reason": _format_elapsed((at - previous).total_seconds()),
                },
            )
        )

    def record_crossing(self, plate: str, at: datetime | None = None) -> CrossingResult:
        """Record one completed lap for *plate* (spec §6, E4.2.1).

        Resolves *plate* to its entry via the roster (an entry's own
        plate, or a rider_pooled rider's plate, credits the entry --
        uncapped, R-16), appends one lap with a timestamp, marks the
        entry has_data, and deals one card from the shoe (R-40). A lap
        under ``config.min_lap_s`` is short and always reports
        ``flagged=True`` -- the review channel the console's FLAGGED
        cue and Needs Review panel read. The W4 policy decides the
        card's disposition alone: with ``config.hold_short_laps`` True
        (its default, the setup dialog's checked radio) the lap still
        records but its card is held, not credited (R-34); with it
        False (the operator's always-deal choice) the card is credited
        to the hand like any other. Refusals come back as
        ``accepted=False``
        results, never raises: not RUNNING, stopped (E4.1.3), or an
        unknown plate (``reason="unknown_plate"``, E4.2.4 -- the error
        cue is E4.4's UI concern).

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
        return self._record_crossing_into(entry, plate, at)

    def _record_crossing_into(
        self, entry: Entry, plate: str, at: datetime | None
    ) -> CrossingResult:
        """Record one lap for the already-resolved *entry*.

        The body of :meth:`record_crossing` past its plate resolution:
        ``record_crossing`` supplies the entry it resolved a typed plate
        to, and replay supplies the entry its payload's stable key names
        (``apply``). *plate* stays the value the operator typed -- it is
        the crossing's ``rider_plate`` attribution (J1) and the
        ``reason``'s "who crossed for whom" -- while every engine
        identity here is the entry's own stable
        :attr:`~rivercrossing.roster.Entry.key`
        (E3.1.2's pooled-live-move seam).
        """
        crossed_at = at if at is not None else self._clock()
        start = self._require_actual_start()
        entry_key = entry.key
        laps = self._laps_for(entry_key)
        seq = len(laps) + 1
        card = self._deal_card()
        crossing = Crossing(entry_id=entry_key, seq=seq, crossed_at=crossed_at, rider_plate=plate)
        self._insert_crossing(crossing)
        self._dealt[crossing] = card
        self._roster.mark_has_data(entry)
        previous = laps[-1].crossed_at if laps else start
        lap_time = (crossed_at - previous).total_seconds()
        short = lap_time < self._config.min_lap_s
        if short and self._config.hold_short_laps:
            # R-34 (W4 default): the card waits for review, uncredited.
            self._held[crossing] = card
        else:
            # Always-deal policy: a short lap still credits.
            self._credit(entry_key, card, crossing.rider_plate)
        self._append(
            Event(
                action="record_crossing",
                payload={
                    "plate": plate,
                    "entry_id": entry_key,
                    "lap": seq,
                    "crossed_at": crossed_at.isoformat(),
                    # The audit trail's own "who crossed for whom"
                    # (scope 6d), resolved from the typed plate.
                    "reason": _crossing_reason(entry, plate),
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
            flagged=short,
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

    def held_card_for(self, crossing: Crossing) -> Card | None:
        """Return *crossing*'s held card, or ``None`` if not held.

        W9's feed seam: the console feed shows every crossing's dealt
        card, so a held crossing's row carries the real code of the
        card in the hold queue, never a placeholder. Returns ``None``
        for a credited, released or unknown crossing -- exactly the
        :meth:`held_crossings` membership test, read-only.
        """
        return self._held.get(crossing)

    def credited_cards(self, entry_id: str) -> tuple[Card, ...]:
        """Return the entry's credited (non-held) cards for *entry_id*.

        The console's rider list reads this for its Cards column: the
        entry's live credited hand -- normal deals plus confirmed-held
        releases (R-16/R-34), oldest first. A held (unconfirmed),
        voided or undone card is not credited, so it never appears;
        ``entry_id`` must be the entry's own stable
        :attr:`~rivercrossing.roster.Entry.key`
        -- the stable identity the credited hand is kept under, never
        the mutable display plate -- so callers pass ``entry.key``. An
        entry that has credited nothing (and an unknown key) returns an
        empty tuple rather than raising.

        Read-only: unlike :meth:`snapshot` this does not apply
        ``config.max_cards`` (R-13), which caps the scored hand, not
        the cards the entry actually holds -- nor a DNF rider's
        forfeiture, which is a scoring rule (the console's Cards column
        still shows every card dealt).
        """
        return tuple(card for card, _rider_plate in self._hand.get(entry_id, ()))

    def pending_misses(self) -> tuple[PendingMiss, ...]:
        """Return every pending miss, oldest first, read-only.

        The console feed renders one ``-``/``missed`` row per pending
        miss (newest first); :meth:`assign_plate_to_miss` removes one
        and records the crossing it stands for. Read-only, like
        :meth:`held_crossings`: a miss never enters ``_crossings``.
        """
        return tuple(self._pending_misses)

    def record_miss(self, crossed_at: datetime, reason: str) -> Event:
        """Record a miss -- a passing whose number was not captured (K).

        The console path for a scorer who sees a rider cross but does
        not catch the plate, so one of the miss symbols is typed
        instead of a number. The miss is kept *outside*
        :attr:`crossings` -- it is not a :class:`Crossing`, deals no
        card and is ignored by :meth:`snapshot`/``on_course``/the
        counters -- until :meth:`assign_plate_to_miss` resolves a real
        plate, at which point the crossing and its card are recorded at
        the miss's original instant. RUNNING or REOPENED only.

        Args:
            crossed_at: The instant the miss was signalled.
            reason: Why the number was missed; carried in the audit
                payload.

        Returns:
            The appended ``record_miss`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot record miss from {self._state}")
        self._miss_counter += 1
        self._pending_misses.append(
            PendingMiss(miss_seq=self._miss_counter, crossed_at=crossed_at)
        )
        return self._append(
            Event(
                action="record_miss",
                payload={
                    "miss_seq": self._miss_counter,
                    "crossed_at": crossed_at.isoformat(),
                    "reason": reason,
                },
            )
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
        self._credit(crossing.entry_id, card, crossing.rider_plate)
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
        not added to any hand -- and recorded in the voided-card
        registry, the same one ``void_card``/``void_crossing`` use, so
        every later reassign or return path reads it as already voided.
        The card recorded is the engine's own dealt object, which is
        what the registry's identity key needs. The lap itself stays
        recorded. Audited. Gated only by the card being held.

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
        self._mark_voided(card)
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

    def return_to_held(self, crossing: Crossing) -> Event:
        """Put *crossing*'s dealt card back into the hold queue (R-34).

        The operator's "that card needs another look" action, in two
        dispositions. A *credited* card leaves the entry's hand and
        re-enters the hold queue under the same state-irrelevant gate
        ``confirm_held``/``void_held`` use. A *voided* card
        (``void_card``/``void_held``) never comes back -- it stays in
        the voided-card registry, which ``_is_voided`` reads by
        identity, out of the ride -- so the crossing is given a fresh
        card dealt from the shoe instead, which needs the shoe open:
        RUNNING or REOPENED. Either way the card awaits
        ``confirm_held`` or ``void_held`` again. Audited.

        Args:
            crossing: A crossing this engine dealt a card for.

        Returns:
            The appended ``return_to_held`` audit event.

        Raises:
            IllegalStateError: *crossing*'s card is already held, or it
                was voided and the ride is not RUNNING or REOPENED.
            KeyError: *crossing* was never dealt by this engine.
        """
        if crossing in self._held:
            raise IllegalStateError("crossing's card is already held")
        card = self.card_for(crossing)
        if self._is_voided(card):
            if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
                raise IllegalStateError(f"cannot re-deal a voided card from {self._state}")
            card = self._deal_card()
            self._dealt[crossing] = card
        else:
            self._discard_credited(crossing.entry_id, card)
        self._held[crossing] = card
        return self._append(
            Event(
                action="return_to_held",
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
        self._discard_credited(last.entry_id, card)
        self._unmark_voided(card)
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

        The operator's bonus-card correction: one card comes off the
        shoe and credits straight into the entry's hand -- never the
        held queue, whose short-lap path (R-34) a deliberate manual
        deal must not bypass -- the entry is marked has_data, and an
        audit ``Event`` carrying *reason* lands. The card is
        **entry-scoped** (no rider tag): a bonus card is not a lap
        crossing, so a pooled rider's DNF never forfeits it, and the
        typed *plate* rides in the audit payload only. The card joins
        the credited sequence, so it obeys ``config.max_cards`` exactly
        like a crossing's card: a manual card past the cap is dealt but
        non-scoring (R-13). A
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
        return self._deal_manual_into(self._require_entry(plate), plate, reason)

    def _deal_manual_into(self, entry: Entry, plate: str, reason: str) -> Event:
        """Deal one bonus card to the already-resolved *entry*.

        The body of :meth:`deal_manual` past its plate resolution:
        ``deal_manual`` supplies the entry it resolved the typed plate
        to, and replay supplies the entry the payload's stable key names
        (``apply``). *plate* stays the operator's own typed value -- it
        rides in the audit payload -- while the credited hand is keyed
        by the entry's :attr:`~rivercrossing.roster.Entry.key`.
        """
        card = self._deal_card()
        # Entry-scoped, not rider-tagged: a bonus card is not a lap
        # crossing, so a pooled rider's DNF never forfeits it (the typed
        # plate rides in the audit payload only).
        self._credit(entry.key, card, None)
        self._roster.mark_has_data(entry)
        return self._append(
            Event(
                action="deal_manual",
                payload={
                    "plate": plate,
                    "entry_id": entry.key,
                    "card": card.code(),
                    "reason": reason,
                },
            )
        )

    # --------------------------------- E7.1.1 audited corrections

    # (entry, seq, crossed_at, reason): the correction's four fixed
    # fields
    def edit_crossing(  # noqa: PLR0913, PLR0917
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
            crossed_at: The corrected crossing instant; must be
                strictly after the lap before it (Phase 3).
            reason: Why the time was wrong; carried in the audit
                payload.

        Returns:
            The appended ``edit_crossing`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only, or
                *crossed_at* would give the crossing a zero or negative
                lap time (at or before the previous crossing, or
                ``actual_start`` for the earliest lap).
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                no crossing matches *entry_id*/*seq*.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot edit crossing from {self._state}")
        crossing = self._require_crossing(entry_id, seq)
        # A lap is measured from the crossing before it in the entry's
        # own time-ordered lap sequence -- lap 1 from the gun (spec §6)
        # -- so the refusal is exactly the lap time `lap_times` derives.
        laps = self._laps_for(crossing.entry_id)
        position = laps.index(crossing)
        previous = laps[position - 1].crossed_at if position > 0 else self._require_actual_start()
        # The refusal is operator copy, so it names the entry's plate --
        # never the internal stable key the crossing is filed under.
        owning = self._roster.entry_by_key(crossing.entry_id)
        _require_lap_after(
            owning.plate if owning is not None else crossing.entry_id, crossed_at, previous
        )
        replacement = Crossing(
            entry_id=crossing.entry_id,
            seq=crossing.seq,
            crossed_at=crossed_at,
            rider_plate=crossing.rider_plate,
        )
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
        self._discard_credited(crossing.entry_id, card)
        self._mark_voided(card)
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
        crossing's card comes off the same continuing deal order. A
        zero or negative lap time is refused (Phase 3): *crossed_at*
        must be strictly after the entry's own latest crossing, or
        after ``actual_start`` when the entry has none yet, so a
        back-filled correction can never manufacture a duplicate of a
        lap already on the line.

        Args:
            plate: The recorded plate, resolved like a crossing's
                (R-16).
            crossed_at: The explicit past crossing instant.
            reason: Why the crossing was missed; carried in the audit
                payload.

        Returns:
            The appended ``add_crossing_at`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only, or
                *crossed_at* is at or before the entry's latest
                crossing (``actual_start`` when it has none).
            IllegalStateError: the ride is not RUNNING or REOPENED.
            UnknownPlateError: *plate* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot add crossing from {self._state}")
        return self._add_crossing_at_into(self._require_entry(plate), plate, crossed_at, reason)

    def _add_crossing_at_into(  # noqa: PLR0913, PLR0917 -- (entry, plate, at, reason)
        self, entry: Entry, plate: str, crossed_at: datetime, reason: str
    ) -> Event:
        """Record a missed crossing on the already-resolved *entry*.

        The body of :meth:`add_crossing_at` past its plate resolution:
        ``add_crossing_at`` supplies the entry it resolved the typed
        plate to, and replay supplies the entry the payload's stable key
        names (``apply``). The Phase 3 lap-time gate still names
        *entry*'s plate in its refusal -- operator copy, never the
        internal stable key -- while the laps it counts are keyed by the
        entry's ``key``.
        """
        entry_key = entry.key
        laps = self._laps_for(entry_key)
        latest = laps[-1].crossed_at if laps else self._require_actual_start()
        _require_lap_after(entry.plate, crossed_at, latest)
        self._record_crossing_at(entry, crossed_at, rider_plate=plate)
        return self._append(
            Event(
                action="add_crossing_at",
                payload={
                    "plate": plate,
                    "entry_id": entry_key,
                    "crossed_at": crossed_at.isoformat(),
                    "reason": reason,
                },
            )
        )

    def assign_plate_to_miss(self, miss_seq: int, new_plate: str, reason: str) -> Event:
        """Resolve a pending miss to a real plate (K).

        The follow-up to :meth:`record_miss`: the operator identifies
        the rider whose number was missed, so the pending miss is
        removed (newest first) and the crossing is recorded -- *and its
        card dealt* -- at the miss's original ``crossed_at``. The card
        is deliberately assigned at edit time, never at miss time, so a
        miss held across a finish/reopen still deals from the live
        shoe. The crossing credits straight into the hand and never
        routes through R-34's hold queue (a correction, not live
        entry), and ``new_plate`` is resolved like a crossing's (R-16),
        so a rider_pooled member's plate attributes the team (J1).
        RUNNING or REOPENED only.

        Args:
            miss_seq: The pending miss's 1-based ``miss_seq``.
            new_plate: The now-known plate.
            reason: Why/how the number was recovered; carried in the
                audit payload.

        Returns:
            The appended ``assign_plate_to_miss`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED, or
                no pending miss matches *miss_seq*.
            UnknownPlateError: *new_plate* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot assign plate to miss from {self._state}")
        miss = self._pending_miss(miss_seq)
        return self._assign_plate_to_miss_into(
            self._require_entry(new_plate), miss, new_plate, reason
        )

    def _pending_miss(self, miss_seq: int) -> PendingMiss:
        """Return the pending miss *miss_seq* names, or raise.

        Raises:
            IllegalStateError: no pending miss matches *miss_seq*.
        """
        miss = next(
            (candidate for candidate in self._pending_misses if candidate.miss_seq == miss_seq),
            None,
        )
        if miss is None:
            raise IllegalStateError(f"no pending miss with miss_seq {miss_seq}")
        return miss

    def _assign_plate_to_miss_into(  # noqa: PLR0913, PLR0917 -- (entry, miss, plate, reason)
        self, entry: Entry, miss: PendingMiss, new_plate: str, reason: str
    ) -> Event:
        """Resolve the pending *miss* onto the already-resolved *entry*.

        The body of :meth:`assign_plate_to_miss` past its own
        resolution: ``assign_plate_to_miss`` supplies the miss it found
        and the entry it resolved *new_plate* to, and replay supplies
        the same pair -- the entry from the payload's stable key
        (``apply``). *new_plate* stays the operator's typed value (the
        audit payload and the crossing's rider attribution) and the
        recorded crossing is filed under the entry's ``key``.
        """
        self._pending_misses.remove(miss)
        self._record_crossing_at(entry, miss.crossed_at, rider_plate=new_plate)
        return self._append(
            Event(
                action="assign_plate_to_miss",
                payload={
                    "miss_seq": miss.miss_seq,
                    "new_plate": new_plate,
                    "entry_id": entry.key,
                    "crossed_at": miss.crossed_at.isoformat(),
                    "reason": reason,
                },
            )
        )

    def _record_crossing_at(
        self, entry: Entry, crossed_at: datetime, *, rider_plate: str | None = None
    ) -> Crossing:
        """Record one crossing at *crossed_at*, dealing a card.

        The shared deal+insert body of :meth:`add_crossing_at` and
        :meth:`assign_plate_to_miss`: the caller resolves *entry*, this
        method assigns the entry's next lap number, deals the next shoe
        card straight into the credited hand (a deliberate correction
        never routes through R-34's hold queue) and marks the entry
        has_data. *rider_plate* is the plate the operator typed -- the
        entry's own when omitted -- so a rider_pooled team's crossing
        still attributes the member who actually crossed (J1); the
        crossing itself, its laps and its credited hand are keyed by the
        entry's ``key``.
        """
        seq = len(self._laps_for(entry.key)) + 1
        card = self._deal_card()
        crossing = Crossing(
            entry_id=entry.key,
            seq=seq,
            crossed_at=crossed_at,
            rider_plate=rider_plate if rider_plate is not None else entry.plate,
        )
        self._insert_crossing(crossing)
        self._dealt[crossing] = card
        self._credit(entry.key, card, crossing.rider_plate)
        self._roster.mark_has_data(entry)
        return crossing

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
        return self._reassign_crossing_into(self._require_entry(new_plate), seq, new_plate, reason)

    def _reassign_crossing_into(  # noqa: PLR0913, PLR0917 -- (entry, seq, plate, reason)
        self, entry: Entry, seq: int, new_plate: str, reason: str
    ) -> Event:
        """Reattribute crossing *seq* to the already-resolved *entry*.

        The body of :meth:`reassign_crossing` past its plate
        resolution: ``reassign_crossing`` supplies the entry it
        resolved *new_plate* to, and replay supplies the entry the
        payload's stable key names (``apply``). Every identity here is
        a ``key`` -- the source entry the crossing leaves, the
        destination it joins -- while *new_plate* stays the operator's
        typed value on the crossing's rider attribution.
        """
        if not 1 <= seq <= len(self._crossings):
            raise IllegalStateError(f"no crossing at ordinal {seq}")
        crossing = self._crossings[seq - 1]
        old_entry_id = crossing.entry_id
        old_seq = crossing.seq
        card = self._dealt[crossing]
        held = self._held.pop(crossing, None)
        self._discard_credited(old_entry_id, card)
        self._dealt.pop(crossing)
        self._remove_crossing(crossing)
        self._renumber_later(old_entry_id, old_seq)
        new_seq = len(self._laps_for(entry.key)) + 1
        replacement = Crossing(
            entry_id=entry.key,
            seq=new_seq,
            crossed_at=crossing.crossed_at,
            rider_plate=new_plate,
        )
        self._insert_crossing(replacement)
        self._dealt[replacement] = card
        if held is not None:
            self._held[replacement] = held
        elif not self._is_voided(card):
            self._credit(entry.key, card, replacement.rider_plate)
        return self._append(
            Event(
                action="reassign",
                payload={
                    "seq": seq,
                    "old_entry_id": old_entry_id,
                    "new_entry_id": entry.key,
                    "new_plate": new_plate,
                    "reason": reason,
                },
            )
        )

    def mark_dnf(self, plate: str, reason: str) -> Event:
        """Mark one rider, or one whole entry, DNF (E7.1.1, spec §6).

        The operator's "did not finish" correction. *plate* resolves
        through the roster (R-16), and what it names decides the scope:
        a pooled team member's own number marks **that rider**, so the
        team stays in the results with its remaining riders' cards
        while the DNF rider's cards are forfeited (:meth:`snapshot`);
        every other plate -- a solo entry's, or a relay team's, whose
        riders carry no plate at all (S1) -- marks the whole entry, as
        it always has. A team comes out only when every rider of it is
        DNF. Every recorded lap and card is kept either way (spec §6).
        Reversible semantics (un-DNF) are the dialog's concern, not the
        engine's: this command only records the mark. RUNNING or
        REOPENED only.

        Args:
            plate: The rider number or entry plate to mark.
            reason: Why the rider did not finish; carried in the audit
                payload.

        Returns:
            The appended ``dnf`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is not RUNNING or REOPENED.
            UnknownPlateError: *plate* resolves to no entry.
        """
        _require_reason(reason)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot mark DNF from {self._state}")
        entry = self._require_entry(plate)
        # A team member's own number is the rider's, not the team's:
        # under rider_pooled the team's own plate is derived from its
        # lowest-numbered rider (S1), so a typed number that names a
        # member always scopes to that member. Solo riders are never
        # scoped this way -- their DNF *is* the entry's DNF.
        rider = entry.type.value == "team" and any(
            member.plate == plate for member in entry.riders
        )
        return self._record_dnf(entry, plate=plate, rider=rider, reason=reason)

    def _record_dnf(  # noqa: PLR0913 -- (entry, plate, rider, reason): the dnf event's four fields
        self, entry: Entry, *, plate: str, rider: bool, reason: str
    ) -> Event:
        """Set one DNF mark on *entry* and append its event.

        The shared body of :meth:`mark_dnf` and the replay of a
        persisted ``dnf`` event: *rider* True records the member's own
        plate in the per-rider set, False writes the entry's status.
        Both scopes audit the same payload shape -- the plate, the
        entry's stable key, the scope, the reason and the marked
        target's human display -- so replay rebuilds the identical
        state without re-deriving the scope from the roster.
        ``display`` is for the audit trail alone (the trail names the
        rider, never the internal entry id): :meth:`apply` reads the
        other four keys and re-derives a display of its own, so the
        extra key can never move the state it replays onto.
        """
        if rider:
            self._dnf_riders.add(plate)
        else:
            # Duck-typed status write: never import roster at runtime
            # (module docstring), and the member must be a real StrEnum
            # -- the store's save_roster reads ``entry.status.value``.
            entry.status = type(entry.status)("dnf")
        # The marked rider's own plate and name, or -- for a whole-entry
        # mark, whose riders may carry no plate at all (S1) -- the
        # entry's. The same "plate · name" sentence the DNF dialog names
        # its target with (ui.views.dialogs.dnf_message).
        member = next((item for item in entry.riders if item.plate == plate), None)
        name = member.full_name if member is not None else entry.display_name
        return self._append(
            Event(
                action="dnf",
                payload={
                    "entry_id": entry.key,
                    "plate": plate,
                    "rider": rider,
                    "reason": reason,
                    "display": f"{plate} · {name}",
                },
            )
        )

    def void_card(self, entry_id: str, card: Card, reason: str) -> Event:
        """Void one dealt card; its crossing and lap stay (E7.1.1).

        The operator's "wrong card off the line" correction: *card*
        leaves the entry's credited hand and its state becomes voided
        (spec §2 ``card.state``), while the crossing that dealt it --
        and therefore the lap -- stays recorded. The voided object is
        the engine's own dealt card, which is the one
        ``_discard_credited`` removed: *card* itself may be a fresh,
        value-equal ``Card`` (replay builds one with ``Card.parse``),
        so marking the caller's object would
        leave the dealt card live. A held card is refused: the
        short-lap hold is the review surface's domain
        (``confirm_held``/``void_held``), never this command -- the
        guard compares identity, so a same-code card in the hold queue
        never blocks the void of a different physical card. RUNNING or
        REOPENED only.

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
        return self._void_card_into(self._require_entry(entry_id), card, reason)

    def _void_card_into(self, entry: Entry, card: Card, reason: str) -> Event:
        """Void *card* from the already-resolved *entry*'s hand.

        The body of :meth:`void_card` past its plate resolution:
        ``void_card`` supplies the entry it resolved *entry_id* to, and
        replay supplies the entry the payload's stable key names
        (``apply``). The hold guard and the credited-hand removal both
        run against the entry's stable ``key``, so a re-plating between
        the deal and the void cannot strand the card.
        """
        entry_key = entry.key
        for crossing, held_card in self._held.items():
            if crossing.entry_id == entry_key and held_card is card:
                msg = "card is held; confirm or void it through the review panel"
                raise IllegalStateError(msg)
        removed = self._discard_credited(entry_key, card)
        if removed is None:
            raise IllegalStateError(f"no dealt card {card.code()} credited to {entry.plate}")
        self._mark_voided(removed)
        return self._append(
            Event(
                action="void_card",
                payload={"entry_id": entry_key, "card": card.code(), "reason": reason},
            )
        )

    # --------------------------------- E3.1.2 pooled live rider moves

    def move_rider(self, rider_plate: str, *, to_team: str, reason: str) -> Event:
        """Move one pooled rider onto another team mid-ride (R-17).

        The engine half of the pooled live move E3.1.2's lock matrix
        keeps open: the roster changes the rider's membership, and this
        method carries the rider's *data* across with them
        (:meth:`_reattribute_rider`) -- their live laps, their held
        card and their credited cards, re-keyed to the destination
        entry. The source may be a pooled TEAM member or a whole SOLO
        entry; a solo entry has exactly one rider, so a move out of one
        dissolves it (``Roster.move_rider``), which is why the audit
        event records the two *keys* rather than the two entries.

        Legal only while the ride is stopped (RUNNING with R-35's Stop
        guard set) or REOPENED, and only on a ``rider_pooled`` ride
        (:meth:`_require_move_allowed`): the operator stops the clock
        before a mid-ride re-shuffle, and a finished ride is corrected
        through Reopen.

        Args:
            rider_plate: The moving rider's own plate (R-16).
            to_team: The destination team's display name.
            reason: Why the rider moved; carried in the audit payload.

        Returns:
            The appended ``move_rider`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is RUNNING and not stopped, is
                DRAFT or FINISHED, its plate model is not
                ``rider_pooled``, or *to_team* is the rider's own team.
            UnknownPlateError: *rider_plate* names no rider in the
                roster, or *to_team* names no team.
        """
        self._require_move_allowed(reason)
        source, rider = self._find_rider(rider_plate)
        destination = self._require_team(to_team)
        if destination is source:
            raise IllegalStateError(f"rider {rider_plate} is already on team {to_team}")
        source_key = source.key
        destination_key = destination.key
        self._roster.move_rider(rider, to_entry=destination)
        self._roster.mark_has_data(destination)
        self._reattribute_rider(rider_plate, source_key, destination_key)
        return self._append(
            Event(
                action="move_rider",
                payload={
                    "rider_plate": rider_plate,
                    "from_key": source_key,
                    "to_team": to_team,
                    "to_key": destination_key,
                    "reason": reason,
                },
            )
        )

    def extract_rider_to_solo(self, rider_plate: str, *, reason: str) -> Event:
        """Move one pooled team member into their own solo entry (R-17).

        The team-to-solo half of the pooled live move: the roster
        extracts the member into a brand-new solo entry (its own plate
        becomes the entry's) and this method carries the rider's data
        onto that new entry's key. Gated exactly like
        :meth:`move_rider` (:meth:`_require_move_allowed`): stopped
        RUNNING or REOPENED, ``rider_pooled`` only.

        Args:
            rider_plate: The extracted rider's own plate (R-16).
            reason: Why the rider left the team; carried in the audit
                payload.

        Returns:
            The appended ``extract_rider_to_solo`` audit event.

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is RUNNING and not stopped, is
                DRAFT or FINISHED, or its plate model is not
                ``rider_pooled``.
            UnknownPlateError: *rider_plate* names no rider, or names
                a rider who is not on a pooled team.
        """
        self._require_move_allowed(reason)
        source, rider = self._find_rider(rider_plate)
        if source.type.value != "team":
            raise UnknownPlateError(f"plate {rider_plate} is not a pooled team member")
        source_key = source.key
        solo = self._roster.extract_rider_to_solo(rider)
        destination_key = solo.key
        self._roster.mark_has_data(solo)
        self._reattribute_rider(rider_plate, source_key, destination_key)
        return self._append(
            Event(
                action="extract_rider_to_solo",
                payload={
                    "rider_plate": rider_plate,
                    "from_key": source_key,
                    "to_key": destination_key,
                    "reason": reason,
                },
            )
        )

    def _require_move_allowed(self, reason: str) -> None:
        """Refuse a rider move the ride's state or model forbids.

        The one gate both move primitives share (E3.1.2, R-17): *reason*
        must name something, the ride must be stopped RUNNING (R-35's
        Stop guard -- the operator locks plate entry before a mid-ride
        re-shuffle) or REOPENED, and the ride must be ``rider_pooled``.
        A live RUNNING ride is refused with "Stop the ride first", the
        console's own instruction; DRAFT and FINISHED name their state.
        ``team_relay`` is refused outright: the plate *is* the team
        there, so a move would re-key identity itself -- the same
        carve-out ``roster.can_move_rider`` makes, read off the stored
        ``.value`` because this module never imports ``roster`` at
        runtime (module docstring).

        Raises:
            ValueError: *reason* is empty or whitespace-only.
            IllegalStateError: the ride is RUNNING and not stopped, is
                DRAFT or FINISHED, or its plate model is not
                ``rider_pooled``.
        """
        _require_reason(reason)
        if self._state is RideStatus.RUNNING and not self._stopped:
            msg = "Stop the ride first"
            raise IllegalStateError(msg)
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot move a rider from {self._state}")
        if self._config.plate_model.value != "rider_pooled":
            raise IllegalStateError(
                f"rider moves need a rider_pooled ride, not {self._config.plate_model.value}"
            )

    def _find_rider(self, rider_plate: str) -> tuple[Entry, Rider]:
        """Return the ``(entry, rider)`` *rider_plate* names, or raise.

        A rider lookup, never a plate resolution: a pooled team's own
        plate is *derived* from its lowest-numbered member (S1), so
        resolving *rider_plate* through the roster's plate index could
        return the team itself -- and a move's subject must be the
        member whose laps and cards travel. Duck-typed over
        ``entries``/``riders`` like every other roster read here.

        Raises:
            UnknownPlateError: no rider carries *rider_plate*.
        """
        for entry in self._roster.entries:
            for rider in entry.riders:
                if rider.plate == rider_plate:
                    return entry, rider
        raise UnknownPlateError(f"unknown plate: {rider_plate}")

    def _require_team(self, display_name: str) -> Entry:
        """Return the TEAM entry named *display_name*, or raise.

        The destination lookup :meth:`move_rider` needs and no plate
        resolution can serve: the operator picks a team by the name the
        entry list shows, and a pooled team's plate is exactly what a
        mid-ride move re-derives, so only the name names the target
        (E3.1.2's pooled-live-move seam).

        Raises:
            UnknownPlateError: *display_name* names no TEAM entry.
        """
        for entry in self._roster.entries:
            if entry.type.value == "team" and entry.display_name == display_name:
                return entry
        raise UnknownPlateError(f"unknown team: {display_name}")

    def _reattribute_rider(self, rider_plate: str, source_key: str, destination_key: str) -> None:
        """Re-key *rider_plate*'s ride data from one entry to another.

        The direction-agnostic core of every pooled move: the roster's
        membership change belongs to the caller (:meth:`move_rider`,
        :meth:`extract_rider_to_solo`, and their replay branches in
        :meth:`apply`), while the moved rider's *data* -- credited
        cards, live laps, restored voided laps -- follows them here. It
        takes **keys**, never ``Entry`` objects, so a replay can pass a
        ``from_key`` whose entry the live move dissolved: by the time
        the event is re-applied that entry no longer exists to hand
        over, and the recorded key is the only identity that survives.

        Three passes, each selecting the moved rider's own data alone:

        - **credited cards.** Every card the source hand tags with
          *rider_plate* moves to the destination hand, tag and all (a
          manual card's ``None`` tag and a teammate's tag stay put). The
          tag is what a per-rider DNF forfeits on, so it must travel.
        - **live laps.** In record order, each live crossing of
          *rider_plate* moves to the destination as its next lap, its
          dealt card (and its hold, when held) travelling with it --
          never a fresh deal, and never a re-credit: a credited card
          moved in the first pass and a voided one stays voided.
        - **voided laps.** A ``void_crossing`` void *of this rider* is
          reversed into the destination instead of staying void: the
          card is un-voided and the voided lap re-appends, its
          short-lap disposition (R-34) re-derived against its new
          predecessor because the pre-void one was never stored. Only
          the moved rider's own plate selects a void: a teammate's
          voided lap is none of this move's business.
        """
        source_hand = self._hand.get(source_key, [])
        staying = [(card, tag) for card, tag in source_hand if tag != rider_plate]
        travelling = [(card, tag) for card, tag in source_hand if tag == rider_plate]
        if travelling:
            self._hand[source_key] = staying
            self._hand.setdefault(destination_key, []).extend(travelling)

        moved = [
            crossing
            for crossing in self._crossings
            if crossing.entry_id == source_key and crossing.rider_plate == rider_plate
        ]
        removed_seqs: list[int] = []
        for crossing in moved:
            card = self._dealt.pop(crossing)
            held = self._held.pop(crossing, None)
            self._remove_crossing(crossing)
            removed_seqs.append(crossing.seq)
            replacement = Crossing(
                entry_id=destination_key,
                seq=len(self._laps_for(destination_key)) + 1,
                crossed_at=crossing.crossed_at,
                rider_plate=rider_plate,
            )
            self._insert_crossing(replacement)
            self._dealt[replacement] = card
            if held is not None:
                self._held[replacement] = held
        # The source renumbers last: _renumber_later re-keys each later
        # crossing through _replace_crossing, which carries the card to
        # the replacement -- and the crossing this loop still holds
        # would then have no _dealt entry left to look up. Descending
        # order is the only one that leaves the survivors contiguous
        # 1..N: each call closes the single gap above the seq it names.
        for seq in reversed(removed_seqs):
            self._renumber_later(source_key, seq)

        # Snapshot: the body takes every matching pair out of _voided.
        for pair in list(self._voided):
            crossing, card = pair
            if crossing.rider_plate != rider_plate:
                continue
            self._voided.remove(pair)
            self._unmark_voided(card)
            replacement = Crossing(
                entry_id=destination_key,
                seq=len(self._laps_for(destination_key)) + 1,
                crossed_at=crossing.crossed_at,
                rider_plate=rider_plate,
            )
            self._insert_crossing(replacement)
            self._dealt[replacement] = card
            laps = self._laps_for(destination_key)
            position = laps.index(replacement)
            previous = (
                laps[position - 1].crossed_at if position > 0 else self._require_actual_start()
            )
            lap_time = (replacement.crossed_at - previous).total_seconds()
            if self._config.hold_short_laps and lap_time < self._config.min_lap_s:
                self._held[replacement] = card
            else:
                self._credit(destination_key, card, rider_plate)

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
        stopped_at = self._clock()
        return self._append(
            Event(
                action="stop",
                payload={
                    "stopped_at": stopped_at.isoformat(),
                    # The ride clock's reading at the stop (scope 6d).
                    "reason": _format_elapsed(
                        (stopped_at - self._require_actual_start()).total_seconds()
                    ),
                },
            )
        )

    def finish(self, *, self_test_failed_checks: Sequence[str] = ()) -> Event:
        """Finish the ride: RUNNING or REOPENED to FINISHED (spec §3).

        Closing the shoe here (spec §4, task-briefs E2.2.1)
        locks every later deal while the ride stays FINISHED; the
        ``ShoeClosedError`` contract holds for a ride that is still
        FINISHED, and only ``reopen()`` opens the shoe again.
        ``undo_last`` of an existing crossing stays legal in REOPENED,
        where the re-opened shoe's restitution returns the undone card
        to the front.

        The finish instant is recorded as :attr:`_finished_at` (C3) so
        a closed ride's final elapsed (:meth:`closed_elapsed`) never
        drifts with the live clock.

        The venue's high-card draw is recorded here too, once the finish
        instant is in (:meth:`_record_tiebreak_draws`, R-14): the
        returned event is still the finish's own, and the draw's event
        follows it. :meth:`apply` re-applies a persisted finish through
        :meth:`_finish_at` alone -- never through this method -- so a
        replay restores the draws from the ``tiebreak_draw`` event
        instead of drawing them twice.

        Args:
            self_test_failed_checks: The E6.4.3 override -- the names
                of the evaluator self-test's failed BLOCKING checks,
                when the operator chose to finish anyway. Empty for a
                clean finish, which is the only case with no override
                to record.

        Raises:
            IllegalStateError: the ride is DRAFT or already FINISHED.
        """
        if self._state not in (RideStatus.RUNNING, RideStatus.REOPENED):
            raise IllegalStateError(f"cannot finish from {self._state}")
        event = self._finish_at(self._clock(), self_test_failed_checks=self_test_failed_checks)
        self._record_tiebreak_draws()
        return event

    def _finish_at(
        self, finished_at: datetime, *, self_test_failed_checks: Sequence[str] = ()
    ) -> Event:
        """Move to FINISHED, recording *finished_at*.

        Shared by :meth:`finish` (stamping the current clock) and the
        replay seam (:meth:`apply`), which re-applies the persisted
        instant so a replayed old ride keeps its own finish time.

        *self_test_failed_checks* is written to the payload only when
        it names something: a clean finish's row, and every finish row
        persisted before E6.4.3, then replays byte-identically, while a
        non-empty list is what marks the finished ride's results
        self-test unverified.
        """
        self._state = RideStatus.FINISHED
        self._roster.status = RideStatus.FINISHED
        self._stopped = False
        self._finished_at = finished_at
        self._shoe.close()
        payload: dict[str, object] = {
            "finished_at": finished_at.isoformat(),
            # The recorded final elapsed (C3's own instant, not
            # the replay clock) -- scope 6d.
            "reason": _format_elapsed(
                (finished_at - self._require_actual_start()).total_seconds()
            ),
        }
        if self_test_failed_checks:
            payload["self_test_failed_checks"] = list(self_test_failed_checks)
        return self._append(Event(action="finish", payload=payload))

    def reopen(self) -> Event:
        """Reopen a finished ride for corrections (spec §3, R-64).

        REOPENED is corrections-only, not RUNNING: the clock stays
        closed and live plate entry stays off; ``finish()`` re-locks.
        Re-opening also re-opens the shoe (``Shoe.reopen``, spec §15):
        the closed state from Finish is not sticky, so the corrections
        commands (``deal_manual``, ``add_crossing_at``) deal new cards
        from the continuing deal order. The recorded finish instant is
        preserved (C3), so :meth:`closed_elapsed` still reads it here.

        Raises:
            IllegalStateError: the ride is not FINISHED.
        """
        if self._state is not RideStatus.FINISHED:
            raise IllegalStateError(f"cannot reopen from {self._state}")
        return self._reopen_at(self._clock())

    def _reopen_at(self, reopened_at: datetime) -> Event:
        """Move to REOPENED, re-opening the shoe.

        Shared by :meth:`reopen` (stamping the current clock) and the
        replay seam (:meth:`apply`), which re-applies the persisted
        instant. The finish instant is untouched.

        R-14's recorded draws are cleared: corrections are about to
        change the field's hands, so the next finish draws afresh rather
        than carrying a card the corrected ride may no longer be tied
        for.
        """
        self._state = RideStatus.REOPENED
        self._roster.status = RideStatus.REOPENED
        self._stopped = False
        self._tiebreak.clear()
        self._shoe.reopen()
        return self._append(
            Event(
                action="reopen",
                payload={
                    "reopened_at": reopened_at.isoformat(),
                    # Measured to the reopen, not to the frozen finish
                    # (scope 6d).
                    "reason": _format_elapsed(
                        (reopened_at - self._require_actual_start()).total_seconds()
                    ),
                },
            )
        )

    def closed_elapsed(self) -> float:
        """Return the recorded final elapsed of a closed ride (C3).

        FINISHED and REOPENED render a frozen clock: this is
        ``finished_at - actual_start`` from the recorded instant, with
        zero when the ride never started or was never finished. The
        console reads it instead of the live :meth:`elapsed`, so a
        closed ride's display never advances.
        """
        if self._finished_at is None or self._actual_start is None:
            return 0.0
        return (self._finished_at - self._actual_start).total_seconds()

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
        *entry_id* is the entry's stable
        :attr:`~rivercrossing.roster.Entry.key` -- the identity its
        crossings are filed under -- and an unknown key has no laps, so
        it returns the empty tuple.
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
        (unconfirmed) and voided cards never reach the hand. A DNF mark
        (``mark_dnf``, E7.1.1) excludes its subject from the results:
        ``dnf=True`` for an entry that is out -- its own status, or a
        team every one of whose riders is DNF -- and a DNF team
        member's cards are forfeited, so only the survivors' cards
        score. ``standings`` then drops DNF results outright; the
        leaderboards already excluded them. ``sex`` (E7) is a solo
        entry's lone rider's ``"M"``/``"F"`` and None for a team: a
        team has no single sex, so the exports render it blank there.
        ``tiebreak_card`` is the entry's recorded high-card draw (R-14),
        ``None`` until a finish has drawn one (or a replayed
        ``tiebreak_draw`` has restored it), which is exactly the
        undrawn reading ``standings`` flags "draw required".
        """
        results: list[EntryResult] = []
        for entry in self._roster.entries:
            dnf = self.entry_is_dnf(entry)
            kind = entry.type.value
            laps = self._laps_for(entry.key)
            times = self.lap_times(entry.key)
            cards = self._scoring_cards(entry.key)
            if self._config.max_cards is not None:
                cards = cards[: self._config.max_cards]
            results.append(
                EntryResult(
                    entry_id=entry.key,
                    plate=entry.plate,
                    name=entry.display_name,
                    kind=kind,
                    laps=len(laps),
                    total_time=self._total_time(laps),
                    best_lap=min(times) if times else 0.0,
                    cards=cards,
                    hand=best_hand(cards),
                    dnf=dnf,
                    # Solo carries its lone rider's sex; a team has none
                    # (module docstring records the roster non-import).
                    sex=entry.riders[0].sex if kind == "solo" else None,
                    # R-14: this entry's own recorded draw card, or None
                    # while the draw has not happened (yet).
                    tiebreak_card=self._tiebreak.get(entry.key),
                )
            )
        return results

    def _equal_hand_groups(self) -> list[list[EntryResult]]:
        """Group the ACTIVE snapshot entries by exactly equal hand.

        One group per set of two or more ACTIVE entries whose hands
        compare equal (:func:`rivercrossing.hands.compare`), in the
        first-appearance order of the group's earliest entry and in the
        snapshot's own roster order within a group -- the order
        :meth:`_record_tiebreak_draws` hands the drawn cards out in. A
        DNF entry is excluded outright: ``standings`` drops it from the
        results, so it can never be part of a tie that needs breaking,
        and an entry whose hand ties no other is dropped here too (no
        draw can break a group of one).
        """
        groups: list[list[EntryResult]] = []
        for result in self.snapshot():
            if result.dnf:
                continue
            for group in groups:
                if compare(group[0].hand, result.hand) == 0:
                    group.append(result)
                    break
            else:
                groups.append([result])
        return [group for group in groups if len(group) > 1]

    def _record_tiebreak_draws(self) -> None:
        """Draw and record the venue's high-card tie-break (R-14).

        Called by :meth:`finish` once the finish instant is recorded:
        every group of equal hands (:meth:`_equal_hand_groups`) draws
        one card per entry from *one* fresh deck --
        :func:`rivercrossing.cards.high_card_draw` under the shoe's
        stored seed salted with :data:`_TIEBREAK_DRAW_SEED_XOR`, so the
        draw replays from the persisted ``rng_seed`` (R-40) and never
        deals from the shoe itself. Groups come out in first-appearance
        order and entries in roster order, so the cards go out in that
        order and the whole draw is reproducible.

        The stored cards land in :attr:`_tiebreak` (read by
        :meth:`snapshot`) and in a ``tiebreak_draw`` event whose
        ``draws`` rows are JSON-ready card codes; ``summary`` names each
        entry's card by its plate, because the audit viewer's Entry cell
        reads a single ``entry_id``/``plate`` and cannot project a row
        list -- and the row's ``entry_id`` is the entry's stable key
        (a uuid would mean nothing to the viewer, so the human summary
        is what it shows). A
        ride with no equal-hand group appends nothing. One fresh deck
        holds 52 naturals, so a field with more tied entries than that
        takes the cards the deck holds and the rest stay undrawn --
        which ``standings`` reads as "draw required", never as a silent
        order.
        """
        groups = self._equal_hand_groups()
        if not groups:
            return
        seed = self._shoe.seed ^ _TIEBREAK_DRAW_SEED_XOR
        cards = high_card_draw(seed, sum(map(len, groups)))
        tied_entries = [result for group in groups for result in group]
        draws: list[dict[str, str]] = []
        labels: list[str] = []
        for result, card in zip(tied_entries, cards, strict=False):
            self._tiebreak[result.entry_id] = card
            draws.append({"entry_id": result.entry_id, "card": card.code()})
            labels.append(f"{result.plate} · {card.code()}")
        self._append(
            Event(
                action="tiebreak_draw",
                payload={"draws": draws, "summary": ", ".join(labels)},
            )
        )

    def _total_time(self, laps: tuple[Crossing, ...]) -> float:
        """Return the last crossing minus ``actual_start`` (spec §6)."""
        if not laps:
            return 0.0
        return (laps[-1].crossed_at - self._require_actual_start()).total_seconds()

    def _deal_card(self) -> Card:
        """Deal the next shoe card, reshuffling + auditing on ShoeEmpty.

        spec §4/R-40: an empty shoe reshuffles (seed+1) and the caller
        writes the reshuffle's own audit entry -- here that entry lands
        before the crossing's own ``record_crossing`` event. The entry
        carries the cycle it opened, the jokers it re-dealt
        (``Shoe.jokers_in_cycle``, read after the reshuffle) and the
        reason the audit trail draws from those two (scope 6d).
        """
        try:
            card, _deal_index = self._shoe.deal()
        except ShoeEmpty:
            self._shoe.reshuffle()
            jokers_added = self._shoe.jokers_in_cycle
            self._append(
                Event(
                    action="shoe_reshuffle",
                    payload={
                        "cycle": self._shoe.cycle,
                        "jokers_added": jokers_added,
                        "reason": f"{jokers_added} jokers added",
                    },
                )
            )
            card, _deal_index = self._shoe.deal()
        return card

    def _require_entry(self, plate: str) -> Entry:
        """Return the entry *plate* resolves to (R-16), or raise.

        The one plate->entry resolution every correction command shares
        (``deal_manual``/``add_crossing_at``/``assign_plate_to_miss``/
        ``reassign_crossing``/``mark_dnf``/``void_card``);
        ``record_crossing`` keeps its own refusal result instead, since
        a live mis-key must not raise into the console.

        Raises:
            UnknownPlateError: *plate* resolves to no entry.
        """
        entry = self._roster.resolve_plate(plate)
        if entry is None:
            raise UnknownPlateError(f"unknown plate: {plate}")
        return entry

    def _require_entry_by_key(self, key: str) -> Entry:
        """Return the entry *key* names, or raise.

        The replay seam's own resolution: a persisted payload carries
        the entry's stable :attr:`~rivercrossing.roster.Entry.key`
        (E3.1.2's pooled-live-move seam), so ``apply`` rebuilds the
        recorded entry by the identity that was recorded -- never by
        re-resolving the payload's plate against the roster it is
        replaying onto, which a mid-ride re-plating would take to a
        different team.

        Raises:
            UnknownPlateError: *key* names no entry in this roster.
        """
        entry = self._roster.entry_by_key(key)
        if entry is None:
            raise UnknownPlateError(f"unknown entry key: {key}")
        return entry

    def entry_is_dnf(self, entry: Entry) -> bool:
        """Return whether *entry* is out of the results entirely.

        An entry-level DNF (spec §2's stored ``entry.status``) covers
        the solo and relay cases. A pooled team is out only when every
        one of its riders is -- the marker lives in this engine, never
        written back to the roster, because a rider's DNF has no roster
        column to live in (Phase 3). A riderless entry (a transient
        empty team) is never all-DNF.

        Public because the console's crossings feed is a wx-free
        presenter module with a roster of its own: it marks a row DNF
        from the rider's plate (:attr:`dnf_riders`) or from this
        verdict, and must not restate the entry-level rule.
        """
        if entry.status.value == "dnf":  # spec §2's stored spelling
            return True
        return bool(entry.riders) and all(
            member.plate in self._dnf_riders for member in entry.riders
        )

    def _scoring_cards(self, entry_id: str) -> tuple[Card, ...]:
        """Return *entry_id*'s credited cards that still score.

        A DNF rider forfeits exactly the cards their own crossings
        dealt -- the credited hand's rider tag (``_credit``) is what
        separates a pooled team's members; a card with no rider tag is
        never forfeited. The cards themselves stay credited
        (:meth:`credited_cards` still lists them for the console).
        """
        return tuple(
            card
            for card, rider_plate in self._hand.get(entry_id, ())
            if rider_plate not in self._dnf_riders
        )

    def _credit(self, entry_id: str, card: Card, rider_plate: str | None) -> None:
        """Add *card* to *entry_id*'s credited hand.

        *rider_plate* tags the card -- the plate the operator typed for
        the crossing that dealt it (:attr:`Crossing.rider_plate`), which
        is exactly what a per-rider DNF forfeits on.
        """
        self._hand.setdefault(entry_id, []).append((card, rider_plate))

    def is_card_voided(self, card: Card) -> bool:
        """Return whether this engine voided *card* (E7.1.1, R-34).

        The public read of the voided-card registry, for every consumer
        that is not the engine's own correction paths. Identity, never
        value: ``Card`` is a frozen value dataclass, so a value test
        would report a same-code card -- a different physical card --
        as voided too. Only the engine's own dealt object can be in the
        registry, so a caller's fresh ``Card.parse`` copy reads
        ``False`` by design: ask
        :meth:`card_for` for the object the engine dealt.
        """
        return self._is_voided(card)

    def _is_voided(self, card: Card) -> bool:
        """Return whether *card* is this engine's own voided object.

        The identity rule ``void_card``'s hold guard,
        ``reassign_crossing``'s gate and ``return_to_held`` all share:
        the registry is keyed by ``id(card)``, never by value, because
        the shoe builds a distinct-but-equal ``Card`` per deck.
        """
        return id(card) in self._voided_cards

    def _mark_voided(self, card: Card) -> None:
        """Register *card* -- the engine's own dealt object -- voided.

        The dict stores the card itself, not just its id, so the
        registry keeps every voided card alive and no later object can
        be handed a retired ``id``.
        """
        self._voided_cards[id(card)] = card

    def _unmark_voided(self, card: Card) -> None:
        """Drop *card* from the voided registry; absent is a no-op.

        ``undo_last`` reverses a void by un-voiding the card it has
        just taken back, and most undone cards were never voided, so a
        missing entry is the normal case, not an error.
        """
        self._voided_cards.pop(id(card), None)

    def _discard_credited(self, entry_id: str, card: Card) -> Card | None:
        """Remove one *card* from *entry_id*'s credited hand.

        The undo/void/reassign reversal of :meth:`_credit`: the first
        credited card matching *card* leaves the hand and is returned
        -- the engine's own object, which is what the void paths mark
        with :meth:`_mark_voided`. ``None`` when no credited card
        matches, so ``void_card``'s own refusal can name the missing
        card.

        The match is by *value*, deliberately, next to the registry's
        identity rule: ``_hand`` carries no crossing id, so two
        same-code cards credited to one entry cannot be told apart here
        and the first match goes. Live and replay credit in the same
        order, so both pick the same card and equivalence holds.
        """
        hand = self._hand.get(entry_id)
        if hand is None:
            return None
        for index, (credited, _rider_plate) in enumerate(hand):
            if credited == card:
                del hand[index]
                return credited
        return None

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
        (``edit_crossing`` re-times it, ``_renumber_later`` renumbers
        the laps a void left behind): the crossing's dealt card -- and
        its hold, when held -- travel to the replacement so the deal
        accounting never drifts. The replacement is always same-entry:
        every caller swaps within *old*'s own entry, and the per-entry
        index takes the new crossing in *old*'s slot (stable re-sort
        only when the time changed, so tied instants keep record
        order). ``reassign_crossing`` moves a crossing to another
        entry through ``_remove_crossing`` + ``_insert_crossing``
        instead, never this method.
        """
        index = self._crossings.index(old)
        self._crossings[index] = new
        card = self._dealt.pop(old)
        self._dealt[new] = card
        held = self._held.pop(old, None)
        if held is not None:
            self._held[new] = held
        laps = self._laps[old.entry_id]
        position = laps.index(old)
        laps[position] = new
        if new.crossed_at != old.crossed_at:
            laps.sort(key=lambda c: c.crossed_at)

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
                    crossing,
                    Crossing(
                        crossing.entry_id,
                        crossing.seq - 1,
                        crossing.crossed_at,
                        rider_plate=crossing.rider_plate,
                    ),
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

    # The replay dispatch is inherently one branch per action (22
    # mutations + the unknown-action guard); the cyclomatic and
    # statement counts are the event vocabulary's size, not a
    # refactorable control-flow tangle.
    def apply(self, event: Event) -> None:  # noqa: C901, PLR0912, PLR0915
        """Replay one previously-recorded event onto this engine.

        The store's replay seam: :class:`~rivercrossing.store.
        Store.load_engine` builds a fresh DRAFT engine and calls this
        for every persisted event, oldest first, to reach the exact
        live state. Dispatch calls the matching mutation with the
        payload's own values, so the re-appended event equals the
        original for every action that takes an explicit timestamp
        (``start``/``set_start_time``/``record_crossing`` and the
        identity-based holds); a ``start`` row is re-applied through
        :meth:`_begin_start` directly -- never through :meth:`start`'s
        readiness gates, which a live start already cleared -- so a
        persisted running ride rebuilds even when its stored state
        would not pass today's gates (an empty roster, say). The Phase 3
        lap-time gate is *not* bypassed that way: a replayed
        ``edit_crossing``/``add_crossing_at`` goes through the public
        command, so a stored row whose ``crossed_at`` yields a zero or
        negative lap time is refused (class docstring's Phase 3
        resolutions).
        ``stop`` re-stamps its payload timestamp from the engine's
        clock, so its audit bytes differ by design on replay (class
        docstring's E5.1.2 resolutions); ``finish``/``reopen``
        re-apply the persisted instant through :meth:`_finish_at`/
        :meth:`_reopen_at`, keeping an old ride's recorded finish time
        (C3). A replayed ``finish`` also re-applies its own
        ``self_test_failed_checks`` (E6.4.3), so a reloaded overridden
        ride's results stay marked self-test unverified; the venue's
        high-card draw arrives as its own ``tiebreak_draw`` row, which
        rebuilds from that payload's cards rather than redrawing them
        (R-14). ``continue`` from a replayed REOPENED ride is legal
        (:meth:`start` accepts it). ``dnf`` replays its payload's own
        scope, never a roster re-resolve, because a rider's DNF has no
        roster column to come back from. The shoe's open/closed state
        is part of the reproduced state: ``finish`` closes the fresh
        shoe and a replayed ``reopen`` opens it again, exactly as live.
        Every replayed subject that names an entry is located by the
        payload's ``entry_id`` -- the entry's stable
        :attr:`~rivercrossing.roster.Entry.key` (E3.1.2's
        pooled-live-move seam) -- through :meth:`_require_entry_by_key`,
        never by re-resolving a plate against the roster being rebuilt:
        a ride saved after a mid-ride rider move has re-derived both
        teams' plates, so the plate the operator typed on the night is
        exactly what no longer names the team the crossing belongs to.

        Args:
            event: The event to re-apply, exactly as persisted.

        Raises:
            UnknownEventActionError: *event.action* is not a known
                ride mutation.
            ValueError: a replayed correction's reason is empty, or a
                replayed ``edit_crossing``/``add_crossing_at`` violates
                Phase 3's zero/negative-lap gate.
            UnknownPlateError: a replayed payload's ``entry_id`` names
                no entry in this roster (an inconsistent event stream).
            RideEngineError: ``confirm_held``/``void_held``/
                ``return_to_held`` name a crossing this engine never
                recorded (an inconsistent event stream).
        """
        action = event.action
        if action == "start":
            self._begin_start(_payload_dt(event, "actual_start"))
        elif action == "continue":
            self.start()
        elif action == "set_start_time":
            self.set_start_time(_payload_dt(event, "actual_start"))
        elif action == "record_crossing":
            self._record_crossing_into(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                str(event.payload["plate"]),
                _payload_dt(event, "crossed_at"),
            )
        elif action == "confirm_held":
            self.confirm_held(self._crossing_from(event))
        elif action == "void_held":
            self.void_held(self._crossing_from(event))
        elif action == "return_to_held":
            self.return_to_held(self._crossing_from(event))
        elif action == "undo":
            self.undo_last()
        elif action == "deal_manual":
            self._deal_manual_into(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                str(event.payload["plate"]),
                str(event.payload["reason"]),
            )
        elif action == "edit_crossing":
            self.edit_crossing(
                str(event.payload["entry_id"]),
                _payload_int(event, "seq"),
                _payload_dt(event, "crossed_at"),
                reason=str(event.payload["reason"]),
            )
        elif action == "void_crossing":
            self.void_crossing(
                str(event.payload["entry_id"]),
                _payload_int(event, "seq"),
                reason=str(event.payload["reason"]),
            )
        elif action == "add_crossing_at":
            self._add_crossing_at_into(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                str(event.payload["plate"]),
                _payload_dt(event, "crossed_at"),
                str(event.payload["reason"]),
            )
        elif action == "record_miss":
            self.record_miss(_payload_dt(event, "crossed_at"), reason=str(event.payload["reason"]))
        elif action == "assign_plate_to_miss":
            self._assign_plate_to_miss_into(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                self._pending_miss(_payload_int(event, "miss_seq")),
                str(event.payload["new_plate"]),
                str(event.payload["reason"]),
            )
        elif action == "reassign":
            self._reassign_crossing_into(
                self._require_entry_by_key(str(event.payload["new_entry_id"])),
                _payload_int(event, "seq"),
                str(event.payload["new_plate"]),
                str(event.payload["reason"]),
            )
        elif action == "dnf":
            # The payload's own scope replays -- the entry is located by
            # its stable key like every other replayed subject, but
            # whether the mark was a rider's or the entry's is never
            # re-derived from the roster: a rider DNF has no roster
            # column to come back from, and a member may have changed
            # teams since. The
            # payload's audit-only ``display`` is never read either: the
            # replayed event re-derives its own.
            self._record_dnf(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                plate=str(event.payload["plate"]),
                rider=bool(event.payload["rider"]),
                reason=str(event.payload["reason"]),
            )
        elif action == "void_card":
            self._void_card_into(
                self._require_entry_by_key(str(event.payload["entry_id"])),
                Card.parse(str(event.payload["card"])),
                reason=str(event.payload["reason"]),
            )
        elif action == "move_rider":
            # The roster is never re-mutated on replay: it is already
            # final (it is rebuilt from the entry/rider tables, not
            # from this log), so only the rider's data follows the
            # from/to keys the move recorded -- the one identity a
            # dissolved source entry cannot be re-derived from. The
            # payload's ``to_team``/``reason`` are operator copy, never
            # state.
            self._reattribute_rider(
                str(event.payload["rider_plate"]),
                str(event.payload["from_key"]),
                str(event.payload["to_key"]),
            )
            self._append(event)
        elif action == "extract_rider_to_solo":
            # Same seam as ``move_rider``: the extracted rider's solo
            # entry is already in the replayed roster, so the event
            # restores the data under the recorded destination key and
            # re-appends its own row.
            self._reattribute_rider(
                str(event.payload["rider_plate"]),
                str(event.payload["from_key"]),
                str(event.payload["to_key"]),
            )
            self._append(event)
        elif action == "stop":
            self.stop()
        elif action == "finish":
            self._finish_at(
                _payload_dt(event, "finished_at"),
                self_test_failed_checks=_payload_strings(event, "self_test_failed_checks"),
            )
        elif action == "reopen":
            self._reopen_at(_payload_dt(event, "reopened_at"))
        elif action == "tiebreak_draw":
            # R-14's draw rebuilds verbatim from its own rows -- the
            # cards are never redrawn -- and the row re-appends, so a
            # reloaded ride's snapshot and audit trail keep exactly the
            # cards the venue drew.
            self._tiebreak = _payload_draws(event)
            self._append(event)
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
        seq = _payload_int(event, "seq")
        for crossing in self._crossings:
            if crossing.entry_id == entry_id and crossing.seq == seq:
                return crossing
        raise RideEngineError(f"no crossing with entry_id {entry_id} seq {seq} for {event.action}")
