# SPDX-License-Identifier: GPL-3.0-only
"""CSV roster import: preview-then-commit (spec S7, R-21, E3.3.1-2).

Spec section 7 defines two CSV shapes, selected by the target ride's
plate model (S1): ``team_relay``'s
``plate,entry_name,type,rider_1..rider_N,notes`` (N =
``ride.max_team_size``, ``type`` one of ``solo``/``teamN``) or
``rider_pooled``'s one-row-per-rider ``plate,name,team_name,notes``,
where riders sharing a ``team_name`` form a team (blank = solo).
:func:`preview` reads either shape and reports every conflict found
without raising for content problems; :func:`commit` then applies a
conflict-free preview to its roster, atomically (spec S7's own
words: "preview first, commit second, nothing touched on preview").

Only a truly unreadable *path* (e.g. missing file) propagates as an
``OSError``; a malformed or wrong-shaped CSV is reported as one or
more :class:`ImportConflict` rows instead ("a missing/malformed
header is a conflict, not a crash").

This module's ``ride`` parameter -- the frozen name from
module-skeletons.md's ``csvio.preview(path, ride) -> ImportPreview``
-- is, for now, :class:`rivercrossing.roster.Roster`: EPIC 5's Store
("6 csvio -> store models" in that same doc) does not exist yet in
EPIC 3's build order, so csvio operates directly on the in-memory
roster aggregate until persistence lands. A design write-back should
record this once Store arrives.

**Match/insert/reshape (R-21, spec S7:173-177).** ``commit`` matches
a row on plate: an existing plate updates that entry's name/notes in
place; a new plate inserts. A row's ride-model composition -- a
relay entry's ``type``/rider list, a pooled rider's team_name -- may
also *reshape* an existing match, applying every change through the
roster's own mutators (so it is fully audit-logged) subject to the
same lock matrix E3.1.2 already governs edits with: DRAFT reshapes
freely; once started, relay keeps its permanent lock, while pooled
keeps team-to-team moves open per
:func:`~rivercrossing.roster.can_move_rider` (spec S7:171 -- "a
changed team_name is treated as an audited membership move, not a
conflict"). A status/model combination that
cannot safely reshape becomes a conflict at preview time instead of a
partial or unaudited mutation. **An entry present in the roster but
absent from the file is left alone** -- neither the spec nor commit
ever deletes on that basis; only DNF/void (E4) or the rider editor
remove an entry with no matching row.

**Two reshape shapes remain unsupported by commit, and are always
preview conflicts, in every status (see the two functions above the
pooled assembler for exactly which check names them)**: converting an
existing team member into a solo entry, and adding a brand-new or
currently-solo rider directly onto an existing (or freshly forming)
pooled team. Neither has a roster.py primitive that applies it audited
and atomically; :meth:`~rivercrossing.roster.Roster.move_rider` only
relocates a rider who is already on some *other* team. Closing this
gap is flagged for a follow-up decision, not attempted here.

**Pooled team notes (decided 2026-08-09).** On import, a team's
``notes`` is every non-empty member row's own notes, joined with
``"; "`` in file order -- no member's note is silently dropped. Export
(E3.3.3) is expected to write that joined value back onto the team's
first row only, leaving the other member rows' notes blank.
"""

