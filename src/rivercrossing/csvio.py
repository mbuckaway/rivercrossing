# SPDX-License-Identifier: GPL-3.0-only
"""CSV roster import/export: preview-then-commit (spec S7, R-21).

Phase 2 replaces spec S7's two CSV shapes -- relay
``plate,entry_name,type,rider_1..rider_N,notes`` and rider_pooled's
one-row-per-rider ``plate,name,team_name,notes``, selected by the ride's
plate model -- with ONE unified, header-mapped format. The file's actual
header row resolves to canonical fields (:func:`_map_header`) through an
ordered matcher list -- TEAMNAME, TYPE, FIRSTNAME, LASTNAME, NUMBER,
NOTES -- first match per column wins, all case-insensitive, and a column
matching nothing is ignored. Every data row is one RIDER; rows with
neither first nor last name are skipped (the trailing footer/empty rows
registration exports carry), unless they still name a plate/team/type,
which is a missing-name conflict instead of a silent drop. TYPE is
``solo``/``team`` after case folding (blank derives: team when a
TEAMNAME is present, else solo); team rows group by TEAMNAME's
normalized form -- trim, collapse internal whitespace, lowercase, "a
team name is its normalized form" -- across the whole file, never by
adjacency, so "BNBA1" and "BNBA 1" are two teams while "Full Send" and
"full   send" are one. NUMBER auto-assigns sequential numeric plates
from :meth:`Roster.next_free_plate` when blank; under
``rider_pooled`` each rider owns their row's plate, under
``team_relay`` a team's member rows share the team's single plate
(solo rows get their own).

:func:`preview` reads the file and reports every conflict found without
raising for content problems and without writing anything -- to the
filesystem or the roster; :func:`commit` then applies a conflict-free
preview to its roster, atomically (spec S7's own words: "preview first,
commit second, nothing touched on preview"). Only a truly unreadable
*path* (e.g. missing file) propagates as an ``OSError``; a malformed or
wrong-shaped CSV is reported as one or more :class:`ImportConflict`
rows instead ("a missing/malformed header is a conflict, not a crash").

This module's ``ride`` parameter -- the frozen name from
module-skeletons.md's ``csvio.preview(path, ride) -> ImportPreview``
-- is, for now, :class:`rivercrossing.roster.Roster`: EPIC 5's Store
("6 csvio -> store models" in that same doc) does not exist yet in
EPIC 3's build order, so csvio operates directly on the in-memory
roster aggregate until persistence lands. A design write-back should
record this once Store arrives.

**Match/insert/reshape (R-21, spec S7:173-177).** ``commit`` matches a
parsed entry on its plate: an existing entry updates that entry's
name/notes (and a solo match's rider first/last, the same rename the
rider editor performs) in place; a new plate inserts. A row's ride-model
composition -- a relay entry's rider set, a pooled rider's team
membership -- may also *reshape* an existing match, applying every
change through the roster's own mutators (so it is fully audit-logged)
subject to the same lock matrix E3.1.2 already governs edits with:
DRAFT reshapes freely; once started, relay keeps its permanent lock,
while pooled keeps team-to-team moves open per
:func:`~rivercrossing.roster.can_move_rider` (spec S7:171 -- "a changed
team_name is treated as an audited membership move, not a conflict"). A
status/model combination that cannot safely reshape becomes a conflict
at preview time instead of a partial or unaudited mutation. **An entry
present in the roster but absent from the file is
left alone** -- neither the spec nor commit ever deletes on that basis;
only DNF/void (E4) or the rider editor removes an entry with no row.

**Pooled team<->solo conversions are DRAFT-only (the pooled-reshape
follow-on).** A team member's row losing its team name applies via
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

**Team notes (decided 2026-08-09, unified format).** On import, a
team's ``notes`` is every non-empty member row's own notes, joined with
``"; "`` in file order -- no member's note is silently dropped.
:func:`export` writes that joined value back onto the team's first
member row only, leaving the other member rows' notes blank -- so a
commit-then-export-then-preview round trip reproduces the same
``notes`` string.

**Export (E3.3.3).** ``export(ride, path, *, placed=None)`` writes
*ride*'s current roster in the same unified shape :func:`preview`
reads -- one row per rider, header
``FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES`` -- so an export of a
conflict-free preview's target therefore previews clean again (spec
§7's own "export mirrors the columns"; task-briefs.md E3.3.3's
round-trip property). Under ``rider_pooled`` each row carries its
rider's own plate; under ``team_relay`` every member row of a team
carries the team's single plate. Passing *placed* -- a sequence of
:class:`rivercrossing.standings.Placed` from EPIC 6's rankings, the
module's first standings dependency (approved decision D3) -- appends
spec §7's four finished-ride columns ``laps, cards, best_hand,
total_time`` after the roster's own columns, guarded to a FINISHED ride
and filled from the matching entry's Placed row (an entry missing from
*placed* fails loudly; extra rows are ignored). Value formats are
machine-readable, decided for P3: laps an int, cards
``len(result.cards)``, best_hand
:func:`~rivercrossing.standings.hand_name`'s prose, total_time raw
numeric seconds (``repr``-clean) -- human formatting belongs to the
HTML/PDF exports only. The standalone spec §15 standings CSV ships as
:func:`export_standings` (E6.4.2): rows ``place, plate, entry, type,
laps, hand`` -- the ``type`` column carries each row's entry kind
(``team``/``solo``, Phase 3's team/solo results split) -- plus a
raw-seconds ``total_time`` column when asked (R-63).

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
    rider_name_key,
)
from rivercrossing.standings import Placed, hand_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
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

_HEADER_PROBLEM = "missing or malformed header: no first or last name column"
_FINISHED_COLUMNS = ("laps", "cards", "best_hand", "total_time")
_MISSING_NAME_PROBLEM = "missing name"
_UNIFIED_COLUMNS = ("FIRSTNAME", "LASTNAME", "TYPE", "TEAMNAME", "NUMBER", "NOTES")

_TEAM_NAME_PATTERN = re.compile(r"team\s*name", re.IGNORECASE)
_FIRST_NAME_PATTERN = re.compile(r"first\s*name", re.IGNORECASE)
_LAST_NAME_PATTERN = re.compile(r"last\s*name", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"\s*(?:number|plate|bib)\s*", re.IGNORECASE)
_NOTES_PATTERN = re.compile(r"\bnotes?\b", re.IGNORECASE)


class CsvIoError(Exception):
    """Base for every csvio invariant violation this module raises."""


class ImportConflictsPresentError(CsvIoError):
    """commit() was called on a preview that still has conflicts (R-21).

    Nothing is mutated: commit() checks this before touching the
    preview's roster at all.
    """


def _map_header(header_row: Sequence[str]) -> dict[str, int]:
    r"""Map each header column to one canonical field (Phase 2 spec).

    The ordered matcher list is TEAMNAME, TYPE, FIRSTNAME, LASTNAME,
    NUMBER, NOTES; for every column the first matcher that fires claims
    it, and a column matching nothing is ignored. All matching is
    case-insensitive. The canonical export tokens map too -- FIRSTNAME
    via ``first\\s*name``, TEAMNAME via ``team\\s*name``, TYPE via its
    exact token -- so an app-written export round-trips.

    Args:
        header_row: The file's header cells, in column order.

    Returns:
        A mapping of canonical field name to column index. Absent
        fields are omitted; a repeated field keeps its first column.
    """
    matchers: tuple[tuple[str, Callable[[str], bool]], ...] = (
        ("TEAMNAME", lambda header: _TEAM_NAME_PATTERN.search(header) is not None),
        ("TYPE", _is_type_header),
        ("FIRSTNAME", lambda header: _FIRST_NAME_PATTERN.search(header) is not None),
        ("LASTNAME", lambda header: _LAST_NAME_PATTERN.search(header) is not None),
        ("NUMBER", lambda header: _NUMBER_PATTERN.fullmatch(header) is not None),
        ("NOTES", lambda header: _NOTES_PATTERN.search(header) is not None),
    )
    mapping: dict[str, int] = {}
    for index, header in enumerate(header_row):
        for field, matcher in matchers:
            if matcher(header):
                mapping.setdefault(field, index)
                break
    return mapping


def _is_type_header(header: str) -> bool:
    """Return True when *header* names the solo/team discriminator.

    The registration forms ask "Are you riding Solo or on a Team?", so
    a header containing BOTH words is the TYPE column; the app's own
    export writes the exact canonical token ``TYPE``, which must map
    too for the export/import round trip.
    """
    lowered = header.lower()
    if "solo" in lowered and "team" in lowered:
        return True
    return "".join(header.split()).lower() == "type"


def _normalize_team_name(name: str) -> str:
    """Return *name*'s normalized form: the team's identity (Phase 2).

    Trim, collapse every run of internal whitespace to one space, and
    lowercase: "Full Send", " full   send " and "FULL SEND" are one
    team ("full send"), while "BNBA1" and "BNBA 1" stay distinct.
    """
    return " ".join(name.split()).lower()


@dataclass(frozen=True)
class ParsedRider:
    """One rider parsed from a CSV row, not yet applied to any roster.

    A value object, unlike :class:`rivercrossing.roster.Rider`: two
    riders parsed with the same first/last names and plate compare
    equal, which is exactly what a preview assertion needs. ``plate``
    is the rider's own plate under ``rider_pooled``; under
    ``team_relay`` riders stay plateless (the entry owns the plate).
    """

    first_name: str
    last_name: str
    plate: str | None = None

    @property
    def full_name(self) -> str:
        """Return this rider's display name, first and last joined."""
        return " ".join(part for part in (self.first_name.strip(), self.last_name.strip()) if part)


