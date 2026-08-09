# SPDX-License-Identifier: GPL-3.0-only
"""CSV roster import: preview-then-commit (spec S7, R-21, E3.3.1).

Spec section 7 defines two CSV shapes, selected by the target ride's
plate model (S1): ``team_relay``'s
``plate,entry_name,type,rider_1..rider_N,notes`` (N =
``ride.max_team_size``, ``type`` one of ``solo``/``teamN``) or
``rider_pooled``'s one-row-per-rider ``plate,name,team_name,notes``,
where riders sharing a ``team_name`` form a team (blank = solo).
:func:`preview` reads either shape, reports every conflict found
without raising for content problems, and never writes anything --
to the file, or to the given roster -- so an operator can see exactly
what an import would do before committing it (R-21's "preview first,
commit second, nothing touched on preview").

Only a truly unreadable *path* (e.g. missing file) propagates as an
``OSError``; a malformed or wrong-shaped CSV is reported as one or
more :class:`ImportConflict` rows instead, per spec S7's "a
missing/malformed header is a conflict, not a crash."

This module's ``ride`` parameter -- the frozen name from
module-skeletons.md's ``csvio.preview(path, ride) -> ImportPreview``
-- is, for now, :class:`rivercrossing.roster.Roster`: EPIC 5's Store
("6 csvio -> store models" in that same doc) does not exist yet in
EPIC 3's build order, so csvio operates directly on the in-memory
roster aggregate until persistence lands. A design write-back should
record this once Store arrives.

E3.3.2 (not built here) will add ``commit(preview) -> ImportReport``:
:class:`ImportPreview` already carries everything that call needs --
the target ``ride``, and every structurally valid :class:`ParsedEntry`
-- so it can apply an import without re-reading *path*. The
2026-08-09 follow-on decision that lets a rider_editor_dlg team sit at
size 1 in DRAFT (roster.py's ``create_team_entry_of_one``) extends
here too: a rider_pooled team of exactly one rider is not refused at
preview time, only flagged as a "team-under-min" conflict, mirroring
how a relay ``teamN`` outside 2..max_team_size already surfaces as a
conflict rather than an exception.
"""

import csv
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rivercrossing.roster import MIN_TEAM_SIZE, EntryType, PlateModel, Roster

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

_HEADER_PROBLEM = "missing or malformed header for this ride's plate model"
_MISSING_NAME_PROBLEM = "missing name"
_POOLED_HEADER = ("plate", "name", "team_name", "notes")
_TEAM_TYPE_PATTERN = re.compile(r"team(\d+)")


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
    under ``rider_pooled`` -- so E3.3.2's commit can hand these
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
    duplicate plate or a missing name -- ready for E3.3.2's
    ``commit`` to apply without re-reading ``source_path``. A row
    that fails shape validation (an unrecognized ``type``, a
    ``teamN`` outside 2..max_team_size, or a malformed/mismatched
    header) contributes to ``conflicts`` only, never to ``entries``.
    ``rider_count`` and ``team_count`` are derived from ``entries``
    rather than stored, so they can never drift out of sync with it.
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


def preview(path: Path, ride: Roster) -> ImportPreview:
    """Preview a CSV import against *ride*; write nothing (R-21).

    The file's header must match *ride*'s plate_model exactly, or the
    whole file is reported as one header conflict at row 1 and no
    rows are parsed (spec S7).

    Args:
        path: The CSV file to read. Never written to.
        ride: The roster this import would apply to; its
            plate_model, max_team_size and existing entries drive
            every check below. Never mutated.

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


def _expected_header(ride: Roster) -> list[str]:
    """Return the CSV header *ride*'s plate_model requires (spec S7)."""
    if ride.plate_model is PlateModel.TEAM_RELAY:
        rider_cols = [f"rider_{i}" for i in range(1, ride.max_team_size + 1)]
        return ["plate", "entry_name", "type", *rider_cols, "notes"]
    return list(_POOLED_HEADER)


def _field(row: Mapping[str, str | None], column: str) -> str:
    """Return *column* from *row*, stripped; blank/None becomes ''."""
    return (row.get(column) or "").strip()