import csv
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rivercrossing.roster import (
    MIN_TEAM_SIZE,
    AuditEvent,
    Entry,
    EntryType,
    PlateModel,
    Rider,
    Roster,
    can_edit_structure,
    can_move_rider,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from rivercrossing.ride import RideStatus

_HEADER_PROBLEM = "missing or malformed header for this ride's plate model"
_MISSING_NAME_PROBLEM = "missing name"
_TEAM_TO_SOLO_PROBLEM = "converting a team member to a solo entry via CSV import is not supported"
_JOIN_UNSUPPORTED_PROBLEM = (
    "adding a new or currently-solo rider into a team via CSV import is not yet supported"
)
_POOLED_HEADER = ("plate", "name", "team_name", "notes")
_TEAM_TYPE_PATTERN = re.compile(r"team(\d+)")


class CsvIoError(Exception):
    """Base for every csvio invariant violation this module raises."""


class ImportConflictsPresentError(CsvIoError):
    """commit() was called on a preview that still has conflicts (R-21).

    Nothing is mutated: commit() checks this before touching the
    preview's roster at all.
    """


@dataclass(frozen=True)
class ParsedRider:
    """One rider parsed from a CSV row, not yet applied to any roster.

    A value object, unlike :class:`rivercrossing.roster.Rider`: two
    riders parsed with the same name and plate compare equal, which
    is exactly what a preview assertion needs.
    """

    name: str
    plate: str | None = None


@dataclass(frozen=True)
class ParsedEntry:
    """One structurally valid entry parsed from the CSV (spec S7).

    ``plate`` is already resolved to the same shape
    :meth:`~rivercrossing.roster.Roster.create_solo_entry` and
    :meth:`~rivercrossing.roster.Roster.create_team_entry` expect --
    direct under ``team_relay``, the lowest-numbered rider's plate
    under ``rider_pooled`` -- so :func:`commit` can hand these
    straight to those constructors without re-deriving anything. A
    row with a *content* conflict (a duplicate plate, a missing
    name) still becomes one of these -- its shape parsed fine -- but
    a row that fails shape validation never does (see
    :class:`ImportPreview`).
    """

    plate: str
    display_name: str
    type: EntryType
    riders: tuple[ParsedRider, ...]
    notes: str = ""


@dataclass(frozen=True)
class ImportConflict:
    """One reason a CSV row cannot be imported as written (spec S7).

    ``row`` is the 1-indexed line number an operator would see
    opening the file in a spreadsheet: row 1 is the header, so the
    first data row is row 2.
    """

    row: int
    problem: str


@dataclass(frozen=True)
class ImportPreview:
    """The result of previewing a CSV import: nothing has been written.

    ``entries`` carries every structurally valid row -- including one
    flagged in ``conflicts`` for a content problem such as a
    duplicate plate or a missing name -- ready for :func:`commit` to
    apply without re-reading ``source_path``. A row that fails shape
    validation (an unrecognized ``type``, a ``teamN`` outside
    2..max_team_size, or a malformed/mismatched header) contributes to
    ``conflicts`` only, never to ``entries``. ``rider_count`` and
    ``team_count`` are derived from ``entries`` rather than stored, so
    they can never drift out of sync with it.
    """

    source_path: Path
    ride: Roster
    entries: tuple[ParsedEntry, ...]
    conflicts: tuple[ImportConflict, ...]

    @property
    def rider_count(self) -> int:
        """Return the total rider count across every parsed entry."""
        return sum(len(entry.riders) for entry in self.entries)

    @property
    def team_count(self) -> int:
        """Return how many parsed entries are teams (not solo)."""
        return sum(1 for entry in self.entries if entry.type is EntryType.TEAM)


@dataclass(frozen=True)
class ImportReport:
    """What commit() actually did, for E3.4's dialog to display (R-21).

    ``audit_events`` is the exact slice of ``ride.audit_log`` commit()
    appended, in order -- so a caller can show a human-readable
    summary without re-deriving it from ``ride`` afterwards.
    """

    inserted_count: int
    updated_count: int
    moved_count: int
    audit_events: tuple[AuditEvent, ...]


def preview(path: Path, ride: Roster) -> ImportPreview:
    """Preview a CSV import against *ride*; write nothing (R-21).

    The file's header must match *ride*'s plate_model exactly, or the
    whole file is reported as one header conflict at row 1 and no
    rows are parsed (spec S7).

    Args:
        path: The CSV file to read. Never written to.
        ride: The roster this import would apply to; its
            plate_model, max_team_size, status and existing entries
            drive every check below. Never mutated.

    Returns:
        An :class:`ImportPreview` naming every conflict found and
        every structurally valid entry.

    Raises:
        OSError: *path* cannot be opened (e.g. it does not exist).
            Preview does not catch or wrap I/O errors.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != _expected_header(ride):
            conflict = ImportConflict(row=1, problem=_HEADER_PROBLEM)
            return ImportPreview(source_path=path, ride=ride, entries=(), conflicts=(conflict,))
        if ride.plate_model is PlateModel.TEAM_RELAY:
            return _preview_relay(reader, path, ride)
        return _preview_pooled(reader, path, ride)


def commit(preview: ImportPreview) -> ImportReport:
    """Apply *preview* to its roster, atomically (R-21).

    Every mutation goes through one of the roster's own mutators, so
    it is fully audit-logged; nothing present in the roster but
    absent from the file is ever touched (match/insert/reshape only,
    never delete-on-import).

    Args:
        preview: A previously computed, still-current preview.

    Returns:
        An :class:`ImportReport` summarising what changed.

    Raises:
        ImportConflictsPresentError: *preview* still has conflicts;
            nothing is mutated.
    """
    if preview.conflicts:
        msg = f"{len(preview.conflicts)} conflict(s) must be resolved before importing"
        raise ImportConflictsPresentError(msg)
    ride = preview.ride
    before = len(ride.audit_log)
    if ride.plate_model is PlateModel.TEAM_RELAY:
        inserted, updated = _commit_relay(ride, preview.entries)
        moved = 0
    else:
        inserted, updated, moved = _commit_pooled(ride, preview.entries)
    return ImportReport(
        inserted_count=inserted,
        updated_count=updated,
        moved_count=moved,
        audit_events=ride.audit_log[before:],
    )


def _expected_header(ride: Roster) -> list[str]:
    """Return the CSV header *ride*'s plate_model requires (spec S7)."""
    if ride.plate_model is PlateModel.TEAM_RELAY:
        rider_cols = [f"rider_{i}" for i in range(1, ride.max_team_size + 1)]
        return ["plate", "entry_name", "type", *rider_cols, "notes"]
    return list(_POOLED_HEADER)


def _field(row: Mapping[str, str | None], column: str) -> str:
    """Return *column* from *row*, stripped; blank/None becomes ''."""
    return (row.get(column) or "").strip()


def _find_entry_by_plate(ride: Roster, plate: str) -> Entry | None:
    """Return *ride*'s entry whose own plate equals *plate*, if any."""
    for entry in ride.entries:
        if entry.plate == plate:
            return entry
    return None


def _pooled_owner_index(ride: Roster) -> dict[str, tuple[Entry, Rider]]:
    """Map every pooled plate already claimed in *ride* to its owner.

    Every rider_pooled rider carries a plate by construction
    (roster.py's own ``_shape_and_validate`` enforces it), so this
    only ever runs against entries where that is already true.
    """
    return {
        cast("str", rider.plate): (entry, rider)
        for entry in ride.entries
        for rider in entry.riders
    }


def _duplicate_plate_problem(plate: str, seen_plates: set[str]) -> str | None:
    """Return a conflict message when *plate* repeats in the file."""
    if plate in seen_plates:
        return f"duplicate plate {plate}"
    return None


# --------------------------------------------------------- team_relay


def _team_size_from_type(type_field: str) -> int | None:
    """Return teamN's N, or None if *type_field* is not a teamN."""
    match = _TEAM_TYPE_PATTERN.fullmatch(type_field)
    if match is None:
        return None
    return int(match.group(1))


def _shape_relay_row(row: Mapping[str, str | None], *, max_team_size: int) -> ParsedEntry | str:
    """Parse one team_relay row, or return its shape problem text."""
    plate = _field(row, "plate")
    entry_name = _field(row, "entry_name")
    type_field = _field(row, "type")
    notes = _field(row, "notes")
    if type_field == "solo":
        rider = ParsedRider(name=entry_name)
        return ParsedEntry(
            plate=plate, display_name=entry_name, type=EntryType.SOLO, riders=(rider,), notes=notes
        )
    size = _team_size_from_type(type_field)
    if size is None:
        return f"unknown entry type {type_field!r}"
    if not MIN_TEAM_SIZE <= size <= max_team_size:
        return f"team size must be between {MIN_TEAM_SIZE} and {max_team_size}, got {size}"
    riders = tuple(ParsedRider(name=_field(row, f"rider_{i}")) for i in range(1, size + 1))
    return ParsedEntry(
        plate=plate, display_name=entry_name, type=EntryType.TEAM, riders=riders, notes=notes
    )


def _missing_name_problem(entry: ParsedEntry) -> str | None:
    """Return a missing-name conflict if any required name is blank."""
    if not entry.display_name or any(not rider.name for rider in entry.riders):
        return _MISSING_NAME_PROBLEM
    return None


def _relay_composition_changed(existing: Entry, parsed: ParsedEntry) -> bool:
    """Return True if *parsed*'s riders/type differ from *existing*'s.

    A solo entry's one "rider" is just its own display name (S1), so
    a solo-to-solo match is never a composition change -- renaming it
    is exactly :func:`_update_name_notes`'s job, not a reshape.
    """
    if existing.type != parsed.type:
        return True
    if parsed.type is EntryType.SOLO:
        return False
    existing_names = tuple(rider.name for rider in existing.riders)
    parsed_names = tuple(rider.name for rider in parsed.riders)
    return existing_names != parsed_names


def _relay_structural_problem(
    existing: Entry | None, parsed: ParsedEntry, status: RideStatus
) -> str | None:
    """Return a conflict if a matched relay row's shape is unsafe now.

    A relay entry's roster is only reshaped through delete+recreate
    (no roster.py mutator resizes one in place), which needs DRAFT's
    free structural edits; once started, relay keeps its permanent
    lock (R-17), so any composition change becomes a conflict instead.
    """
    if existing is None or not _relay_composition_changed(existing, parsed):
        return None
    if can_edit_structure(status):
        return None
    return (
        f"team composition changed but the ride is {status}; only new "
        "plates or name fixes are allowed"
    )


def _preview_relay(reader: csv.DictReader[str], path: Path, ride: Roster) -> ImportPreview:
    """Parse every team_relay data row *reader* yields (spec S7)."""
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    seen_plates: set[str] = set()
    for row_num, row in enumerate(reader, start=2):
        parsed = _shape_relay_row(row, max_team_size=ride.max_team_size)
        if isinstance(parsed, str):
            conflicts.append(ImportConflict(row=row_num, problem=parsed))
            continue
        existing = _find_entry_by_plate(ride, parsed.plate)
        problem = (
            _missing_name_problem(parsed)
            or _duplicate_plate_problem(parsed.plate, seen_plates)
            or _relay_structural_problem(existing, parsed, ride.status)
        )
        if problem is not None:
            conflicts.append(ImportConflict(row=row_num, problem=problem))
        seen_plates.add(parsed.plate)
        entries.append(parsed)
    return ImportPreview(
        source_path=path, ride=ride, entries=tuple(entries), conflicts=tuple(conflicts)
    )


def _commit_relay(ride: Roster, entries: Sequence[ParsedEntry]) -> tuple[int, int]:
    """Apply every parsed relay entry: insert, reshape, or rename it."""
    inserted = 0
    updated = 0
    for parsed in entries:
        existing = _find_entry_by_plate(ride, parsed.plate)
        if existing is None:
            _insert_relay_entry(ride, parsed)
            inserted += 1
        elif _relay_composition_changed(existing, parsed):
            ride.delete_entry(existing)
            _insert_relay_entry(ride, parsed)
            updated += 1
        elif _update_name_notes(ride, existing, parsed):
            updated += 1
    return inserted, updated


def _insert_relay_entry(ride: Roster, parsed: ParsedEntry) -> None:
    """Create *parsed* as a fresh relay entry, keeping its own plate."""
    if parsed.type is EntryType.SOLO:
        entry = ride.create_solo_entry(name=parsed.display_name, plate=parsed.plate)
    else:
        riders = [Rider(name=rider.name) for rider in parsed.riders]
        entry = ride.create_team_entry(
            display_name=parsed.display_name, riders=riders, plate=parsed.plate
        )
    if parsed.notes:
        ride.update_entry(entry, notes=parsed.notes)


def _update_name_notes(ride: Roster, existing: Entry, parsed: ParsedEntry) -> bool:
    """Rename/renote *existing* to match *parsed*; True if changed."""
    changes: dict[str, str] = {}
    if existing.display_name != parsed.display_name:
        changes["display_name"] = parsed.display_name
    if existing.notes != parsed.notes:
        changes["notes"] = parsed.notes
    if not changes:
        return False
    ride.update_entry(existing, **changes)
    return True


# --------------------------------------------------------- rider_pooled


def _preview_pooled(reader: csv.DictReader[str], path: Path, ride: Roster) -> ImportPreview:
    """Parse every rider_pooled row, grouped by team_name (spec S7)."""
    existing_index = _pooled_owner_index(ride)
    solo_entries: list[ParsedEntry] = []
    groups: dict[str, list[tuple[int, ParsedRider, str]]] = {}
    conflicts: list[ImportConflict] = []
    seen_plates: set[str] = set()
    for row_num, row in enumerate(reader, start=2):
        plate = _field(row, "plate")
        name = _field(row, "name")
        team_name = _field(row, "team_name")
        notes = _field(row, "notes")
        owner = existing_index.get(plate) if plate else None
        problem = (_MISSING_NAME_PROBLEM if not name else None) or _duplicate_plate_problem(
            plate, seen_plates
        )
        if (
            problem is None
            and not team_name
            and owner is not None
            and owner[0].type is EntryType.TEAM
        ):
            problem = _TEAM_TO_SOLO_PROBLEM
        if problem is not None:
            conflicts.append(ImportConflict(row=row_num, problem=problem))
        seen_plates.add(plate)
        rider = ParsedRider(name=name, plate=plate)
        if not team_name:
            solo_entries.append(
                ParsedEntry(
                    plate=plate,
                    display_name=name,
                    type=EntryType.SOLO,
                    riders=(rider,),
                    notes=notes,
                )
            )
        else:
            groups.setdefault(team_name, []).append((row_num, rider, notes))
    team_entries, team_conflicts = _assemble_pooled_teams(groups, ride)
    conflicts.extend(team_conflicts)
    entries = (*solo_entries, *team_entries)
    return ImportPreview(source_path=path, ride=ride, entries=entries, conflicts=tuple(conflicts))


def _pooled_team_target(
    index: Mapping[str, tuple[Entry, Rider]], riders: Sequence[ParsedRider]
) -> Entry | None:
    """Return the existing team most of *riders* already belong to."""
    team_owners = [
        owner[0]
        for rider in riders
        if rider.plate
        and (owner := index.get(rider.plate)) is not None
        and owner[0].type is EntryType.TEAM
    ]
    if not team_owners:
        return None
    return Counter(team_owners).most_common(1)[0][0]


def _assemble_pooled_teams(
    groups: Mapping[str, Sequence[tuple[int, ParsedRider, str]]], ride: Roster
) -> tuple[tuple[ParsedEntry, ...], list[ImportConflict]]:
    """Build one team ParsedEntry per team_name group (spec S7, R-12).

    A group of exactly one rider still becomes a team entry -- the
    2026-08-09 follow-on decision defers rider_pooled's own 2-rider
    floor to this preview-time "team-under-min" conflict rather than
    refusing the row outright. ``notes`` joins every non-empty
    member's own row notes with "; " (2026-08-09, module docstring).
    """
    existing_index = _pooled_owner_index(ride)
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    for team_name, rows in groups.items():
        riders = tuple(rider for _, rider, _ in rows)
        notes = "; ".join(note for _, _, note in rows if note)
        plate = min((rider.plate for rider in riders if rider.plate), key=int)
        entries.append(
            ParsedEntry(
                plate=plate,
                display_name=team_name,
                type=EntryType.TEAM,
                riders=riders,
                notes=notes,
            )
        )
        if len(riders) < MIN_TEAM_SIZE:
            conflicts.append(
                ImportConflict(row=rows[0][0], problem=_team_under_min_problem(riders))
            )
            continue
        conflicts.extend(_pooled_team_structural_conflicts(rows, existing_index, ride.status))
    return tuple(entries), conflicts


def _team_under_min_problem(riders: Sequence[ParsedRider]) -> str:
    """Return the team-under-min conflict text for *riders* (R-12)."""
    return f"team of {len(riders)} rider is below the minimum of {MIN_TEAM_SIZE} (team-under-min)"


def _pooled_move_problem(status: RideStatus) -> str:
    """Return the conflict text for a pooled move *status* disallows."""
    return f"team change requires DRAFT, RUNNING or REOPENED (ride is {status})"


def _pooled_team_structural_conflicts(
    rows: Sequence[tuple[int, ParsedRider, str]],
    existing_index: Mapping[str, tuple[Entry, Rider]],
    status: RideStatus,
) -> list[ImportConflict]:
    """Return every conflict this team's own membership reshape has.

    A member already on the resolved target needs nothing. One
    already on a *different* existing team is a real move, gated by
    :func:`~rivercrossing.roster.can_move_rider` (spec S7:171's pooled
    exception). Anything else reaching an existing or forming target
    -- a brand-new plate, or a currently-solo rider -- has no
    supported commit() primitive yet (module docstring) and always
    conflicts, in every status.
    """
    riders = [rider for _, rider, _ in rows]
    target = _pooled_team_target(existing_index, riders)
    conflicts: list[ImportConflict] = []
    for row_num, rider, _notes in rows:
        owner = existing_index.get(rider.plate) if rider.plate else None
        if owner is not None and owner[0] is target:
            continue
        if owner is not None and owner[0].type is EntryType.TEAM:
            if not can_move_rider(status, PlateModel.RIDER_POOLED):
                conflicts.append(ImportConflict(row=row_num, problem=_pooled_move_problem(status)))
            continue
        if target is not None or owner is not None:
            conflicts.append(ImportConflict(row=row_num, problem=_JOIN_UNSUPPORTED_PROBLEM))
    return conflicts


@dataclass
class _PooledCtx:
    """Mutable running state for one rider_pooled commit() pass."""

    ride: Roster
    index: dict[str, tuple[Entry, Rider]]
    inserted: int = 0
    updated: int = 0
    moved: int = 0


def _commit_pooled(ride: Roster, entries: Sequence[ParsedEntry]) -> tuple[int, int, int]:
    """Apply every parsed pooled entry: insert, rename, or move it."""
    ctx = _PooledCtx(ride=ride, index=_pooled_owner_index(ride))
    for parsed in entries:
        if parsed.type is EntryType.SOLO:
            _commit_pooled_solo(ctx, parsed)
        else:
            _commit_pooled_team(ctx, parsed)
    return ctx.inserted, ctx.updated, ctx.moved


def _commit_pooled_solo(ctx: _PooledCtx, parsed: ParsedEntry) -> None:
    """Insert a brand-new pooled solo entry, or rename an existing one.

    preview() rejects a team->solo demotion (no roster.py primitive
    applies it), so *owner* here is never a TEAM entry.
    """
    owner = ctx.index.get(parsed.plate)
    if owner is None:
        entry = ctx.ride.create_solo_entry(name=parsed.display_name, plate=parsed.plate)
        if parsed.notes:
            ctx.ride.update_entry(entry, notes=parsed.notes)
        ctx.index[parsed.plate] = (entry, entry.riders[0])
        ctx.inserted += 1
        return
    existing_entry, _existing_rider = owner
    if _update_name_notes(ctx.ride, existing_entry, parsed):
        ctx.updated += 1


def _commit_pooled_team(ctx: _PooledCtx, parsed: ParsedEntry) -> None:
    """Insert a brand-new team, or move riders onto an existing one."""
    target = _pooled_team_target(ctx.index, list(parsed.riders))
    if target is None:
        _form_pooled_team(ctx, parsed)
        return
    _join_pooled_team(ctx, parsed, target)
    if _update_name_notes(ctx.ride, target, parsed):
        ctx.updated += 1


def _form_pooled_team(ctx: _PooledCtx, parsed: ParsedEntry) -> None:
    """Create a brand-new pooled team from entirely-new riders.

    preview() rejects any group that mixes in a currently-solo rider
    (no roster.py primitive promotes one into a fresh team yet), so
    every member reaching here is genuinely new.
    """
    riders = [Rider(name=rider.name, plate=rider.plate) for rider in parsed.riders]
    entry = ctx.ride.create_team_entry(display_name=parsed.display_name, riders=riders)
    if parsed.notes:
        ctx.ride.update_entry(entry, notes=parsed.notes)
    for rider in entry.riders:
        ctx.index[cast("str", rider.plate)] = (entry, rider)
    ctx.inserted += 1


def _join_pooled_team(ctx: _PooledCtx, parsed: ParsedEntry, target: Entry) -> None:
    """Move every parsed rider not already on *target* onto it.

    preview() only lets a rider reach here already belonging to a
    *different* existing team (no roster.py primitive adds a
    brand-new or solo rider directly onto an existing team yet).
    """
    for rider in parsed.riders:
        # preview() guarantees every member here already has a plate on
        # record -- see this function's own docstring.
        plate = cast("str", rider.plate)
        entry, old_rider = ctx.index[plate]
        if entry is target:
            continue
        ctx.ride.move_rider(old_rider, to_entry=target)
        ctx.index[plate] = (target, old_rider)
        ctx.moved += 1
