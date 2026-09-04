# SPDX-License-Identifier: GPL-3.0-only
"""Roster models: entries, riders and their hard rules (spec S1-S2).

An **entry** is a solo rider or, in a mixed ride, a team of 2..
``max_team_size`` (<=10, default 4) riders sharing one placing
(R-11/R-12). A ride's **plate model** decides where the plate lives
(R-16): ``team_relay`` gives the entry one plate and its riders
none; ``rider_pooled`` (the default) gives every rider a unique
plate, with the entry's own plate derived -- the rider's own plate
for a solo entry, the lowest-numbered rider's plate for a team.
Every entry's and pooled rider's plate shares one namespace per
ride (R-20).

This module is a store-less, in-memory model of that shape: EPIC 5's
Store is the persistence layer these dataclasses feed once it lands
(module-skeletons.md S4's ``schema.py`` names the same ``entries``/
``riders`` tables this module's fields mirror). :class:`Roster`
enforces the hard rules -- plate uniqueness, team size, plate shape,
solo-only rides -- and appends one :class:`AuditEvent` per mutation.
State-based editability (DRAFT free edit, the relay start lock, when
a pooled move is allowed, and the permanent has-data delete guard) is
this module's lock matrix (E3.1.2, R-15/R-17): ``can_edit_structure``,
``can_delete_entry``, ``can_move_rider``, ``can_add_entry`` and
``can_fix_name``, consulted by :meth:`Roster.delete_entry` and
:meth:`Roster.move_rider`.

E3.2's 2026-08-09 follow-on decision relaxes the 2..max_team_size
floor to a start-time check: rider_editor_dlg adds one rider at a
time, so ``create_team_entry_of_one`` and ``move_rider`` allow a
transient size-1 team while DRAFT (the last rider leaving one
dissolves it); ``validate_for_start`` reports every team still
below the floor. The same decision makes plates editable in DRAFT
for a solo entry (``change_solo_plate``), a pooled team member
(``change_pooled_rider_plate``) or a relay team
(``change_team_plate`` -- spec S3:46's "fully editable" derived
rule, no separate decision needed).

The pooled-reshape follow-on closes csvio's two remaining gaps
(spec S1's "move riders ... convert solo <-> team ... before
start"): ``add_rider_to_team`` attaches a brand-new or currently-
solo rider straight onto a team, and ``extract_rider_to_solo``
converts a pooled team member into their own solo entry.

``Entry`` and ``Rider`` compare by identity, not by field value
(``eq=False``): they are living records a caller holds a reference
to across renames and moves, not interchangeable values like
:class:`rivercrossing.cards.Card` -- two same-named riders on a
relay ride, both plate-less, must never compare equal to each other.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from rivercrossing.cards import seeded_card_codes
from rivercrossing.ride import RideStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "DEFAULT_MAX_TEAM_SIZE",
    "MAX_TEAM_SIZE_LIMIT",
    "MIN_TEAM_SIZE",
    "AuditEvent",
    "DuplicatePlateError",
    "Entry",
    "EntryMode",
    "EntryNotFoundError",
    "EntryStatus",
    "EntryType",
    "InvalidMoveError",
    "LockedError",
    "PlateModel",
    "PlateShapeError",
    "Rider",
    "RiderNotFoundError",
    "Roster",
    "RosterError",
    "SoloOnlyRideError",
    "StartViolation",
    "TeamSizeError",
    "can_add_entry",
    "can_delete_entry",
    "can_edit_structure",
    "can_fix_name",
    "can_move_rider",
]

MIN_TEAM_SIZE = 2
MAX_TEAM_SIZE_LIMIT = 10
DEFAULT_MAX_TEAM_SIZE = 4


class EntryMode(StrEnum):
    """A ride's team policy (spec S1, R-11): the stored enum values.

    New rides default to solo-only; team fields and columns stay
    hidden in the UI until "Solo + teams" is chosen.
    """

    SOLO = "solo"
    MIXED = "mixed"


class PlateModel(StrEnum):
    """A mixed ride's plate policy (spec S1, R-16): the stored values.

    ``RIDER_POOLED`` is the default: every rider carries a unique
    plate, uncapped per rider, and the team's hand pools their
    cards. ``TEAM_RELAY`` is the EPIC's own format: one plate per
    entry, one rider on course at a time.
    """

    RIDER_POOLED = "rider_pooled"
    TEAM_RELAY = "team_relay"


class EntryType(StrEnum):
    """An entry's kind (spec S2 ``entry.type``): the stored values."""

    SOLO = "solo"
    TEAM = "team"


class EntryStatus(StrEnum):
    """An entry's standing (spec S2 ``entry.status``): stored values.

    Setting DNF is a live-ride (``RideEngine``) concern outside this
    module's scope; the field exists here only so a freshly created
    entry has the correct default.
    """

    ACTIVE = "active"
    DNF = "dnf"


class RosterError(Exception):
    """Base for every roster invariant violation this module raises."""


class DuplicatePlateError(RosterError):
    """A plate collides with another entry's or rider's plate (R-20)."""