def _existing_plates(ride: Roster) -> set[str]:
    """Return every plate already claimed by *ride*'s entries/riders."""
    plates: set[str] = set()
    for entry in ride.entries:
        plates.add(entry.plate)
        plates.update(rider.plate for rider in entry.riders if rider.plate is not None)
    return plates


def _duplicate_plate_problem(plate: str, seen_plates: set[str]) -> str | None:
    """Return a conflict message when *plate* repeats, else None."""
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


def _preview_relay(reader: csv.DictReader[str], path: Path, ride: Roster) -> ImportPreview:
    """Parse every team_relay data row *reader* yields (spec S7)."""
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    seen_plates = _existing_plates(ride)
    for row_num, row in enumerate(reader, start=2):
        parsed = _shape_relay_row(row, max_team_size=ride.max_team_size)
        if isinstance(parsed, str):
            conflicts.append(ImportConflict(row=row_num, problem=parsed))
            continue
        problem = _missing_name_problem(parsed) or _duplicate_plate_problem(
            parsed.plate, seen_plates
        )
        if problem is not None:
            conflicts.append(ImportConflict(row=row_num, problem=problem))
        seen_plates.add(parsed.plate)
        entries.append(parsed)
    return ImportPreview(
        source_path=path, ride=ride, entries=tuple(entries), conflicts=tuple(conflicts)
    )


# -------------------------------------------------------- rider_pooled


def _preview_pooled(reader: csv.DictReader[str], path: Path, ride: Roster) -> ImportPreview:
    """Parse every rider_pooled row, grouped by team_name (spec S7)."""
    solo_entries: list[ParsedEntry] = []
    groups: dict[str, list[tuple[int, ParsedRider]]] = {}
    conflicts: list[ImportConflict] = []
    seen_plates = _existing_plates(ride)
    for row_num, row in enumerate(reader, start=2):
        plate = _field(row, "plate")
        name = _field(row, "name")
        team_name = _field(row, "team_name")
        notes = _field(row, "notes")
        missing = _MISSING_NAME_PROBLEM if not name else None
        problem = missing or _duplicate_plate_problem(plate, seen_plates)
        if problem is not None:
            conflicts.append(ImportConflict(row=row_num, problem=problem))
        seen_plates.add(plate)
        rider = ParsedRider(name=name, plate=plate)
        if not team_name:
            # A pooled solo row is one rider *and* one entry, so its
            # notes map 1:1 (relay's solo rows do the same). A team's
            # notes are a per-rider column with no defined merge rule
            # across a group's several rows -- left "" pending that.
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
            groups.setdefault(team_name, []).append((row_num, rider))
    team_entries, team_conflicts = _assemble_pooled_teams(groups)
    conflicts.extend(team_conflicts)
    entries = (*solo_entries, *team_entries)
    return ImportPreview(source_path=path, ride=ride, entries=entries, conflicts=tuple(conflicts))


def _assemble_pooled_teams(
    groups: Mapping[str, Sequence[tuple[int, ParsedRider]]],
) -> tuple[tuple[ParsedEntry, ...], list[ImportConflict]]:
    """Build one team ParsedEntry per team_name group (spec S7, R-12).

    A group of exactly one rider still becomes a team entry -- the
    2026-08-09 follow-on decision defers rider_pooled's own 2-rider
    floor to this preview-time "team-under-min" conflict rather than
    refusing the row outright, mirroring how relay's floor already
    surfaces as a "team size must be between..." conflict.
    """
    entries: list[ParsedEntry] = []
    conflicts: list[ImportConflict] = []
    for team_name, rows in groups.items():
        riders = tuple(rider for _, rider in rows)
        plate = min((rider.plate for rider in riders if rider.plate), key=int)
        entries.append(
            ParsedEntry(plate=plate, display_name=team_name, type=EntryType.TEAM, riders=riders)
        )
        if len(riders) < MIN_TEAM_SIZE:
            conflicts.append(
                ImportConflict(
                    row=rows[0][0],
                    problem=(
                        f"team of {len(riders)} rider is below the minimum of "
                        f"{MIN_TEAM_SIZE} (team-under-min)"
                    ),
                )
            )
    return tuple(entries), conflicts