@dataclass(frozen=True)
class ParsedEntry:
    """One structurally valid entry parsed from the CSV (spec S7).

    ``plate`` is already resolved to the same shape
    :meth:`~rivercrossing.roster.Roster.create_solo_entry` and
    :meth:`~rivercrossing.roster.Roster.create_team_entry` expect --
    the entry's own plate under ``team_relay`` (a team's member rows
    share it), the lowest-numbered rider's plate under
    ``rider_pooled`` -- so :func:`commit` can hand these straight to
    those constructors without re-deriving anything. ``display_name``
    is a solo entry's rider full name or a team's normalized name. A
    row with a *content* conflict (a duplicate plate, a missing name,
    an over/under-sized team) still becomes one of these -- its shape
    parsed fine -- but a row that fails shape validation (an unknown
    TYPE, or relay team rows naming two plates) never does (see
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
    duplicate plate or an over-sized team -- ready for
    :func:`commit` to apply without re-reading ``source_path``. A row
    that fails shape validation (an unknown ``type``, a missing rider
    name, a relay team straddling two plates, or a header naming no
    first/last column) contributes to ``conflicts`` only, never to
    ``entries``. ``warnings`` carries non-blocking notices -- a DRAFT
    team below the two-rider floor, a duplicate rider name, or a
    near-duplicate team name -- that do not stop :func:`commit`.
    ``rider_count`` and ``team_count`` are derived from ``entries``
    rather than stored, so they can never drift out of sync with it.
    """

    source_path: Path
    ride: Roster
    entries: tuple[ParsedEntry, ...]
    conflicts: tuple[ImportConflict, ...]
    warnings: tuple[ImportConflict, ...] = ()

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

    The file's header is resolved through :func:`_map_header`; unmapped
    columns are ignored, and a header that names neither a first nor a
    last name column reports the whole file as one header conflict at
    row 1 with no rows parsed (spec S7).

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
        reader = csv.reader(handle)
        try:
            header_row = next(reader)
        except StopIteration:
            conflict = ImportConflict(row=1, problem=_HEADER_PROBLEM)
            return ImportPreview(source_path=path, ride=ride, entries=(), conflicts=(conflict,))
        mapping = _map_header(header_row)
        if "FIRSTNAME" not in mapping and "LASTNAME" not in mapping:
            conflict = ImportConflict(row=1, problem=_HEADER_PROBLEM)
            return ImportPreview(source_path=path, ride=ride, entries=(), conflicts=(conflict,))
        rows, row_conflicts = _read_data_rows(reader, mapping)
    entries, entry_conflicts, entry_warnings = _assemble(rows, ride)
    conflicts = sorted((*row_conflicts, *entry_conflicts), key=lambda conflict: conflict.row)
    warnings = sorted(
        (*entry_warnings, *_duplicate_rider_warnings(rows), *_near_duplicate_team_warnings(rows)),
        key=lambda warning: warning.row,
    )
    return ImportPreview(
        source_path=path,
        ride=ride,
        entries=tuple(entries),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )


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

    The header is the unified ``FIRSTNAME,LASTNAME,TYPE,TEAMNAME,
    NUMBER,NOTES`` -- one row per rider, the same shape :func:`preview`
    reads -- so re-importing the result against an equivalent roster
    previews with zero conflicts (module docstring's round-trip note).
    Passing *placed* -- a finished ride's standings -- appends spec
    §7's four columns (``laps, cards, best_hand, total_time``) after
    the roster's own ones and fills them from each matching entry's
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
    by_plate = _finished_by_plate(ride, placed) if placed is not None else None
    rows: list[list[str]] = []
    for entry in ride.entries:
        entry_rows = _entry_rows(ride, entry)
        if by_plate is not None:
            stats = _stats_values(by_plate[entry.plate])
            entry_rows = [row + stats for row in entry_rows]
        rows.extend(entry_rows)
    header = [*_UNIFIED_COLUMNS]
    if placed is not None:
        header.extend(_FINISHED_COLUMNS)
    _write_csv_rows(path, header, rows)


def export_standings(placed: Sequence[Placed], path: Path, *, show_times: bool = False) -> None:
    """Write *placed* as the spec §15 standings CSV to *path* (E6.4.2).

    Rows are ``place, plate, entry, type, laps, hand`` with a
    ``total_time`` column appended when *show_times* -- raw numeric
    seconds, consistent with :func:`export`'s finished-ride columns
    (CSVs are machine-readable; human formatting is the HTML/PDF
    exports' job). ``type`` is each row's entry kind -- ``team`` or
    ``solo`` from ``Placed.result.kind`` (Phase 3's team/solo results
    split: the caller feeds the two ranked groups Teams-then-Solo, so
    the kind column labels each section and the places are per-kind).
    DNF entries keep their row with their laps and cards (R-33); an
    entry that never crossed renders a blank hand. The write is atomic
    (R-52), exactly like :func:`export`: staged in a same-directory
    temp file, then swapped over *path* with :func:`os.replace`.

    Args:
        placed: Ranked standings, one row each (teams then solo).
        path: The file to write. Replaced atomically; a pre-existing
            file is overwritten wholesale, never truncated in place.
        show_times: Append the ``total_time`` column (R-63: times
            only when the export setting says so).
    """
    header = ["place", "plate", "entry", "type", "laps", "hand"]
    if show_times:
        header.append("total_time")
    rows: list[list[str]] = []
    for placed_row in placed:
        result = placed_row.result
        row = [
            str(placed_row.place),
            result.plate,
            result.name,
            result.kind,
            str(result.laps),
            hand_name(result.hand) if result.cards else "",
        ]
        if show_times:
            row.append(repr(result.total_time))
        rows.append(row)
    _write_csv_rows(path, header, rows)


@dataclass(frozen=True)
class _DataRow:
    """One usable rider row parsed from the file, pre-assembly."""

    row: int
    first_name: str
    last_name: str
    is_team: bool
    team_name: str
    number: str
    notes: str


def _cell(row: Sequence[str], mapping: Mapping[str, int], field: str) -> str:
    """Return *field*'s cell from *row*; missing or blank is ''."""
    index = mapping.get(field)
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _read_data_rows(
    reader: Iterable[Sequence[str]], mapping: Mapping[str, int]
) -> tuple[list[_DataRow], list[ImportConflict]]:
    """Parse every usable data row *reader* yields (Phase 2 spec).

    A row with neither first nor last name is a footer/empty row and is
    skipped -- unless it still names a NUMBER/TEAMNAME/TYPE, in which
    case it is a missing-name conflict (a plated rider is never
    silently dropped). TYPE resolves to solo/team (blank derives from
    TEAMNAME); an unrecognized type value conflicts and is excluded.
    """
    rows: list[_DataRow] = []
    conflicts: list[ImportConflict] = []
    for row_num, raw_row in enumerate(reader, start=2):
        first_name = _cell(raw_row, mapping, "FIRSTNAME")
        last_name = _cell(raw_row, mapping, "LASTNAME")
        type_field = _cell(raw_row, mapping, "TYPE")
        team_raw = _cell(raw_row, mapping, "TEAMNAME")
        number = _cell(raw_row, mapping, "NUMBER")
        notes = _cell(raw_row, mapping, "NOTES")
        if not first_name and not last_name:
            if number or team_raw or type_field:
                conflicts.append(ImportConflict(row_num, _MISSING_NAME_PROBLEM))
            continue
        team_name = _normalize_team_name(team_raw) if team_raw else ""
        kind = _classify_row(type_field, team_name)
        if kind == "solo":
            is_team = False
            team_key = ""
        elif kind == "team":
            is_team = True
            team_key = team_name
        else:
            conflicts.append(ImportConflict(row_num, kind))
            continue
        rows.append(
            _DataRow(
                row=row_num,
                first_name=first_name,
                last_name=last_name,
                is_team=is_team,
                team_name=team_key,
                number=number,
                notes=notes,
            )
        )
    return rows, conflicts


def _classify_row(type_field: str, team_name: str) -> str:
    """Return 'solo', 'team', or the conflict text for *type_field*.

    An explicit TYPE value is authoritative after case folding; blank
    TYPE derives team when a TEAMNAME is present, else solo. An
    explicit team with no team name is a nameless, ungroupable row
    (missing name); any other non-blank TYPE value is unknown.
    """
    lowered = type_field.lower()
    if lowered == "solo":
        return "solo"
    if lowered == "team":
        if not team_name:
            return _MISSING_NAME_PROBLEM
        return "team"
    if lowered == "":
        return "team" if team_name else "solo"
    return f"unknown entry type {type_field!r}"


def _assemble(
    rows: Sequence[_DataRow], ride: Roster
) -> tuple[list[ParsedEntry], list[ImportConflict], list[ImportConflict]]:
    """Turn parsed rows into entries, conflicts and warnings (S7)."""
    if ride.plate_model is PlateModel.TEAM_RELAY:
        return _assemble_relay(rows, ride)
    return _assemble_pooled(rows, ride)


class _PlateAllocator:
    """Assign sequential numeric plates that collide with nothing.

    Blank NUMBER cells auto-assign from :meth:`Roster.next_free_plate`,
    skipping every plate already in use on the roster or named anywhere
    else in the file (R-20: one plate namespace per ride).
    """

    def __init__(self, ride: Roster, explicit_numbers: Iterable[str]) -> None:
        """Reserve explicit and roster plates; find the first free."""
        self._used = {number for number in explicit_numbers if number} | _roster_plates(ride)
        self._next = self._first_free(ride)

    def _first_free(self, ride: Roster) -> int:
        """Return the first integer plate past the roster's own next."""
        candidate = int(ride.next_free_plate())
        while str(candidate) in self._used:
            candidate += 1
        return candidate

    def allocate(self) -> str:
        """Return and reserve the next sequential free plate."""
        plate = str(self._next)
        self._used.add(plate)
        self._next += 1
        while str(self._next) in self._used:
            self._next += 1
        return plate


def _roster_plates(ride: Roster) -> set[str]:
    """Return every plate already claimed in *ride* (R-20)."""
    plates: set[str] = set()
    for entry in ride.entries:
        plates.add(entry.plate)
        plates.update(rider.plate for rider in entry.riders if rider.plate is not None)
    return plates


def _duplicate_plate_problem(plate: str) -> str:
    """Return the conflict text for *plate* repeating in the file."""
    return f"duplicate plate {plate}"


def _team_size_problem(size: int, max_team_size: int) -> str | None:
    """Return the team size conflict text, or None in 2..max (R-12)."""
    if size < MIN_TEAM_SIZE:
        return f"team of {size} rider is below the minimum of {MIN_TEAM_SIZE} (team-under-min)"
    if size > max_team_size:
        return f"team of {size} riders exceeds the maximum of {max_team_size} (team-over-max)"
    return None


_FUZZY_QUOTE_TRANSLATION = str.maketrans("", "", "'\"\u2018\u2019\u201c\u201d")


def _fuzzy_team_key(name: str) -> str:
    """Return *name*'s fuzzy key for near-duplicate team detection.

    Fold case, drop apostrophes/quotes, tokenize on whitespace and drop
    the standalone ``and``/``&`` tokens, then strip every remaining
    non-alphanumeric character and join -- so "BNBA 1" and "BNBA1" key
    to "bnba1", "Good 2 Go" and "Good 2Go" to "good2go", and "Win Win
    More Win" and "Win Win and more Win" to "winwinmorewin".
    """
    folded = name.casefold().translate(_FUZZY_QUOTE_TRANSLATION)
    tokens = [token for token in folded.split() if token not in ("and", "&")]
    return "".join(char for token in tokens for char in token if char.isalnum())


def _duplicate_rider_warnings(rows: Sequence[_DataRow]) -> list[ImportConflict]:
    """Return one warning per rider name that repeats (case-folded).

    Riders are matched by :func:`~rivercrossing.roster.rider_name_key`
    across solo and team rows alike; a key seen twice produces exactly
    one warning naming the first occurrence's trimmed full name, at the
    second occurrence's row. A third occurrence adds nothing.
    """
    first: dict[str, tuple[int, str]] = {}
    warned: set[str] = set()
    warnings: list[ImportConflict] = []
    for row in rows:
        key = rider_name_key(row.first_name, row.last_name)
        if key in warned:
            continue
        if key in first:
            _, full_name = first[key]
            warnings.append(
                ImportConflict(row=row.row, problem=f"duplicate rider name {full_name}")
            )
            warned.add(key)
        else:
            full_name = f"{row.first_name} {row.last_name}".strip()
            first[key] = (row.row, full_name)
    return warnings


def _near_duplicate_team_warnings(rows: Sequence[_DataRow]) -> list[ImportConflict]:
    """Return one warning per pair of near-duplicate team names.

    Two distinct normalized team names that share a fuzzy key
    (:func:`_fuzzy_team_key`) are near-duplicates; the warning names
    both, first-seen first, at the second name's first row.
    """
    first_row: dict[str, int] = {}
    order: list[str] = []
    for row in rows:
        if row.is_team and row.team_name not in first_row:
            first_row[row.team_name] = row.row
            order.append(row.team_name)
    fuzzy_groups: dict[str, list[str]] = {}
    for name in order:
        fuzzy_groups.setdefault(_fuzzy_team_key(name), []).append(name)
    return [
        ImportConflict(
            row=first_row[second],
            problem=f'possible duplicate team name: "{names[0]}" and "{second}"',
        )
        for names in fuzzy_groups.values()
        for second in names[1:]
    ]


def _group_team_rows(rows: Sequence[_DataRow]) -> dict[str, list[_DataRow]]:
    """Group team rows by normalized team name, first-seen order."""
    groups: dict[str, list[_DataRow]] = {}
    for row in rows:
        if row.is_team:
            groups.setdefault(row.team_name, []).append(row)
    return groups


def _find_entry_by_plate(ride: Roster, plate: str) -> Entry | None:
    """Return *ride*'s entry whose own plate equals *plate*, if any."""
    for entry in ride.entries:
        if entry.plate == plate:
            return entry
    return None


def _relay_composition_changed(existing: Entry, parsed: ParsedEntry) -> bool:
    """Return True if *parsed*'s riders/type differ from *existing*'s.

    A solo entry's one "rider" is just its own display name (S1), so
    a solo-to-solo match is never a composition change -- renaming it
    is exactly :func:`_update_solo_entry`'s job, not a reshape.
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
    """Return a conflict if a matched relay entry's shape is unsafe now.

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


def _plate_disagreement_problem(numbers: set[str]) -> str:
    """Return the conflict text for relay team rows naming plates."""
    ordered = ", ".join(sorted(numbers))
    return f"team rows carry different plates ({ordered})"


def _assemble_relay(
    rows: Sequence[_DataRow], ride: Roster
) -> tuple[list[ParsedEntry], list[ImportConflict], list[ImportConflict]]:
    """Build relay entries: solo rows plus normalized-name team groups.

    A team's member rows share the team's single plate: the rows must
    name one plate between them (or none, which auto-assigns); rows
    naming two plates are a shape conflict and contribute no entry.
    """
    groups = _group_team_rows(rows)
    allocator = _PlateAllocator(ride, (row.number for row in rows))
    plan: list[tuple[int, _DataRow | list[_DataRow]]] = []
    seen_groups: set[str] = set()
    for row in rows:
        if not row.is_team:
            plan.append((row.row, row))
        elif row.team_name not in seen_groups:
            seen_groups.add(row.team_name)
            plan.append((row.row, groups[row.team_name]))
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    warnings: list[ImportConflict] = []
    seen_plates: set[str] = set()
    for anchor_row, payload in plan:
        if isinstance(payload, list):
            parsed, team_conflicts, team_warnings = _relay_team_entry(payload, allocator, ride)
        else:
            parsed, problem = _relay_solo_entry(payload, allocator, ride)
            team_conflicts = [problem] if problem is not None else []
            team_warnings = []
        if parsed is None:
            # parsed is None exactly when the team's rows named several
            # plates, and that producer always sets *team_conflicts*.
            conflicts.append(ImportConflict(anchor_row, team_conflicts[0]))
            continue
        if parsed.plate in seen_plates:
            conflicts.append(ImportConflict(anchor_row, _duplicate_plate_problem(parsed.plate)))
        else:
            seen_plates.add(parsed.plate)
        entries.append(parsed)
        conflicts.extend(ImportConflict(anchor_row, problem) for problem in team_conflicts)
        warnings.extend(ImportConflict(anchor_row, problem) for problem in team_warnings)
    return entries, conflicts, warnings


def _relay_solo_entry(
    row: _DataRow, allocator: _PlateAllocator, ride: Roster
) -> tuple[ParsedEntry, str | None]:
    """Build one relay solo ParsedEntry from *row*."""
    plate = row.number or allocator.allocate()
    parsed_rider = ParsedRider(first_name=row.first_name, last_name=row.last_name)
    parsed = ParsedEntry(
        plate=plate,
        display_name=parsed_rider.full_name,
        type=EntryType.SOLO,
        riders=(parsed_rider,),
        notes=row.notes,
    )
    existing = _find_entry_by_plate(ride, plate)
    problem = _relay_structural_problem(existing, parsed, ride.status)
    return parsed, problem


def _relay_team_entry(
    group_rows: list[_DataRow], allocator: _PlateAllocator, ride: Roster
) -> tuple[ParsedEntry | None, list[str], list[str]]:
    """Build one relay team ParsedEntry from *group_rows* (S7).

    Returns the parsed entry plus two problem lists: the team's
    blocking conflicts (a plate disagreement, an over-max size, a
    non-DRAFT under-min size, or a structural reshape the ride's lock
    matrix forbids) and its non-blocking warnings (a DRAFT under-min
    size). The under-min case never skips the structural check.
    """
    numbers = {row.number for row in group_rows if row.number}
    if len(numbers) > 1:
        return None, [_plate_disagreement_problem(numbers)], []
    plate = next(iter(numbers), "")
    if not plate:
        plate = allocator.allocate()
    riders = tuple(
        ParsedRider(first_name=row.first_name, last_name=row.last_name) for row in group_rows
    )
    notes = "; ".join(row.notes for row in group_rows if row.notes)
    parsed = ParsedEntry(
        plate=plate,
        display_name=group_rows[0].team_name,
        type=EntryType.TEAM,
        riders=riders,
        notes=notes,
    )
    conflicts: list[str] = []
    warnings: list[str] = []
    size_problem = _team_size_problem(len(riders), ride.max_team_size)
    if size_problem is not None:
        if len(riders) < MIN_TEAM_SIZE and ride.status is RideStatus.DRAFT:
            warnings.append(size_problem)
        else:
            conflicts.append(size_problem)
    if len(riders) <= ride.max_team_size:
        existing = _find_entry_by_plate(ride, plate)
        structural = _relay_structural_problem(existing, parsed, ride.status)
        if structural is not None:
            conflicts.append(structural)
    return parsed, conflicts, warnings


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


def _pooled_solo_problem(
    index: Mapping[str, tuple[Entry, Rider]], plate: str, status: RideStatus
) -> str | None:
    """Return a conflict if a pooled solo row's plate must leave a team.

    A matched plate that currently belongs to a solo entry is the
    match/update path; one belonging to a team member is a team->solo
    conversion, gated to DRAFT by spec S1.
    """
    owner = index.get(plate)
    if owner is not None and owner[0].type is EntryType.TEAM and not can_edit_structure(status):
        return _team_to_solo_problem(status)
    return None


def _assemble_pooled(
    rows: Sequence[_DataRow], ride: Roster
) -> tuple[list[ParsedEntry], list[ImportConflict], list[ImportConflict]]:
    """Build pooled entries: every row keeps its own plate (S1)."""
    existing_index = _pooled_owner_index(ride)
    allocator = _PlateAllocator(ride, (row.number for row in rows))
    solo_entries: list[ParsedEntry] = []
    groups: dict[str, list[tuple[int, ParsedRider, str]]] = {}
    conflicts: list[ImportConflict] = []
    seen_plates: set[str] = set()
    for row in rows:
        plate = row.number or allocator.allocate()
        if plate in seen_plates:
            conflicts.append(ImportConflict(row.row, _duplicate_plate_problem(plate)))
        seen_plates.add(plate)
        parsed_rider = ParsedRider(first_name=row.first_name, last_name=row.last_name, plate=plate)
        if row.is_team:
            groups.setdefault(row.team_name, []).append((row.row, parsed_rider, row.notes))
            continue
        problem = _pooled_solo_problem(existing_index, plate, ride.status)
        if problem is not None:
            conflicts.append(ImportConflict(row.row, problem))
        solo_entries.append(
            ParsedEntry(
                plate=plate,
                display_name=parsed_rider.full_name,
                type=EntryType.SOLO,
                riders=(parsed_rider,),
                notes=row.notes,
            )
        )
    team_entries, team_conflicts, team_warnings = _assemble_pooled_teams(groups, ride)
    conflicts.extend(team_conflicts)
    return [*solo_entries, *team_entries], conflicts, team_warnings


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
) -> tuple[list[ParsedEntry], list[ImportConflict], list[ImportConflict]]:
    """Build one team ParsedEntry per team_name group (spec S7, R-12).

    A group of exactly one rider still becomes a team entry -- the
    2026-08-09 follow-on decision defers the 2-rider floor to this
    preview-time check rather than refusing the row outright. While
    the ride is DRAFT that check is a non-blocking "team-under-min"
    warning; once started it is a blocking conflict. The under-min
    case never skips the team's structural-conflict checks. ``notes``
    joins every non-empty member's own row notes with "; "
    (2026-08-09, module docstring).
    """
    existing_index = _pooled_owner_index(ride)
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    warnings: list[ImportConflict] = []
    for team_name, rows in groups.items():
        riders = tuple(rider for _, rider, _ in rows)
        notes = "; ".join(note for _, _, note in rows if note)
        plate = _lowest_rider_plate(riders)
        entries.append(
            ParsedEntry(
                plate=plate,
                display_name=team_name,
                type=EntryType.TEAM,
                riders=riders,
                notes=notes,
            )
        )
        size_problem = _team_size_problem(len(riders), ride.max_team_size)
        if size_problem is not None:
            if len(riders) < MIN_TEAM_SIZE and ride.status is RideStatus.DRAFT:
                warnings.append(ImportConflict(row=rows[0][0], problem=size_problem))
            else:
                conflicts.append(ImportConflict(row=rows[0][0], problem=size_problem))
        if len(riders) <= ride.max_team_size:
            conflicts.extend(_pooled_team_structural_conflicts(rows, existing_index, ride.status))
    return entries, conflicts, warnings