class TeamSizeError(RosterError):
    """A team size, or max_team_size itself, is outside 2..10 (R-12)."""


class SoloOnlyRideError(RosterError):
    """A team entry was attempted on a solo-only ride (R-11)."""


class PlateShapeError(RosterError):
    """A plate value violates the ride's plate_model shape (S1)."""


class EntryNotFoundError(RosterError):
    """The referenced entry is not a member of this roster."""


class RiderNotFoundError(RosterError):
    """The referenced rider is not on any entry in this roster."""


class InvalidMoveError(RosterError):
    """move_rider() would violate a structural entry invariant."""


class LockedError(RosterError):
    """A mutation is refused by the lock matrix (E3.1.2, R-15/R-17).

    Raised by :meth:`Roster.delete_entry` once the ride has left
    DRAFT, or when the entry already carries recorded data in any
    state, and by :meth:`Roster.move_rider` whenever the current
    (status, plate_model) cell of the lock matrix forbids the move.
    """


@dataclass(eq=False)
class Rider:
    """One rider on an entry (spec S2 ``rider`` table).

    ``first_name`` and ``last_name`` split what was a single ``name``
    field (Phase 1 rider-name split); :attr:`full_name` is the display
    projection every caller shows or audits -- a solo entry's
    ``display_name`` mirrors its rider's ``full_name``. ``plate``
    holds this rider's own plate only under
    ``PlateModel.RIDER_POOLED``; under ``PlateModel.TEAM_RELAY`` it
    always ends up ``None`` -- the plate belongs to the entry, not
    the individual rider (S1).
    """

    first_name: str
    last_name: str = ""
    plate: str | None = None
    sort_order: int = 0

    @property
    def full_name(self) -> str:
        """Return this rider's display name, first and last joined.

        A single-word rider (no last name) renders as just the first
        name, never ``"Alex "`` with a trailing space; stray blanks
        around either part collapse so the two never double-space.
        """
        return " ".join(part for part in (self.first_name.strip(), self.last_name.strip()) if part)


@dataclass(eq=False)
class Entry:
    """One roster entry (spec S2 ``entry`` table): solo rider or team.

    ``plate`` is always populated: directly, under
    ``PlateModel.TEAM_RELAY``, or derived from ``riders`` under
    ``PlateModel.RIDER_POOLED`` (S1). ``riders`` holds exactly one
    member for a solo entry, two or more for a team. ``has_data`` is
    E3.1.2's permanent delete guard (R-15): once
    :meth:`Roster.mark_has_data` sets it, ``delete_entry`` refuses in
    every ride state -- DNF or void is the only path from there.
    ``logo_card``/``logo_png`` are Phase 4's team logo: a natural
    card code and/or image bytes, set only through
    :meth:`Roster.set_team_logo_card`/:meth:`Roster.set_team_logo_image`
    (an image wins over a card -- either clears the other).
    """

    plate: str
    display_name: str
    type: EntryType
    riders: list[Rider] = field(default_factory=list)
    status: EntryStatus = EntryStatus.ACTIVE
    notes: str = ""
    has_data: bool = False
    logo_card: str | None = None
    logo_png: bytes | None = None

    @property
    def team_size(self) -> int:
        """Return this entry's current rider count."""
        return len(self.riders)


@dataclass(frozen=True)
class AuditEvent:
    """One append-only audit record (spec S2 ``audit`` table shape).

    ``action`` names the mutation; ``payload`` carries whatever that
    mutation needs to explain it later. EPIC 5's Store persists
    these; this module only appends and exposes them read-only.
    """

    action: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class StartViolation:
    """One reason :meth:`Roster.validate_for_start` refuses a start.

    ``entry`` is the exact offending :class:`Entry` -- not a plate or
    name snapshot -- so a caller can trace straight back to the row
    that needs fixing (E4's start gate, E3.3's CSV commit).
    """

    entry: Entry
    reason: str


def _lowest_plate(plates: Iterable[str]) -> str:
    """Return the numerically lowest of *plates* (S1's "adopts...")."""
    return min(plates, key=int)


def can_edit_structure(status: RideStatus) -> bool:
    """Return True when structure edits may proceed (R-15).

    Membership, plate and type edits, and delete, are DRAFT-only,
    for either plate model (spec S3).
    """
    return status is RideStatus.DRAFT


def can_delete_entry(status: RideStatus, *, has_data: bool) -> bool:
    """Return True when delete_entry() may remove the entry (R-15).

    An entry carrying recorded data is never deletable, in any
    state; otherwise deletion follows the same DRAFT-only rule as
    any other structural edit.
    """
    if has_data:
        return False
    return can_edit_structure(status)


def can_move_rider(status: RideStatus, plate_model: PlateModel) -> bool:
    """Return True when move_rider() may relocate a rider (R-17).

    DRAFT allows every move, for either plate model. Once started,
    ``team_relay`` keeps the start lock permanently -- the plate
    *is* the team's identity -- while ``rider_pooled`` stays open
    for audited moves while RUNNING and, as a correction, once
    REOPENED; FINISHED closes that door until reopened.
    """
    if status is RideStatus.DRAFT:
        return True
    if plate_model is PlateModel.TEAM_RELAY:
        return False
    return status in (RideStatus.RUNNING, RideStatus.REOPENED)


