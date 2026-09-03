# SPDX-License-Identifier: GPL-3.0-only
"""CSV roster import/export: preview-then-commit (spec S7, R-21, E3.3).

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

**Pooled team<->solo conversions are DRAFT-only (the pooled-reshape
follow-on).** A team member's row losing its team_name applies via
:meth:`~rivercrossing.roster.Roster.extract_rider_to_solo`; a
brand-new or currently-solo rider's row gaining one applies via
:meth:`~rivercrossing.roster.Roster.add_rider_to_team` (a solo rider's
own entry is dissolved first). Both are gated by
:func:`~rivercrossing.roster.can_edit_structure` -- DRAFT only, spec
S1's "convert solo <-> team ... before start" -- **except** a
brand-new plate landing straight on an *existing* team, which stays
open through RUNNING/REOPENED via
:func:`~rivercrossing.roster.can_move_rider`'s own carve-out, same as
a team-to-team move. A conversion the current status locks becomes a
conflict at preview time instead of a partial or unaudited mutation.

**Pooled team notes (decided 2026-08-09).** On import, a team's
``notes`` is every non-empty member row's own notes, joined with
``"; "`` in file order -- no member's note is silently dropped.
:func:`export` writes that joined value back onto the team's first
row only, leaving the other member rows' notes blank -- so a
commit-then-export-then-preview round trip reproduces the same
``notes`` string.

**Export (E3.3.3).** ``export(ride, path, *, placed=None)`` writes
*ride*'s current roster in the same shape :func:`preview` reads,
selected by ``ride.plate_model`` -- an export of a conflict-free
preview's target therefore previews clean again (spec §7's own
"export mirrors the columns"; task-briefs.md E3.3.3's round-trip
property). Passing *placed* -- a sequence of
:class:`rivercrossing.standings.Placed` from EPIC 6's rankings, the
module's first standings dependency (approved decision D3) --
appends spec §7's four finished-ride columns ``laps, cards,
best_hand, total_time`` after the roster's own columns
(plate/entry_name-or-name/type-or-team_name/riders/notes), guarded to
a FINISHED ride and filled from the matching entry's Placed row (an
entry missing from *placed* fails loudly; extra rows are ignored).
Value formats are machine-readable, decided for P3: laps an int,
cards ``len(result.cards)``, best_hand
:func:`~rivercrossing.standings.hand_name`'s prose, total_time raw
numeric seconds (``repr``-clean) -- human formatting belongs to the
HTML/PDF exports only. The standalone spec §15 standings CSV ships as
:func:`export_standings` (E6.4.2): rows ``place, plate, entry, laps,
hand`` plus a raw-seconds ``total_time`` column when asked (R-63).

**Atomic export writes (R-52).** Both writers stage their CSV in a
same-directory temp file (``<name>.tmp``) and swap it over the
destination with :func:`os.replace` -- never truncating *path* in
place -- so a crash mid-export leaves the previous complete artifact
behind, never a partial file.
"""

import csv
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rivercrossing.ride import RideStatus
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
from rivercrossing.standings import Placed, hand_name

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "CsvIoError",
    "ImportConflict",
    "ImportConflictsPresentError",
    "ImportPreview",
    "ImportReport",
    "ParsedEntry",
    "ParsedRider",
    "commit",
    "export",
    "export_standings",
    "preview",
]

_HEADER_PROBLEM = "missing or malformed header for this ride's plate model"
_FINISHED_COLUMNS = ("laps", "cards", "best_hand", "total_time")
_MISSING_NAME_PROBLEM = "missing name"
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
    riders parsed with the same first/last names and plate compare
    equal, which is exactly what a preview assertion needs. The CSV's
    single name column is split into ``first_name``/``last_name`` at
    parse time (Phase 1 rider-name split) -- see :func:`_split_name`.
    """

    first_name: str
    last_name: str
    plate: str | None = None

    @property
    def full_name(self) -> str:
        """Return this rider's display name, first and last joined."""
        return " ".join(part for part in (self.first_name.strip(), self.last_name.strip()) if part)


def _split_name(name: str) -> tuple[str, str]:
    """Split one CSV name column into (first_name, last_name).

    The single-column CSV shape stays for now (Phase 1 does not
    rewrite the two-shape design); a name is split at its first
    space -- "A. Roy" -> ("A.", "Roy"), "Mary Jane Watson" ->
    ("Mary", "Jane Watson") -- and a single-word name keeps an empty
    last name. ``name`` is already edge-stripped by :func:`_field`.
    """
    first, separator, remainder = name.partition(" ")
    if not separator:
        return first, ""
    return first, remainder.strip()