def _lowest_rider_plate(riders: Sequence[ParsedRider]) -> str:
    """Return the numerically lowest rider plate (S1's "adopts...")."""
    return min(cast("str", rider.plate) for rider in riders)


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
                conflicts.append(ImportConflict(row_num, _pooled_move_problem(status)))
            continue
        if owner is not None:  # currently solo, converting to a team member
            if not can_edit_structure(status):
                conflicts.append(ImportConflict(row_num, _solo_to_team_problem(status)))
            continue
        if target is not None and not can_move_rider(status, PlateModel.RIDER_POOLED):
            conflicts.append(ImportConflict(row_num, _pooled_move_problem(status)))
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
        if _update_solo_entry(ctx.ride, entry, parsed):
            ctx.updated += 1
        ctx.index[parsed.plate] = (entry, existing_rider)
        ctx.extracted += 1
        return
    if _update_solo_entry(ctx.ride, existing_entry, parsed):
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
    if len(riders) == 1:
        entry = ctx.ride.create_team_entry_of_one(
            display_name=parsed.display_name, rider=riders[0], plate=None
        )
    else:
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


def _commit_relay(ride: Roster, entries: Sequence[ParsedEntry]) -> tuple[int, int]:
    """Apply every parsed relay entry: insert, reshape, or rename it."""
    inserted = 0
    updated = 0
    for parsed in entries:
        existing = _find_entry_by_plate(ride, parsed.plate)
        if existing is None:
            _insert_relay_entry(ride, parsed)
            inserted += 1
        elif existing.type is EntryType.SOLO and parsed.type is EntryType.SOLO:
            if _update_solo_entry(ride, existing, parsed):
                updated += 1
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
    elif len(parsed.riders) == 1:
        parsed_rider = parsed.riders[0]
        entry = ride.create_team_entry_of_one(
            display_name=parsed.display_name,
            rider=Rider(first_name=parsed_rider.first_name, last_name=parsed_rider.last_name),
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


def _update_solo_entry(ride: Roster, entry: Entry, parsed: ParsedEntry) -> bool:
    """Rename a matched solo entry's rider and display; True if changed.

    Mirrors the rider editor's own save path: the rider's first/last
    fields are updated in place and, when the display name changes,
    ``update_entry`` logs the rename (name fixes stay open in any
    ride state).
    """
    parsed_rider = parsed.riders[0]
    rider = entry.riders[0]
    rider_changed = False
    if rider.first_name != parsed_rider.first_name:
        rider.first_name = parsed_rider.first_name
        rider_changed = True
    if rider.last_name != parsed_rider.last_name:
        rider.last_name = parsed_rider.last_name
        rider_changed = True
    changes: dict[str, str] = {}
    if entry.display_name != parsed.display_name:
        changes["display_name"] = parsed.display_name
    if entry.notes != parsed.notes:
        changes["notes"] = parsed.notes
    if changes:
        ride.update_entry(entry, **changes)
    return rider_changed or bool(changes)


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


def _entry_rows(ride: Roster, entry: Entry) -> list[list[str]]:
    """Return one unified CSV row per *entry*'s rider (spec S7).

    A team's ``notes`` is written on its first member row only -- the
    export half of the notes-join rule (module docstring) -- so
    re-importing joins it right back onto that one value; a solo entry
    has only the one row, so its own notes always land on it.
    """
    type_field = "team" if entry.type is EntryType.TEAM else "solo"
    team_name = entry.display_name if entry.type is EntryType.TEAM else ""
    return [
        [
            rider.first_name,
            rider.last_name,
            type_field,
            team_name,
            _rider_number(ride, entry, rider),
            entry.notes if index == 0 else "",
        ]
        for index, rider in enumerate(entry.riders)
    ]


def _rider_number(ride: Roster, entry: Entry, rider: Rider) -> str:
    """Return one exported row's NUMBER cell for its plate model (S1).

    ``rider_pooled`` riders each carry their own plate;
    ``team_relay`` riders carry none, so every member row of a team
    exports the entry's own shared plate.
    """
    if ride.plate_model is PlateModel.RIDER_POOLED:
        return cast("str", rider.plate)
    return entry.plate