def can_add_entry() -> bool:
    """Return True: a new plate may be entered in any ride state.

    xrc-windows.md: "ride open (new plates any time)".
    """
    return True


def can_fix_name() -> bool:
    """Return True: a name-spelling fix is allowed in any state.

    Spec S3's "name fixes" stay open regardless of ride status.
    """
    return True


class Roster:
    """The entries and riders of one ride, with S1/S2's hard rules.

    ``entry_mode``, ``max_team_size`` and ``plate_model`` are the
    ride-wide settings spec S1 and R-11/R-12/R-16 describe; every
    mutation validates against them and appends one
    :class:`AuditEvent` to :attr:`audit_log`. ``status`` (default
    DRAFT) is this ride's lifecycle state -- E4's engine owns which
    transitions are legal; this class only reads it, through the
    module-level lock matrix, to decide what :meth:`delete_entry` and
    :meth:`move_rider` currently allow (E3.1.2, R-15/R-17). This is a
    store-less, in-memory model -- EPIC 5's Store persists it.
    """

    def __init__(  # noqa: PLR0913 -- four keyword-only ride settings; bundle with care
        self,
        *,
        entry_mode: EntryMode = EntryMode.SOLO,
        max_team_size: int = DEFAULT_MAX_TEAM_SIZE,
        plate_model: PlateModel = PlateModel.RIDER_POOLED,
        team_logo_seed: int | None = None,
    ) -> None:
        """Build an empty roster under the given ride-wide settings.

        Args:
            entry_mode: The ride's team policy (R-11).
            max_team_size: The ride's configured max riders per team.
            plate_model: The ride's plate policy (R-16).
            team_logo_seed: Phase 4's team-logo shuffle seed (a
                ride's stored ``rng_seed``); when given, each new
                team auto-assigns the next unused natural card code
                from :func:`~rivercrossing.cards.seeded_card_codes`
                as its logo (:meth:`next_team_logo_card`). Without
                one, teams carry no auto-assigned logo.

        Raises:
            TeamSizeError: *max_team_size* is outside 2..10.
        """
        if not MIN_TEAM_SIZE <= max_team_size <= MAX_TEAM_SIZE_LIMIT:
            msg = (
                f"max_team_size must be between {MIN_TEAM_SIZE} and "
                f"{MAX_TEAM_SIZE_LIMIT}, got {max_team_size}"
            )
            raise TeamSizeError(msg)
        self._entry_mode = entry_mode
        self._max_team_size = max_team_size
        self._plate_model = plate_model
        self._status = RideStatus.DRAFT
        self._entries: list[Entry] = []
        self._audit_log: list[AuditEvent] = []
        self._team_logo_codes = (
            seeded_card_codes(team_logo_seed) if team_logo_seed is not None else None
        )

    @property
    def entry_mode(self) -> EntryMode:
        """Return this ride's team policy (R-11)."""
        return self._entry_mode

    @property
    def max_team_size(self) -> int:
        """Return this ride's configured max riders per team (R-12)."""
        return self._max_team_size

    @property
    def plate_model(self) -> PlateModel:
        """Return this ride's plate policy (R-16)."""
        return self._plate_model

    @property
    def status(self) -> RideStatus:
        """Return this roster's ride-lifecycle state (spec S3)."""
        return self._status

    @status.setter
    def status(self, value: RideStatus) -> None:
        """Set this roster's ride-lifecycle state.

        Mechanics only: E4's ride engine owns which transitions are
        legal (spec S3); this setter just records the current state
        for the lock matrix to consult.
        """
        self._status = value

    @property
    def entries(self) -> tuple[Entry, ...]:
        """Return every entry, in creation order, read-only."""
        return tuple(self._entries)

    @property
    def audit_log(self) -> tuple[AuditEvent, ...]:
        """Return every audit event, oldest first, read-only."""
        return tuple(self._audit_log)

    def load_entries(self, entries: Sequence[Entry]) -> None:
        """Restore persisted entries wholesale (EPIC 5's store seam).

        Reconstruction, not mutation: appends *entries* with no
        validation and no audit logging -- the rows came from a
        consistent persisted state (Store.save_roster wrote them), and
        re-logging a restore as a live edit would pollute this
        roster's own audit_log with create events that never happened.
        :meth:`Store._load_roster` calls this to rebuild a ride's
        field from the entry/rider tables.

        Args:
            entries: The entries to restore, in creation order.
        """
        self._entries.extend(entries)

    def next_team_logo_card(self, after: str | None = None) -> str | None:
        """Return the next unused seeded logo card at/after *after*.

        Phase 4's logo source, shared by auto-assignment at team
        creation and the editor's Pick card cycling: the next code in
        this roster's deterministic ``team_logo_seed`` sequence that
        no current team's entry already holds as ``logo_card``. With
        *after* (a team's current card), the scan starts just past it
        and wraps around the sequence end, so repeated picks walk the
        whole deck without ever offering a card another team shows.

        Returns:
            The next unused code, or ``None`` when this roster has no
            ``team_logo_seed`` or all 52 codes are already claimed.
        """
        if self._team_logo_codes is None:
            return None
        codes = self._team_logo_codes
        if after is not None and after in codes:
            start = codes.index(after) + 1
            ordered = codes[start:] + codes[:start]
        else:
            ordered = codes
        in_use = {entry.logo_card for entry in self._entries if entry.logo_card is not None}
        return next((code for code in ordered if code not in in_use), None)

    def set_team_logo_card(self, entry: Entry, *, code: str) -> None:
        """Set *entry*'s logo to the natural card *code* (Phase 4).

        A picked card wins over an image: any ``logo_png`` on *entry*
        is cleared, so the two logo forms never both display.

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
        """
        self._require_known_entry(entry)
        entry.logo_card = code
        entry.logo_png = None
        self._log("set_team_logo_card", {"plate": entry.plate, "code": code})

    def set_team_logo_image(self, entry: Entry, *, image: bytes) -> None:
        """Set *entry*'s logo to the image *image* (Phase 4).

        An image wins over a card: any ``logo_card`` on *entry* is
        cleared, so the two logo forms never both display.

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
        """
        self._require_known_entry(entry)
        entry.logo_png = image
        entry.logo_card = None
        self._log("set_team_logo_image", {"plate": entry.plate})

    def next_free_plate(self) -> str:
        """Return one past the highest numeric plate in use (R-20).

        Non-numeric plates are ignored; an empty roster returns
        ``"1"``.
        """
        numeric = [int(plate) for plate in self._plates_in_use() if plate.isdigit()]
        return str(max(numeric, default=0) + 1)

    def resolve_plate(self, plate: str) -> Entry | None:
        """Return the entry *plate* names, or None if unknown (R-20).

        E4's ride engine resolves a recorded plate to its entry
        through this one method: an entry's own plate always matches,
        and on a ``rider_pooled`` ride a rider's own plate resolves to
        their entry (a team member's crossing credits the team, R-16).
        ``team_relay`` riders carry no plate (S1), so only the entry's
        own plate ever matches there.
        """
        for entry in self._entries:
            if entry.plate == plate:
                return entry
            if self._plate_model is PlateModel.RIDER_POOLED:
                for rider in entry.riders:
                    if rider.plate == plate:
                        return entry
        return None

    def validate_for_start(self) -> list[StartViolation]:
        """Return every reason this roster is not ready to start.

        The one rule the 2026-08-09 follow-on decision defers to
        start time: a team below :data:`MIN_TEAM_SIZE` riders,
        transiently allowed in DRAFT (:meth:`create_team_entry_of_one`,
        :meth:`move_rider`) but never once the ride starts. The
        upper bound is never deferred -- :meth:`create_team_entry`
        and :meth:`move_rider` both enforce it immediately -- so it
        needs no check here. E4's start gate and E3.3's CSV commit
        call this before their own transition; nothing else is
        checked.
        """
        return [
            StartViolation(
                entry=entry,
                reason=f"team size must be at least {MIN_TEAM_SIZE}, got {entry.team_size}",
            )
            for entry in self._entries
            if entry.type is EntryType.TEAM and entry.team_size < MIN_TEAM_SIZE
        ]

    def create_solo_entry(self, *, first_name: str, last_name: str = "", plate: str) -> Entry:
        """Create a solo entry for *first_name*/*last_name*.

        Plated per S1's plate model; the entry's ``display_name``
        mirrors the rider's :attr:`Rider.full_name`.

        Raises:
            DuplicatePlateError: *plate* collides with an existing
                entry's or rider's plate.
        """
        rider = Rider(first_name=first_name, last_name=last_name, plate=plate)
        entry_plate = self._shape_and_validate([rider], plate)
        entry = Entry(
            plate=entry_plate, display_name=rider.full_name, type=EntryType.SOLO, riders=[rider]
        )
        self._entries.append(entry)
        self._log("create_solo_entry", {"plate": entry.plate, "name": rider.full_name})
        return entry

    def create_team_entry(  # noqa: PLR0913 -- (display_name, riders, plate, logo_card), keyword-only
        self,
        *,
        display_name: str,
        riders: Sequence[Rider],
        plate: str | None = None,
        logo_card: str | None = None,
    ) -> Entry:
        """Create a team of 2..max_team_size riders (S1, R-12/R-16).

        *logo_card* (Phase 4) names the team's logo card when the
        caller has one to give (CSV previews, tests); ``None``
        auto-assigns the next unused code from this roster's
        ``team_logo_seed`` sequence -- never a code another team
        already holds.

        Raises:
            SoloOnlyRideError: this ride's entry_mode is solo-only.
            TeamSizeError: *riders* falls outside 2..max_team_size.
            PlateShapeError: *riders*/*plate* violate the ride's
                plate_model shape.
            DuplicatePlateError: a resolved plate collides with an
                existing entry's or rider's plate.
        """
        if self._entry_mode is EntryMode.SOLO:
            msg = "this ride is solo-only; team entries are not allowed"
            raise SoloOnlyRideError(msg)
        team_riders = list(riders)
        if not MIN_TEAM_SIZE <= len(team_riders) <= self._max_team_size:
            msg = (
                f"team size must be between {MIN_TEAM_SIZE} and "
                f"{self._max_team_size}, got {len(team_riders)}"
            )
            raise TeamSizeError(msg)
        entry_plate = self._shape_and_validate(team_riders, plate)
        entry = Entry(
            plate=entry_plate,
            display_name=display_name,
            type=EntryType.TEAM,
            riders=team_riders,
            logo_card=self._resolved_logo_card(logo_card),
        )
        self._entries.append(entry)
        self._log(
            "create_team_entry",
            {"plate": entry.plate, "display_name": display_name, "team_size": len(team_riders)},
        )
        return entry

    def create_team_entry_of_one(  # noqa: PLR0913 -- (display_name, rider, plate, logo_card), keyword-only
        self,
        *,
        display_name: str,
        rider: Rider,
        plate: str | None = None,
        logo_card: str | None = None,
    ) -> Entry:
        """Create a team of exactly one rider -- transient, DRAFT only.

        rider_editor_dlg's Add/Save form carries a single rider at a
        time; the 2026-08-09 follow-on decision permits this
        transient size-1 team while the ride stays in DRAFT,
        deferring R-12's 2..max_team_size floor to a start-time
        check (:meth:`validate_for_start`) rather than a construction
        invariant. :meth:`create_team_entry` (the CSV bulk path)
        keeps its own 2..max_team_size contract, unchanged.

        *logo_card* (Phase 4) is the caller-supplied team logo, the
        same shape as :meth:`create_team_entry`'s own -- ``None``
        auto-assigns the next unused seeded code.

        Raises:
            LockedError: the ride has left DRAFT.
            SoloOnlyRideError: this ride's entry_mode is solo-only.
            PlateShapeError: *rider*/*plate* violate the ride's
                plate_model shape.
            DuplicatePlateError: a resolved plate collides with an
                existing entry's or rider's plate.
        """
        if not can_edit_structure(self._status):
            msg = f"a new team cannot be started once the ride is {self._status}"
            raise LockedError(msg)
        if self._entry_mode is EntryMode.SOLO:
            msg = "this ride is solo-only; team entries are not allowed"
            raise SoloOnlyRideError(msg)
        entry_plate = self._shape_and_validate([rider], plate)
        entry = Entry(
            plate=entry_plate,
            display_name=display_name,
            type=EntryType.TEAM,
            riders=[rider],
            logo_card=self._resolved_logo_card(logo_card),
        )
        self._entries.append(entry)
        self._log(
            "create_team_entry_of_one",
            {"plate": entry.plate, "display_name": display_name, "team_size": 1},
        )
        return entry

    def update_entry(
        self, entry: Entry, *, display_name: str | None = None, notes: str | None = None
    ) -> None:
        """Rename an entry and/or edit its notes (mechanics only).

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
        """
        self._require_known_entry(entry)
        payload: dict[str, object] = {"plate": entry.plate}
        if display_name is not None:
            entry.display_name = display_name
            payload["display_name"] = display_name
        if notes is not None:
            entry.notes = notes
            payload["notes"] = notes
        self._log("update_entry", payload)

    def change_solo_plate(self, entry: Entry, *, plate: str) -> None:
        """Change a solo entry's plate, in either plate model (R-20).

        ``team_relay``: sets the entry's own plate directly -- its
        one rider stays plateless (S1). ``rider_pooled``: sets the
        rider's plate too, so entry and rider stay in lock-step.
        Plates lock at start along with membership (spec S3:46).

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
            LockedError: the ride has left DRAFT.
            PlateShapeError: *entry* is not type SOLO.
            DuplicatePlateError: *plate* collides with an existing
                entry's or rider's plate.
        """
        self._require_known_entry(entry)
        if not can_edit_structure(self._status):
            msg = f"plates cannot be changed once the ride is {self._status}"
            raise LockedError(msg)
        if entry.type is not EntryType.SOLO:
            msg = "change_solo_plate requires a solo entry"
            raise PlateShapeError(msg)
        old_plate = entry.plate
        self._require_plate_free_for_change(plate, exclude=old_plate)
        entry.plate = plate
        if self._plate_model is PlateModel.RIDER_POOLED:
            entry.riders[0].plate = plate
        self._log(
            "change_solo_plate",
            {"display_name": entry.display_name, "old_plate": old_plate, "new_plate": plate},
        )

    def change_pooled_rider_plate(self, rider: Rider, *, plate: str) -> None:
        """Change one rider_pooled team member's own plate (S1, R-20).

        Recomputes the owning team's derived plate afterwards --
        S1's "adopts the lowest-numbered rider's plate." A solo
        entry's own rider is out of scope here; use
        :meth:`change_solo_plate` instead.

        Raises:
            RiderNotFoundError: *rider* is not on any entry here.
            LockedError: the ride has left DRAFT.
            PlateShapeError: this ride's plate_model is not
                rider_pooled, or *rider* is not on a team member.
            DuplicatePlateError: *plate* collides with an existing
                entry's or rider's plate.
        """
        entry = self._find_owning_entry(rider)
        if entry is None:
            msg = "rider is not on any entry in this roster"
            raise RiderNotFoundError(msg)
        if not can_edit_structure(self._status):
            msg = f"plates cannot be changed once the ride is {self._status}"
            raise LockedError(msg)
        if self._plate_model is not PlateModel.RIDER_POOLED or entry.type is not EntryType.TEAM:
            msg = "change_pooled_rider_plate requires a rider_pooled team member"
            raise PlateShapeError(msg)
        old_plate = cast("str", rider.plate)
        self._require_plate_free_for_change(plate, exclude=old_plate)
        rider.plate = plate
        self._recompute_pooled_plate(entry)
        self._log(
            "change_pooled_rider_plate",
            {"rider_name": rider.full_name, "old_plate": old_plate, "new_plate": plate},
        )

    def change_team_plate(self, entry: Entry, *, plate: str) -> None:
        """Change a team_relay team's plate (S1, R-20, spec S3:46).

        A ``rider_pooled`` team has no settable plate of its own --
        it is derived from its riders' plates; change a member's
        own plate with :meth:`change_pooled_rider_plate` instead. A
        solo entry's plate goes through :meth:`change_solo_plate`.

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
            LockedError: the ride has left DRAFT.
            PlateShapeError: *entry* is not type TEAM, or this
                ride's plate_model is not team_relay.
            DuplicatePlateError: *plate* collides with an existing
                entry's or rider's plate.
        """
        self._require_known_entry(entry)
        if not can_edit_structure(self._status):
            msg = f"plates cannot be changed once the ride is {self._status}"
            raise LockedError(msg)
        if entry.type is not EntryType.TEAM:
            msg = "change_team_plate requires a team entry; use change_solo_plate"
            raise PlateShapeError(msg)
        if self._plate_model is not PlateModel.TEAM_RELAY:
            msg = (
                "a rider_pooled team's plate is derived from its riders; "
                "use change_pooled_rider_plate instead"
            )
            raise PlateShapeError(msg)
        old_plate = entry.plate
        self._require_plate_free_for_change(plate, exclude=old_plate)
        entry.plate = plate
        self._log(
            "change_team_plate",
            {"display_name": entry.display_name, "old_plate": old_plate, "new_plate": plate},
        )

    def delete_entry(self, entry: Entry) -> None:
        """Delete *entry* if the lock matrix currently allows it.

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
            LockedError: *entry* carries recorded data (DNF or void
                it instead), or the ride has left DRAFT (E3.1.2,
                R-15).
        """
        self._require_known_entry(entry)
        if not can_delete_entry(self._status, has_data=entry.has_data):
            raise LockedError(self._delete_refusal(entry))
        self._entries.remove(entry)
        self._log("delete_entry", {"plate": entry.plate, "display_name": entry.display_name})

    def mark_has_data(self, entry: Entry) -> None:
        """Flag *entry* as carrying recorded data (E3.1.2, R-15).

        E4's ride engine calls this once an entry's first crossing or
        card lands; from that point ``delete_entry`` refuses in every
        ride state -- DNF or void becomes the only path.

        Raises:
            EntryNotFoundError: *entry* is not a member of this
                roster.
        """
        self._require_known_entry(entry)
        entry.has_data = True
        self._log("mark_has_data", {"plate": entry.plate})

    def move_rider(self, rider: Rider, *, to_entry: Entry) -> None:
        """Move *rider* onto *to_entry* if the lock matrix allows it.

        Both entries must be type TEAM -- a solo entry's one rider
        is fixed by definition (S1) -- and the move must keep the
        destination within max_team_size (R-12). The source team's
        lower bound is a start-time check now
        (:meth:`validate_for_start`), not a move_rider invariant
        (2026-08-09 follow-on decision): dropping to a transient
        size-1 team succeeds; dropping its last rider dissolves the
        now-empty entry outright (:meth:`_dissolve_entry`) rather
        than leaving a size-0 team with no plate owner (spec S2).
        Whether a move may even be attempted depends on the ride's
        current (status, plate_model) cell of the lock matrix
        (E3.1.2, R-17): :func:`can_move_rider`.

        Raises:
            RiderNotFoundError: *rider* is not on any entry here.
            EntryNotFoundError: *to_entry* is not a member of this
                roster.
            LockedError: the lock matrix forbids a move in the
                ride's current state and plate model.
            InvalidMoveError: either entry is not type TEAM, or the
                move would exceed the destination's max size.
        """
        from_entry = self._find_owning_entry(rider)
        if from_entry is None:
            msg = "rider is not on any entry in this roster"
            raise RiderNotFoundError(msg)
        self._require_known_entry(to_entry)
        if not can_move_rider(self._status, self._plate_model):
            msg = f"rider moves are locked for a {self._plate_model} ride once {self._status}"
            raise LockedError(msg)
        if from_entry.type is not EntryType.TEAM or to_entry.type is not EntryType.TEAM:
            msg = "move_rider requires both entries to be team entries"
            raise InvalidMoveError(msg)
        if len(to_entry.riders) + 1 > self._max_team_size:
            msg = "move would exceed the destination team's max size"
            raise InvalidMoveError(msg)

        from_entry.riders.remove(rider)
        to_entry.riders.append(rider)
        if from_entry.riders:
            self._recompute_pooled_plate(from_entry)
        self._recompute_pooled_plate(to_entry)
        self._log(
            "move_rider",
            {
                "rider_name": rider.full_name,
                "from_plate": from_entry.plate,
                "to_plate": to_entry.plate,
            },
        )
        if not from_entry.riders:
            self._dissolve_entry(from_entry)

    def add_rider_to_team(self, rider: Rider, *, to_entry: Entry) -> None:
        """Attach *rider*, not yet on any entry, onto *to_entry*.

        Closes csvio's two pooled-reshape gaps (spec S7:171/177): a
        brand-new plate landing straight on a team, and a
        currently-solo rider joining one. Gated by
        :func:`can_move_rider` alone -- its own DRAFT branch already
        covers every case :func:`can_edit_structure` would add.
        ``rider_pooled`` requires *rider* to already carry a unique
        plate and recomputes the team's adopted lowest plate;
        ``team_relay`` clears *rider* to plateless (S1).

        Raises:
            InvalidMoveError: *rider* is already on some entry (use
                :meth:`move_rider` instead), or *to_entry* is not
                type TEAM.
            EntryNotFoundError: *to_entry* is not a member of this
                roster.
            LockedError: the lock matrix forbids the add in the
                ride's current state and plate model.
            TeamSizeError: the add would exceed max_team_size.
            PlateShapeError: rider_pooled and *rider* carries no
                plate.
            DuplicatePlateError: *rider*'s plate collides with an
                existing entry's or rider's plate.
        """
        if self._find_owning_entry(rider) is not None:
            msg = "rider is already on an entry; use move_rider instead"
            raise InvalidMoveError(msg)
        self._require_known_entry(to_entry)
        if not can_move_rider(self._status, self._plate_model):
            msg = f"rider moves are locked for a {self._plate_model} ride once {self._status}"
            raise LockedError(msg)
        if to_entry.type is not EntryType.TEAM:
            msg = "add_rider_to_team requires a team entry"
            raise InvalidMoveError(msg)
        if len(to_entry.riders) + 1 > self._max_team_size:
            msg = (
                f"team size must be at most {self._max_team_size}, got {len(to_entry.riders) + 1}"
            )
            raise TeamSizeError(msg)
        if self._plate_model is PlateModel.RIDER_POOLED:
            if rider.plate is None:
                msg = "rider_pooled riders must each carry a plate"
                raise PlateShapeError(msg)
            self._require_plates_free([rider.plate])
        else:
            rider.plate = None
        to_entry.riders.append(rider)
        self._recompute_pooled_plate(to_entry)
        self._log(
            "add_rider_to_team",
            {
                "rider_name": rider.full_name,
                "rider_plate": rider.plate,
                "to_plate": to_entry.plate,
                "display_name": to_entry.display_name,
            },
        )

    def extract_rider_to_solo(self, rider: Rider) -> Entry:
        """Convert a rider_pooled team member into their own solo entry.

        Spec S1 scopes team<->solo conversions to pre-start; R-17's
        running carve-out covers only moves between teams, not this.
        *rider*'s own plate becomes the new entry's; the source team
        recomputes its adopted plate, and dissolves outright if
        *rider* was its last member (:meth:`_dissolve_entry`).

        Raises:
            RiderNotFoundError: *rider* is not on any entry here.
            LockedError: the ride has left DRAFT.
            PlateShapeError: this ride's plate_model is not
                rider_pooled, or *rider*'s entry is not type TEAM.
        """
        entry = self._find_owning_entry(rider)
        if entry is None:
            msg = "rider is not on any entry in this roster"
            raise RiderNotFoundError(msg)
        if not can_edit_structure(self._status):
            msg = f"a rider cannot be extracted to solo once the ride is {self._status}"
            raise LockedError(msg)
        if self._plate_model is not PlateModel.RIDER_POOLED or entry.type is not EntryType.TEAM:
            msg = "extract_rider_to_solo requires a rider_pooled team member"
            raise PlateShapeError(msg)
        entry.riders.remove(rider)
        solo = Entry(
            plate=cast("str", rider.plate),
            display_name=rider.full_name,
            type=EntryType.SOLO,
            riders=[rider],
        )
        self._entries.append(solo)
        if entry.riders:
            self._recompute_pooled_plate(entry)
        self._log(
            "extract_rider_to_solo",
            {
                "rider_name": rider.full_name,
                "plate": solo.plate,
                "from_display_name": entry.display_name,
            },
        )
        if not entry.riders:
            self._dissolve_entry(entry)
        return solo

    def _shape_and_validate(self, riders: Sequence[Rider], plate: str | None) -> str:
        """Resolve riders'/plate's shape; return the entry's plate.

        Applies S1's plate-model rule for both solo and team
        entries: team_relay clears every rider's plate and requires
        *plate*; rider_pooled requires every rider to already carry
        one and derives the entry's plate as the lowest-numbered.

        Raises:
            PlateShapeError: the given riders/plate do not fit the
                ride's plate_model.
            DuplicatePlateError: the resolved plate(s) collide with
                an existing entry's or rider's plate.
        """
        if self._plate_model is PlateModel.TEAM_RELAY:
            if plate is None:
                msg = "team_relay entries require an explicit plate"
                raise PlateShapeError(msg)
            for rider in riders:
                rider.plate = None
            self._require_plates_free([plate])
            return plate

        if any(rider.plate is None for rider in riders):
            msg = "rider_pooled riders must each carry a plate"
            raise PlateShapeError(msg)
        rider_plates = [cast("str", rider.plate) for rider in riders]
        self._require_plates_free(rider_plates)
        return _lowest_plate(rider_plates)

    def _plates_in_use(self) -> set[str]:
        """Return every plate currently claimed in this roster."""
        in_use: set[str] = set()
        for entry in self._entries:
            in_use.add(entry.plate)
            in_use.update(rider.plate for rider in entry.riders if rider.plate is not None)
        return in_use

    def _require_plates_free(self, plates: Iterable[str]) -> None:
        """Raise if any of *plates* is already in use here or repeated.

        Raises:
            DuplicatePlateError: a plate is already claimed, or two
                of *plates* repeat each other.
        """
        in_use = self._plates_in_use()
        seen: set[str] = set()
        for plate in plates:
            # logic-coverage-exempt: T-13 -- the (plate in in_use AND
            # plate in seen) row is unreachable by construction.
            # in_use is fixed before this loop starts, so any plate
            # value that satisfies "in in_use" always raises on its
            # own first occurrence, before that same value could ever
            # have been added to "seen" for a later occurrence of it
            # to observe. The two conditions cannot both be True for
            # one evaluation of this line.
            if plate in in_use or plate in seen:
                msg = f"plate {plate!r} is already in use"
                raise DuplicatePlateError(msg)
            seen.add(plate)

    def _delete_refusal(self, entry: Entry) -> str:
        """Return why *entry* is currently undeletable (R-15)."""
        if entry.has_data:
            return "entry has recorded data; DNF or void it instead of deleting"
        return f"entries can no longer be deleted once the ride is {self._status}"

    def _dissolve_entry(self, entry: Entry) -> None:
        """Remove *entry* once move_rider has emptied it (E3.2).

        An empty team has no plate owner and no size-0 representation
        (spec S2); it ceases to exist rather than lingering as an
        empty row in :attr:`entries`.
        """
        self._entries.remove(entry)
        self._log(
            "dissolve_team_entry", {"plate": entry.plate, "display_name": entry.display_name}
        )

    def _require_plate_free_for_change(self, new_plate: str, *, exclude: str) -> None:
        """Raise unless *new_plate* is free, or equal to *exclude*.

        *exclude* is the plate's own current value: setting a plate
        back to itself is a harmless no-op, not a collision with its
        own prior claim.
        """
        if new_plate != exclude:
            self._require_plates_free([new_plate])

    def _resolved_logo_card(self, supplied: str | None) -> str | None:
        """Return *supplied*, else the next unused auto-assigned code.

        ``None`` both ways when the caller supplied none and this
        roster carries no ``team_logo_seed`` (or every code is
        claimed) -- a caller-supplied card is always kept verbatim.
        """
        if supplied is not None:
            return supplied
        return self.next_team_logo_card()

    def _require_known_entry(self, entry: Entry) -> None:
        """Raise EntryNotFoundError unless *entry* is a member here."""
        if entry not in self._entries:
            msg = "entry is not a member of this roster"
            raise EntryNotFoundError(msg)

    def _find_owning_entry(self, rider: Rider) -> Entry | None:
        """Return the entry *rider* is on, or None if none."""
        for entry in self._entries:
            if rider in entry.riders:
                return entry
        return None

    def _recompute_pooled_plate(self, entry: Entry) -> None:
        """Re-derive *entry*'s plate from its riders (pooled)."""
        if self._plate_model is PlateModel.RIDER_POOLED:
            entry.plate = _lowest_plate(cast("str", r.plate) for r in entry.riders)

    def _log(self, action: str, payload: Mapping[str, object]) -> None:
        """Append one audit event naming *action* and its *payload*."""
        self._audit_log.append(AuditEvent(action=action, payload=payload))