def _parsed_rider(name: str, *, plate: str | None = None) -> ParsedRider:
    """Build one ParsedRider by splitting a single-column *name*."""
    first_name, last_name = _split_name(name)
    return ParsedRider(first_name=first_name, last_name=last_name, plate=plate)


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

    ``extracted_count`` counts a pooled team member turned into their
    own solo entry (``extract_rider_to_solo``); ``joined_count``
    counts a brand-new or converted-from-solo rider added onto an
    *existing* pooled team (``add_rider_to_team``) -- a rider folded
    into a *freshly created* team counts under ``inserted_count``
    instead, same as any other new team's initial members. Both are
    always 0 for team_relay, which has neither operation.
    ``audit_events`` is the exact slice of ``ride.audit_log`` commit()
    appended, in order -- so a caller can show a human-readable
    summary without re-deriving it from ``ride`` afterwards.
    """

    inserted_count: int
    updated_count: int
    moved_count: int
    extracted_count: int
    joined_count: int
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
        moved = extracted = joined = 0
    else:
        inserted, updated, moved, extracted, joined = _commit_pooled(ride, preview.entries)
    return ImportReport(
        inserted_count=inserted,
        updated_count=updated,
        moved_count=moved,
        extracted_count=extracted,
        joined_count=joined,
        audit_events=ride.audit_log[before:],
    )


def export(ride: Roster, path: Path, *, placed: Sequence[Placed] | None = None) -> None:
    """Write *ride*'s current roster to *path* as CSV (R-21, spec S7).

    The header and column shape match *ride*'s plate_model exactly --
    the same shape :func:`preview` requires -- so re-importing the
    result against an equivalent roster previews with zero conflicts
    (module docstring's round-trip note). Passing *placed* -- a
    finished ride's standings -- appends spec §7's four columns
    (``laps, cards, best_hand, total_time``) after the roster's own
    ones and fills them from each matching entry's
    :class:`~rivercrossing.standings.Placed` row; ``placed=None``
    keeps the export byte-identical to the roster-only shape. The
    write is atomic (R-52): the CSV is staged in a same-directory temp
    file and swapped over *path* with :func:`os.replace`, so a crash
    mid-export leaves the previous complete file in place.

    Args:
        ride: The roster to export. Never mutated.
        path: The file to write. Replaced atomically; a pre-existing
            file is overwritten wholesale, never truncated in place.
        placed: Optional standings for a FINISHED ride; the caller may
            pass the whole ranked field (rows with no matching entry
            are ignored, DNFs included). Defaults to None, the
            roster-only export.

    Raises:
        CsvIoError: *placed* is given while *ride* is not FINISHED, or
            an entry has no matching
            :class:`~rivercrossing.standings.Placed` row (naming the
            first missing plate).
    """
    if placed is not None and ride.status is not RideStatus.FINISHED:
        msg = f"standings columns require a finished ride (ride is {ride.status})"
        raise CsvIoError(msg)
    header = _expected_header(ride, with_standings=placed is not None)
    by_plate = _finished_by_plate(ride, placed) if placed is not None else None
    rows: list[list[str]] = []
    if ride.plate_model is PlateModel.TEAM_RELAY:
        for entry in ride.entries:
            row = _relay_export_row(entry, ride.max_team_size)
            if by_plate is not None:
                row.extend(_stats_values(by_plate[entry.plate]))
            rows.append(row)
    else:
        for entry in ride.entries:
            entry_rows = _pooled_export_rows(entry)
            if by_plate is not None:
                stats = _stats_values(by_plate[entry.plate])
                entry_rows = [row + stats for row in entry_rows]
            rows.extend(entry_rows)
    _write_csv_rows(path, header, rows)


def export_standings(placed: Sequence[Placed], path: Path, *, show_times: bool = False) -> None:
    """Write *placed* as the spec §15 standings CSV to *path* (E6.4.2).

    Rows are ``place, plate, entry, laps, hand`` with a
    ``total_time`` column appended when *show_times* -- raw numeric
    seconds, consistent with :func:`export`'s finished-ride columns
    (CSVs are machine-readable; human formatting is the HTML/PDF
    exports' job). DNF entries keep their row with their laps and
    cards (R-33); an entry that never crossed renders a blank hand.
    The write is atomic (R-52), exactly like :func:`export`: staged in
    a same-directory temp file, then swapped over *path* with
    :func:`os.replace`.

    Args:
        placed: Ranked standings, one row each.
        path: The file to write. Replaced atomically; a pre-existing
            file is overwritten wholesale, never truncated in place.
        show_times: Append the ``total_time`` column (R-63: times
            only when the export setting says so).
    """
    header = ["place", "plate", "entry", "laps", "hand"]
    if show_times:
        header.append("total_time")
    rows: list[list[str]] = []
    for placed_row in placed:
        result = placed_row.result
        row = [
            str(placed_row.place),
            result.plate,
            result.name,
            str(result.laps),
            hand_name(result.hand) if result.cards else "",
        ]
        if show_times:
            row.append(repr(result.total_time))
        rows.append(row)
    _write_csv_rows(path, header, rows)


def _write_csv_rows(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """Write *header* and *rows* to *path* as CSV, atomically (R-52).

    Content is staged in a same-directory temp sibling and swapped over
    *path* with :func:`os.replace` -- the temp handle is closed (and
    therefore flushed) before the swap -- so a crash mid-export leaves
    the previous complete file in place and a reader never observes a
    truncated CSV.
    """
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    os.replace(tmp, path)  # noqa: PTH105 -- R-52 mandates the os.replace atomic swap; tests patch it


def _stats_values(placed: Placed) -> list[str]:
    """Return *placed*'s four finished-ride column values (spec §7).

    Machine-readable formats (P3 decision): laps an int, cards the
    count, best_hand :func:`~rivercrossing.standings.hand_name`'s
    prose, total_time raw numeric seconds (``repr``-clean) -- human
    formatting belongs to the HTML/PDF exports only.
    """
    result = placed.result
    return [
        str(result.laps),
        str(len(result.cards)),
        hand_name(result.hand),
        repr(result.total_time),
    ]


def _finished_by_plate(ride: Roster, placed: Sequence[Placed]) -> dict[str, Placed]:
    """Map every *ride* entry's plate to its Placed row (spec §7).

    An entry with no matching row is an invariant violation -- the
    caller passed standings that do not cover the roster -- so export
    fails loudly rather than inventing placeholder values; extra
    placed rows (the caller may pass the whole ranked field, DNF
    entries included) are ignored.
    """
    by_plate = {placed_row.result.plate: placed_row for placed_row in placed}
    missing = [entry.plate for entry in ride.entries if entry.plate not in by_plate]
    if missing:
        raise CsvIoError(f"no standings for plate {missing[0]}")
    return by_plate


def _relay_export_row(entry: Entry, max_team_size: int) -> list[str]:
    """Return one team_relay CSV row for *entry* (spec S7)."""
    if entry.type is EntryType.SOLO:
        type_field = "solo"
        names: list[str] = []
    else:
        type_field = f"team{len(entry.riders)}"
        names = [rider.full_name for rider in entry.riders]
    slots = [*names, *([""] * (max_team_size - len(names)))]
    return [entry.plate, entry.display_name, type_field, *slots, entry.notes]


def _pooled_export_rows(entry: Entry) -> list[list[str]]:
    """Return one rider_pooled CSV row per *entry*'s rider (spec S7).

    A team's ``notes`` is written on its first row only -- the export
    half of the notes-join rule (module docstring) -- so re-importing
    joins it right back onto that one value; a solo entry has only
    the one row, so its own notes always land on it.
    """
    team_name = entry.display_name if entry.type is EntryType.TEAM else ""
    return [
        [
            cast("str", rider.plate),
            rider.full_name,
            team_name,
            entry.notes if index == 0 else "",
        ]
        for index, rider in enumerate(entry.riders)
    ]


def _expected_header(ride: Roster, *, with_standings: bool = False) -> list[str]:
    """Return the CSV header *ride*'s plate_model requires (spec S7).

    A finished ride's standings export appends spec §7's four columns
    (laps, cards, best_hand, total_time) after the model's own ones;
    :func:`preview` never sets *with_standings*, so its header check
    stays byte-identical to the roster-only shape.
    """
    if ride.plate_model is PlateModel.TEAM_RELAY:
        rider_cols = [f"rider_{i}" for i in range(1, ride.max_team_size + 1)]
        header = ["plate", "entry_name", "type", *rider_cols, "notes"]
    else:
        header = list(_POOLED_HEADER)
    if with_standings:
        header.extend(_FINISHED_COLUMNS)
    return header


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
        rider = _parsed_rider(entry_name)
        return ParsedEntry(
            plate=plate, display_name=entry_name, type=EntryType.SOLO, riders=(rider,), notes=notes
        )
    size = _team_size_from_type(type_field)
    if size is None:
        return f"unknown entry type {type_field!r}"
    if not MIN_TEAM_SIZE <= size <= max_team_size:
        return f"team size must be between {MIN_TEAM_SIZE} and {max_team_size}, got {size}"
    riders = tuple(_parsed_rider(_field(row, f"rider_{i}")) for i in range(1, size + 1))
    return ParsedEntry(
        plate=plate, display_name=entry_name, type=EntryType.TEAM, riders=riders, notes=notes
    )


def _missing_name_problem(entry: ParsedEntry) -> str | None:
    """Return a missing-name conflict if any required name is blank."""
    if not entry.display_name or any(not rider.full_name for rider in entry.riders):
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
    existing_names = tuple(rider.full_name for rider in existing.riders)
    parsed_names = tuple(rider.full_name for rider in parsed.riders)
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
        parsed_rider = parsed.riders[0]
        entry = ride.create_solo_entry(
            first_name=parsed_rider.first_name,
            last_name=parsed_rider.last_name,
            plate=parsed.plate,
        )
    else:
        riders = [
            Rider(first_name=rider.first_name, last_name=rider.last_name)
            for rider in parsed.riders
        ]
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
            and not can_edit_structure(ride.status)
        ):
            problem = _team_to_solo_problem(ride.status)
        if problem is not None:
            conflicts.append(ImportConflict(row=row_num, problem=problem))
        seen_plates.add(plate)
        rider = _parsed_rider(name, plate=plate)
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
        if len(riders) > ride.max_team_size:
            conflicts.append(
                ImportConflict(
                    row=rows[0][0], problem=_team_over_max_problem(riders, ride.max_team_size)
                )
            )
            continue
        conflicts.extend(_pooled_team_structural_conflicts(rows, existing_index, ride.status))
    return tuple(entries), conflicts


def _team_under_min_problem(riders: Sequence[ParsedRider]) -> str:
    """Return the team-under-min conflict text for *riders* (R-12)."""
    return f"team of {len(riders)} rider is below the minimum of {MIN_TEAM_SIZE} (team-under-min)"


def _team_over_max_problem(riders: Sequence[ParsedRider], max_team_size: int) -> str:
    """Return the team-over-max conflict text for *riders* (R-12)."""
    return f"team of {len(riders)} riders exceeds the maximum of {max_team_size} (team-over-max)"


def _pooled_move_problem(status: RideStatus) -> str:
    """Return the conflict text for a pooled move *status* disallows."""
    return f"team change requires DRAFT, RUNNING or REOPENED (ride is {status})"


def _team_to_solo_problem(status: RideStatus) -> str:
    """Return the conflict text for a team->solo *status* locks out."""
    return f"converting a team member to a solo entry requires DRAFT (ride is {status})"


def _solo_to_team_problem(status: RideStatus) -> str:
    """Return the conflict text for a solo->team *status* locks out."""
    return f"converting a solo rider into a team member requires DRAFT (ride is {status})"


def _pooled_team_structural_conflicts(
    rows: Sequence[tuple[int, ParsedRider, str]],
    existing_index: Mapping[str, tuple[Entry, Rider]],
    status: RideStatus,
) -> list[ImportConflict]:
    """Return every conflict this team's own membership reshape has.

    A member already on the resolved target needs nothing. One
    already on a *different* existing team is a real move, gated by
    :func:`~rivercrossing.roster.can_move_rider` (spec S7:171's pooled
    exception, also covering a brand-new plate landing straight on an
    existing team). A currently-solo rider converting into a team
    member is gated by :func:`~rivercrossing.roster.can_edit_structure`
    instead -- DRAFT only, in every case, existing or forming target.
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
        if owner is not None:  # currently solo, converting to a team member
            if not can_edit_structure(status):
                conflicts.append(
                    ImportConflict(row=row_num, problem=_solo_to_team_problem(status))
                )
            continue
        if target is not None and not can_move_rider(status, PlateModel.RIDER_POOLED):
            conflicts.append(ImportConflict(row=row_num, problem=_pooled_move_problem(status)))
    return conflicts


@dataclass
class _PooledCtx:
    """Mutable running state for one rider_pooled commit() pass."""

    ride: Roster
    index: dict[str, tuple[Entry, Rider]]
    inserted: int = 0
    updated: int = 0
    moved: int = 0
    extracted: int = 0
    joined: int = 0


def _commit_pooled(ride: Roster, entries: Sequence[ParsedEntry]) -> tuple[int, int, int, int, int]:
    """Apply every parsed pooled entry: insert, rename, move, join."""
    ctx = _PooledCtx(ride=ride, index=_pooled_owner_index(ride))
    for parsed in entries:
        if parsed.type is EntryType.SOLO:
            _commit_pooled_solo(ctx, parsed)
        else:
            _commit_pooled_team(ctx, parsed)
    return ctx.inserted, ctx.updated, ctx.moved, ctx.extracted, ctx.joined


def _commit_pooled_solo(ctx: _PooledCtx, parsed: ParsedEntry) -> None:
    """Insert, rename, or extract-to-solo a matched pooled solo row."""
    owner = ctx.index.get(parsed.plate)
    if owner is None:
        parsed_rider = parsed.riders[0]
        entry = ctx.ride.create_solo_entry(
            first_name=parsed_rider.first_name,
            last_name=parsed_rider.last_name,
            plate=parsed.plate,
        )
        if parsed.notes:
            ctx.ride.update_entry(entry, notes=parsed.notes)
        ctx.index[parsed.plate] = (entry, entry.riders[0])
        ctx.inserted += 1
        return
    existing_entry, existing_rider = owner
    if existing_entry.type is EntryType.TEAM:
        entry = ctx.ride.extract_rider_to_solo(existing_rider)
        if _update_name_notes(ctx.ride, entry, parsed):
            ctx.updated += 1
        ctx.index[parsed.plate] = (entry, existing_rider)
        ctx.extracted += 1
        return
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
    """Create a brand-new pooled team from new and/or converted riders.

    A currently-solo member's own solo entry is dissolved first, then
    its existing :class:`~rivercrossing.roster.Rider` object joins the
    new team's initial roster (no rename, same identity); a brand-new
    plate gets a fresh one. Either way the team itself is new, so
    every member here counts under ``inserted``, not ``joined`` --
    matching how a wholly-new team's members were never counted per
    rider either.
    """
    riders: list[Rider] = []
    for parsed_rider in parsed.riders:
        plate = cast("str", parsed_rider.plate)
        owner = ctx.index.get(plate)
        if owner is None:
            riders.append(
                Rider(
                    first_name=parsed_rider.first_name,
                    last_name=parsed_rider.last_name,
                    plate=plate,
                )
            )
        else:
            old_entry, old_rider = owner
            ctx.ride.delete_entry(old_entry)
            riders.append(old_rider)
    entry = ctx.ride.create_team_entry(display_name=parsed.display_name, riders=riders)
    if parsed.notes:
        ctx.ride.update_entry(entry, notes=parsed.notes)
    for rider in entry.riders:
        ctx.index[cast("str", rider.plate)] = (entry, rider)
    ctx.inserted += 1


def _join_pooled_team(ctx: _PooledCtx, parsed: ParsedEntry, target: Entry) -> None:
    """Move, convert, or add every parsed rider not already on *target*.

    A rider already on a *different* team moves there
    (``move_rider``); a currently-solo rider has their own entry
    dissolved first, then joins with their existing identity kept; a
    brand-new plate gets a fresh :class:`~rivercrossing.roster.Rider`
    -- both of the latter two via ``add_rider_to_team``.
    """
    for parsed_rider in parsed.riders:
        plate = cast("str", parsed_rider.plate)
        owner = ctx.index.get(plate)
        if owner is not None and owner[0] is target:
            continue
        if owner is not None and owner[0].type is EntryType.TEAM:
            _, old_rider = owner
            ctx.ride.move_rider(old_rider, to_entry=target)
            ctx.index[plate] = (target, old_rider)
            ctx.moved += 1
            continue
        if owner is not None:
            old_entry, old_rider = owner
            ctx.ride.delete_entry(old_entry)
            ctx.ride.add_rider_to_team(old_rider, to_entry=target)
            ctx.index[plate] = (target, old_rider)
            ctx.joined += 1
            continue
        new_rider = Rider(
            first_name=parsed_rider.first_name,
            last_name=parsed_rider.last_name,
            plate=plate,
        )
        ctx.ride.add_rider_to_team(new_rider, to_entry=target)
        ctx.index[plate] = (target, new_rider)
        ctx.joined += 1
