# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.csvio (E3.3.1 preview, E3.3.2-3 commit).

Spec S7's column spec and R-21 are this task's specification, narrowed
by task-briefs.md's own named cases: ``preview`` never writes anything
-- to the filesystem or to the target roster -- and reports exact
rider/team counts plus a per-row conflict list before ``commit``
applies them. ``commit`` refuses outright while any conflict remains
(nothing mutated), otherwise matches on plate (update in place),
inserts new plates, and reshapes team membership through the
roster's own mutators (so every change is audit-logged) subject to
the ride's lock matrix: DRAFT reshapes freely, including a pooled
team<->solo conversion; once started, relay keeps its permanent lock
while pooled keeps team-to-team moves *and* a brand-new plate joining
an existing team open through RUNNING/REOPENED (spec S7:171/177) --
only a solo<->team *conversion* stays DRAFT-only past that point.

E3.3.2's binding semantics change one piece of E3.3.1 behaviour: a CSV
plate that already matches an existing roster entry is no longer a
"duplicate" conflict by itself -- it is the match/update path -- so
``test_preview_relay_plate_colliding_with_an_existing_roster_entry_is_flagged``
became
``test_preview_relay_plate_matching_an_existing_roster_entry_is_not_a_conflict``
below; duplicate detection now only fires within one file. The
pooled-reshape follow-on (``add_rider_to_team``/
``extract_rider_to_solo`` landing in roster.py) turns three former
"unsupported, always conflicts" tests into DRAFT-allowed scenarios,
each paired with a RUNNING/FINISHED variant that still conflicts,
naming the lock.

Fixtures live in ``tests/unit/fixtures/csv/``: ``clean_180.csv`` is
the EPIC-shaped clean sample (team_relay, 120 solo + 15 team4 rows =
180 riders / 15 teams / 0 conflicts); ``clean_pooled.csv`` is the
rider_pooled equivalent (4 solos + two teams via team_name grouping);
``dup_plate.csv``, ``missing_name.csv``, ``team_over_max.csv`` and
``team_under_min_pooled.csv`` are minimal negatives, each producing
exactly one named conflict at a pinned row. Every other conflict shape
(unknown type, a malformed/mismatched header, an empty file, a blank
pooled name, the status/reshape conflicts) is built inline against
``tmp_path`` or a directly-seeded roster -- small enough not to need a
committed fixture of its own.

Written FIRST, against a module that does not exist yet: this file is
red until rivercrossing/csvio.py lands.
"""

import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.cards import Card
from rivercrossing.csvio import (
    CsvIoError,
    ImportConflict,
    ImportConflictsPresentError,
    ParsedEntry,
    ParsedRider,
    commit,
    export,
    export_standings,
    preview,
)
from rivercrossing.hands import best_hand
from rivercrossing.ride import RideStatus
from rivercrossing.roster import (
    DEFAULT_MAX_TEAM_SIZE,
    EntryMode,
    EntryType,
    PlateModel,
    Rider,
    Roster,
)
from rivercrossing.standings import EntryResult, Placed

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "csv"

_HEADER_PROBLEM = "missing or malformed header for this ride's plate model"
_STRUCTURAL_PROBLEM = "only new plates or name fixes are allowed"
_MOVE_NOT_ALLOWED_PROBLEM = "team change requires DRAFT, RUNNING or REOPENED"
_TEAM_TO_SOLO_LOCKED_PROBLEM = "converting a team member to a solo entry requires DRAFT"
_SOLO_TO_TEAM_LOCKED_PROBLEM = "converting a solo rider into a team member requires DRAFT"

# ------------------------------------------------------------- helpers


def _relay_roster(*, max_team_size: int = DEFAULT_MAX_TEAM_SIZE) -> Roster:
    """Build a mixed, team_relay roster for preview()'s *ride* param."""
    return Roster(
        entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY, max_team_size=max_team_size
    )


def _pooled_roster(*, max_team_size: int = DEFAULT_MAX_TEAM_SIZE) -> Roster:
    """Build a mixed, rider_pooled roster for preview()'s *ride*."""
    return Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=max_team_size,
    )


def _write_csv(tmp_path: Path, lines: list[str]) -> Path:
    """Write *lines* (header first) as ``tmp_path/riders.csv``."""
    path = tmp_path / "riders.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _relay_header(max_team_size: int) -> str:
    """Build a team_relay header sized to *max_team_size* (spec S7)."""
    rider_cols = ",".join(f"rider_{i}" for i in range(1, max_team_size + 1))
    return f"plate,entry_name,type,{rider_cols},notes"


class _RelayRow(NamedTuple):
    """One team_relay data row's fields, before comma-joining."""

    plate: str
    entry_name: str
    type_field: str
    rider_names: tuple[str, ...] = ()
    notes: str = ""


def _relay_line(row: _RelayRow, *, max_team_size: int) -> str:
    """Render *row* as one team_relay CSV line, padded to the header."""
    slots = [*row.rider_names, *([""] * (max_team_size - len(row.rider_names)))]
    return ",".join([row.plate, row.entry_name, row.type_field, *slots, row.notes])


class _PooledRow(NamedTuple):
    """One rider_pooled data row's fields, before comma-joining."""

    plate: str
    name: str
    team_name: str = ""
    notes: str = ""


def _pooled_line(row: _PooledRow) -> str:
    """Render *row* as one rider_pooled CSV line (spec S7)."""
    return f"{row.plate},{row.name},{row.team_name},{row.notes}"


_POOLED_HEADER_LINE = "plate,name,team_name,notes"


# ======================================================= clean fixtures


def test_preview_clean_180_reports_exact_counts_and_no_conflicts() -> None:
    """The EPIC-shaped fixture: 180 riders, 15 teams, no conflicts."""
    roster = _relay_roster()

    result = preview(FIXTURES / "clean_180.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (180, 15, ())


def test_preview_clean_180_entries_count_matches_135_rows() -> None:
    """135 rows (120 solo + 15 team4) parse into 135 entries."""
    roster = _relay_roster()

    result = preview(FIXTURES / "clean_180.csv", roster)

    assert len(result.entries) == 135


def test_preview_clean_pooled_reports_exact_counts_and_no_conflicts() -> None:
    """The pooled sample previews as 9 riders/2 teams/0 conflicts."""
    roster = _pooled_roster()

    result = preview(FIXTURES / "clean_pooled.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (9, 2, ())


def test_preview_pooled_clean_sample_falcons_entry_derives_plate_from_lowest_rider() -> None:
    """rider_pooled: a team's plate is its lowest rider's plate (S1)."""
    roster = _pooled_roster()

    result = preview(FIXTURES / "clean_pooled.csv", roster)

    assert result.entries[4] == ParsedEntry(
        plate="10",
        display_name="Falcons",
        type=EntryType.TEAM,
        riders=(
            ParsedRider(name="Elin Novak", plate="10"),
            ParsedRider(name="Faisal Rahman", plate="11"),
            ParsedRider(name="Gita Sundaram", plate="12"),
        ),
    )


# ============================================ named negative fixtures


def test_preview_dup_plate_fixture_reports_exactly_one_conflict_at_row_3() -> None:
    """dup_plate.csv's second plate-1 row is the only conflict."""
    roster = _relay_roster()

    result = preview(FIXTURES / "dup_plate.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        2,
        0,
        (ImportConflict(row=3, problem="duplicate plate 1"),),
    )


def test_preview_missing_name_fixture_reports_exactly_one_conflict_at_row_2() -> None:
    """missing_name.csv's blank entry_name is the only conflict."""
    roster = _relay_roster()

    result = preview(FIXTURES / "missing_name.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        1,
        0,
        (ImportConflict(row=2, problem="missing name"),),
    )


def test_preview_team_over_max_fixture_reports_exactly_one_conflict_at_row_2() -> None:
    """team_over_max.csv's team5 row exceeds max_team_size(4)."""
    roster = _relay_roster()

    result = preview(FIXTURES / "team_over_max.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        0,
        0,
        (ImportConflict(row=2, problem="team size must be between 2 and 4, got 5"),),
    )


def test_preview_team_under_min_pooled_fixture_reports_exactly_one_conflict_at_row_5() -> None:
    """The lone "Solo Team" rider is the only conflict found here."""
    roster = _pooled_roster()

    result = preview(FIXTURES / "team_under_min_pooled.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        4,
        2,
        (
            ImportConflict(
                row=5, problem="team of 1 rider is below the minimum of 2 (team-under-min)"
            ),
        ),
    )


# ==================================================== shape conflicts


def test_preview_relay_unknown_type_value_is_a_shape_conflict_excluded_from_counts(
    tmp_path: Path,
) -> None:
    """An unrecognized type token conflicts, excluded from counts."""
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="triple"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        0,
        0,
        (ImportConflict(row=2, problem="unknown entry type 'triple'"),),
    )


def test_preview_relay_ride_with_pooled_header_reports_header_conflict(tmp_path: Path) -> None:
    """A pooled-shaped header on a relay ride conflicts, not crashes."""
    path = _write_csv(tmp_path, ["plate,name,team_name,notes", "1,Alex,,"])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.entries, result.conflicts) == (
        (),
        (ImportConflict(row=1, problem=_HEADER_PROBLEM),),
    )


def test_preview_pooled_ride_with_relay_header_reports_header_conflict(tmp_path: Path) -> None:
    """A relay-shaped header on a pooled ride also conflicts."""
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.entries, result.conflicts) == (
        (),
        (ImportConflict(row=1, problem=_HEADER_PROBLEM),),
    )


def test_preview_empty_file_reports_header_conflict(tmp_path: Path) -> None:
    """A zero-byte file has no header -- reported, not a crash."""
    path = tmp_path / "riders.csv"
    path.write_text("", encoding="utf-8")
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=1, problem=_HEADER_PROBLEM),)


# =================================================== content conflicts


def test_preview_pooled_row_with_blank_name_reports_missing_name(tmp_path: Path) -> None:
    """A blank rider name in the pooled form is missing-name too."""
    path = _write_csv(tmp_path, ["plate,name,team_name,notes", "1,,,"])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.conflicts) == (
        1,
        (ImportConflict(row=2, problem="missing name"),),
    )


def test_preview_relay_team_row_with_a_blank_rider_slot_reports_missing_name(
    tmp_path: Path,
) -> None:
    """A blank rider_i within a well-shaped teamN row also conflicts."""
    row = "121,Big Team,team2,Alex,,,,"
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=2, problem="missing name"),)


def test_preview_relay_plate_matching_an_existing_roster_entry_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """A CSV plate matching an existing entry is the match/update path.

    E3.3.2's binding semantics: "Match on plate = update in place" --
    this plate is no longer a "duplicate" merely for existing on the
    roster already (E3.3.1's stricter reading). Only a plate repeated
    *within the file itself* is still a duplicate-plate conflict.
    """
    roster = _relay_roster()
    roster.create_solo_entry(name="Existing Rider", plate="1")
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Existing Rider", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])

    result = preview(path, roster)

    assert result.conflicts == ()


def test_preview_row_with_both_missing_name_and_duplicate_plate_reports_missing_name_only(
    tmp_path: Path,
) -> None:
    """Missing name wins; at most one conflict is reported per row."""
    row1 = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    row2 = _relay_line(
        _RelayRow(plate="1", entry_name="", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row1, row2])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=3, problem="missing name"),)


def test_preview_relay_file_with_two_independent_conflicts_reports_both(
    tmp_path: Path,
) -> None:
    """Independent conflicts across a file all appear, in row order."""
    row1 = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="triple"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    row2 = _relay_line(
        _RelayRow(plate="2", entry_name="", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row1, row2])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (
        ImportConflict(row=2, problem="unknown entry type 'triple'"),
        ImportConflict(row=3, problem="missing name"),
    )


# ============================================== parsed entry shape


def test_preview_relay_solo_row_parses_into_one_rider_named_like_the_entry(
    tmp_path: Path,
) -> None:
    """A solo row's rider takes the entry's own display name (S1)."""
    row = _relay_line(
        _RelayRow(plate="9", entry_name="Marc Tremblay", type_field="solo", notes="late scratch"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="9",
            display_name="Marc Tremblay",
            type=EntryType.SOLO,
            riders=(ParsedRider(name="Marc Tremblay"),),
            notes="late scratch",
        ),
    )


def test_preview_pooled_solo_row_carries_its_own_notes_onto_the_entry(tmp_path: Path) -> None:
    """A pooled solo row's notes map 1:1 onto its one-rider entry."""
    path = _write_csv(tmp_path, ["plate,name,team_name,notes", "5,Alex Ferreira,,late scratch"])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="5",
            display_name="Alex Ferreira",
            type=EntryType.SOLO,
            riders=(ParsedRider(name="Alex Ferreira", plate="5"),),
            notes="late scratch",
        ),
    )


def test_preview_relay_team_row_riders_carry_no_plate_of_their_own(tmp_path: Path) -> None:
    """team_relay: plates are direct; the entry's riders carry none."""
    row = _relay_line(
        _RelayRow(plate="7", entry_name="Team A", type_field="team2", rider_names=("Alex", "Bo")),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="7",
            display_name="Team A",
            type=EntryType.TEAM,
            riders=(ParsedRider(name="Alex"), ParsedRider(name="Bo")),
        ),
    )


# ================================================ writes-nothing guard


def test_preview_leaves_the_source_directory_untouched(tmp_path: Path) -> None:
    """Preview reads the file but writes nothing to its directory."""
    path = tmp_path / "riders.csv"
    path.write_bytes((FIXTURES / "clean_180.csv").read_bytes())
    before = (sorted(tmp_path.iterdir()), path.stat().st_mtime, path.stat().st_size)
    roster = _relay_roster()

    preview(path, roster)

    after = (sorted(tmp_path.iterdir()), path.stat().st_mtime, path.stat().st_size)
    assert before == after


def test_preview_does_not_mutate_the_target_roster() -> None:
    """Preview never appends audit events or entries to the roster."""
    roster = _relay_roster()
    before_audit_log = roster.audit_log

    preview(FIXTURES / "clean_180.csv", roster)

    assert (roster.audit_log, roster.entries) == (before_audit_log, ())


def test_preview_records_the_given_source_path_and_ride() -> None:
    """ImportPreview carries the exact path and roster it came from."""
    roster = _relay_roster()
    path = FIXTURES / "clean_180.csv"

    result = preview(path, roster)

    assert (result.source_path, result.ride) == (path, roster)


def test_preview_missing_file_raises_file_not_found_error(tmp_path: Path) -> None:
    """A nonexistent path propagates FileNotFoundError, unwrapped.

    Asserts ``.filename`` directly rather than matching the exception
    message text: CPython's ``OSError.__str__`` embeds ``filename``
    via ``repr()``, so on Windows every backslash in the path doubles
    in the rendered message and ``re.escape(str(missing))`` never
    matches it (CI run 31344728049, windows-latest only -- POSIX
    paths have no backslashes to double, so macOS/Linux stayed green
    with the old, message-matching form).
    """
    roster = _relay_roster()
    missing = tmp_path / "does-not-exist.csv"

    with pytest.raises(FileNotFoundError) as excinfo:
        preview(missing, roster)

    assert excinfo.value.filename == str(missing)


# ==================================================== N boundary (T-4)


@pytest.mark.parametrize(
    ("team_size", "expect_conflict"),
    [
        (1, True),  # min - 1
        (2, False),  # min
        (3, False),  # min + 1 == max - 1 (max=4)
        (4, False),  # max
        (5, True),  # max + 1
    ],
)
def test_preview_relay_team_size_boundary_flags_outside_2_to_max(
    tmp_path: Path, *, team_size: int, expect_conflict: bool
) -> None:
    """TeamN outside 2..max_team_size(4) is the only conflicting N."""
    filled = min(team_size, DEFAULT_MAX_TEAM_SIZE)
    rider_names = tuple(f"Rider{i}" for i in range(1, filled + 1))
    row = _relay_line(
        _RelayRow(
            plate="121",
            entry_name="Big Team",
            type_field=f"team{team_size}",
            rider_names=rider_names,
        ),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()

    result = preview(path, roster)

    assert len(result.conflicts) == (1 if expect_conflict else 0)


def test_preview_team_over_a_configured_max_of_two_uses_the_rides_own_max(
    tmp_path: Path,
) -> None:
    """The conflict message names the ride's own max, not a constant."""
    row = _relay_line(
        _RelayRow(
            plate="1", entry_name="Team A", type_field="team3", rider_names=("Alex", "Bo", "Cy")
        ),
        max_team_size=2,
    )
    path = _write_csv(tmp_path, [_relay_header(2), row])
    roster = _relay_roster(max_team_size=2)

    result = preview(path, roster)

    assert result.conflicts == (
        ImportConflict(row=2, problem="team size must be between 2 and 2, got 3"),
    )


# ======================================================== T-7 property


@given(row_count=st.integers(min_value=1, max_value=30))
@settings(max_examples=25, deadline=None)
def test_preview_relay_clean_solo_rows_rider_count_matches_generated_row_count(
    row_count: int,
) -> None:
    """rider_count always equals the clean solo rows given (T-7)."""
    header = _relay_header(DEFAULT_MAX_TEAM_SIZE)
    rows = [
        _relay_line(
            _RelayRow(plate=str(i), entry_name=f"Rider {i}", type_field="solo"),
            max_team_size=DEFAULT_MAX_TEAM_SIZE,
        )
        for i in range(1, row_count + 1)
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = _write_csv(Path(tmp_dir), [header, *rows])
        roster = _relay_roster()

        result = preview(path, roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (row_count, 0, ())


# =========================================================== E3.3.2
# commit(): atomic apply, matched-plate reshape, RUNNING rules.

# -------------------------------------------- commit: refuses + atomic


def test_commit_with_conflicts_present_raises_and_mutates_nothing(tmp_path: Path) -> None:
    """commit() refuses outright while any conflict remains (R-21)."""
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), "1,,solo,,,,,"])
    roster = _relay_roster()
    result = preview(path, roster)
    before = (roster.entries, roster.audit_log)

    with pytest.raises(ImportConflictsPresentError, match=re.escape("1 conflict")):
        commit(result)

    assert (roster.entries, roster.audit_log) == before


# ------------------------------------------------------- commit: insert


def test_commit_relay_inserts_every_new_plate_and_reports_counts(tmp_path: Path) -> None:
    """A conflict-free relay file inserts every row as a new entry."""
    row1 = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    row2 = _relay_line(
        _RelayRow(plate="2", entry_name="Team A", type_field="team2", rider_names=("Bo", "Cy")),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row1, row2])
    roster = _relay_roster()
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.moved_count) == (2, 0, 0)


def test_commit_relay_insert_creates_matching_roster_entries(tmp_path: Path) -> None:
    """The inserted relay entries carry the file's own plate/riders."""
    row = _relay_line(
        _RelayRow(plate="2", entry_name="Team A", type_field="team2", rider_names=("Bo", "Cy")),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()
    result = preview(path, roster)

    commit(result)

    assert [(e.plate, e.display_name, [r.name for r in e.riders]) for e in roster.entries] == [
        ("2", "Team A", ["Bo", "Cy"])
    ]


def test_commit_pooled_inserts_solo_and_team_and_reports_counts(tmp_path: Path) -> None:
    """A conflict-free pooled file inserts a solo entry and a team."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow(plate="1", name="Alex")),
        _pooled_line(_PooledRow(plate="2", name="Bo", team_name="Wolves")),
        _pooled_line(_PooledRow(plate="3", name="Cy", team_name="Wolves")),
    ]
    path = _write_csv(tmp_path, lines)
    roster = _pooled_roster()
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.moved_count) == (2, 0, 0)


def test_commit_pooled_insert_derives_the_new_teams_plate(tmp_path: Path) -> None:
    """A freshly inserted pooled team's plate is its lowest rider's."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow(plate="9", name="Bo", team_name="Wolves")),
        _pooled_line(_PooledRow(plate="2", name="Cy", team_name="Wolves")),
    ]
    path = _write_csv(tmp_path, lines)
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    team = roster.entries[0]
    assert (team.plate, [r.name for r in team.riders]) == ("2", ["Bo", "Cy"])


def test_commit_relay_insert_with_notes_sets_them_on_the_new_entry(tmp_path: Path) -> None:
    """An inserted relay entry's notes column lands on the new entry."""
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="solo", notes="late scratch"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    roster = _relay_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "late scratch"


def test_commit_pooled_insert_with_notes_sets_them_on_the_new_solo_entry(
    tmp_path: Path,
) -> None:
    """An inserted pooled solo entry's own row notes land on it too."""
    path = _write_csv(
        tmp_path, [_POOLED_HEADER_LINE, _pooled_line(_PooledRow("1", "Alex", "", "late scratch"))]
    )
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "late scratch"


def test_commit_pooled_forming_a_new_team_with_notes_joins_and_sets_them(
    tmp_path: Path,
) -> None:
    """A freshly formed team's per-member notes join onto the entry."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("2", "Bo", "Wolves", "flat tire")),
        _pooled_line(_PooledRow("3", "Cy", "Wolves", "spare batteries")),
    ]
    path = _write_csv(tmp_path, lines)
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "flat tire; spare batteries"


# -------------------------------------------- commit: name/notes update


def test_commit_relay_matched_plate_renames_the_existing_entry_in_place(
    tmp_path: Path,
) -> None:
    """A matched relay plate with only a name change updates it."""
    roster = _relay_roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Alexandra", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    result = preview(path, roster)

    report = commit(result)

    assert (entry.display_name, entry in roster.entries, report.updated_count) == (
        "Alexandra",
        True,
        1,
    )


def test_commit_pooled_matched_solo_plate_renames_in_place(tmp_path: Path) -> None:
    """A matched pooled solo plate with only a name change updates."""
    roster = _pooled_roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    path = _write_csv(tmp_path, [_POOLED_HEADER_LINE, _pooled_line(_PooledRow("1", "Alexandra"))])
    result = preview(path, roster)

    report = commit(result)

    assert (entry.display_name, report.updated_count) == ("Alexandra", 1)


def test_commit_pooled_matched_solo_plate_notes_only_change_updates_in_place(
    tmp_path: Path,
) -> None:
    """A matched pooled solo plate with only a notes change updates."""
    roster = _pooled_roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    path = _write_csv(
        tmp_path, [_POOLED_HEADER_LINE, _pooled_line(_PooledRow("1", "Alex", "", "flat tire"))]
    )
    result = preview(path, roster)

    report = commit(result)

    assert (entry.notes, report.updated_count) == ("flat tire", 1)


def test_commit_relay_matched_plate_with_no_changes_reports_zero_updates(
    tmp_path: Path,
) -> None:
    """A re-import that changes nothing updates nothing (no noise)."""
    roster = _relay_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Alex", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.audit_events) == (0, 0, ())


# ---------------------------------------------- commit: relay reshape


def test_commit_relay_matched_plate_composition_change_reshapes_in_draft(
    tmp_path: Path,
) -> None:
    """A matched relay plate with a changed roster is reshaped (S7)."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team X", riders=[Rider(name="Alex"), Rider(name="Bo")], plate="10"
    )
    row = _relay_line(
        _RelayRow(
            plate="10",
            entry_name="Team X",
            type_field="team3",
            rider_names=("Alex", "Bo", "Cy"),
        ),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    result = preview(path, roster)

    report = commit(result)

    assert ([r.name for r in roster.entries[0].riders], report.updated_count) == (
        ["Alex", "Bo", "Cy"],
        1,
    )


def test_commit_relay_matched_plate_type_change_from_solo_to_team_reshapes(
    tmp_path: Path,
) -> None:
    """A matched plate switching solo<->team is a composition change."""
    roster = _relay_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    row = _relay_line(
        _RelayRow(plate="1", entry_name="Team A", type_field="team2", rider_names=("Alex", "Bo")),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    result = preview(path, roster)

    report = commit(result)

    assert (roster.entries[0].type, report.updated_count) == (EntryType.TEAM, 1)


def test_preview_relay_matched_plate_composition_change_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """The same reshape is a conflict once the ride is RUNNING (S7)."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team X", riders=[Rider(name="Alex"), Rider(name="Bo")], plate="10"
    )
    roster.status = RideStatus.RUNNING
    row = _relay_line(
        _RelayRow(
            plate="10",
            entry_name="Team X",
            type_field="team3",
            rider_names=("Alex", "Bo", "Cy"),
        ),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])

    result = preview(path, roster)

    assert len(result.conflicts) == 1
    assert _STRUCTURAL_PROBLEM in result.conflicts[0].problem


def test_commit_relay_structural_change_while_running_refuses(tmp_path: Path) -> None:
    """commit() refuses the RUNNING structural conflict, unmutated."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team X", riders=[Rider(name="Alex"), Rider(name="Bo")], plate="10"
    )
    roster.status = RideStatus.RUNNING
    row = _relay_line(
        _RelayRow(
            plate="10",
            entry_name="Team X",
            type_field="team3",
            rider_names=("Alex", "Bo", "Cy"),
        ),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])
    result = preview(path, roster)
    before = [r.name for r in roster.entries[0].riders]

    with pytest.raises(ImportConflictsPresentError, match=re.escape("1 conflict")):
        commit(result)

    assert [r.name for r in roster.entries[0].riders] == before


def test_preview_relay_new_plate_insert_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """RUNNING still allows a brand-new plate ("add new plates")."""
    roster = _relay_roster()
    roster.status = RideStatus.RUNNING
    row = _relay_line(
        _RelayRow(plate="99", entry_name="Newcomer", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])

    result = preview(path, roster)

    assert result.conflicts == ()


# --------------------------------------------- commit: pooled team move


def _seed_wolves_and_falcons(roster: Roster) -> None:
    """Seed *roster* with Wolves{Bo,Cy,Zed} and Falcons{Do,El}."""
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(name="Bo", plate="2"),
            Rider(name="Cy", plate="3"),
            Rider(name="Zed", plate="4"),
        ],
    )
    roster.create_team_entry(
        display_name="Falcons",
        riders=[Rider(name="Do", plate="5"), Rider(name="El", plate="6")],
    )


def _bo_moves_to_falcons_csv(tmp_path: Path) -> Path:
    """Write the re-import file moving Bo(2) from Wolves to Falcons."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("1", "Alex")),
        _pooled_line(_PooledRow("2", "Bo", "Falcons")),
        _pooled_line(_PooledRow("3", "Cy", "Wolves")),
        _pooled_line(_PooledRow("4", "Zed", "Wolves")),
        _pooled_line(_PooledRow("5", "Do", "Falcons")),
        _pooled_line(_PooledRow("6", "El", "Falcons")),
    ]
    return _write_csv(tmp_path, lines)


def test_commit_pooled_moved_rider_updates_team_membership_in_draft(
    tmp_path: Path,
) -> None:
    """Re-import moving Bo to Falcons updates membership (R-17)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    result = preview(_bo_moves_to_falcons_csv(tmp_path), roster)

    report = commit(result)

    wolves = next(e for e in roster.entries if e.display_name == "Wolves")
    falcons = next(e for e in roster.entries if e.display_name == "Falcons")
    assert (
        [r.name for r in wolves.riders],
        [r.name for r in falcons.riders],
        report.moved_count,
    ) == (
        ["Cy", "Zed"],
        ["Do", "El", "Bo"],
        1,
    )


def test_commit_pooled_moved_rider_appends_one_move_rider_audit_event(
    tmp_path: Path,
) -> None:
    """The move is audited with the rider's name and both new plates."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    result = preview(_bo_moves_to_falcons_csv(tmp_path), roster)

    report = commit(result)

    assert [(e.action, e.payload["rider_name"]) for e in report.audit_events] == [
        ("move_rider", "Bo")
    ]


def test_commit_pooled_team_move_also_updates_the_targets_notes(tmp_path: Path) -> None:
    """A move that also carries a new team note updates the target."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("1", "Alex")),
        _pooled_line(_PooledRow("2", "Bo", "Falcons")),
        _pooled_line(_PooledRow("3", "Cy", "Wolves")),
        _pooled_line(_PooledRow("4", "Zed", "Wolves")),
        _pooled_line(_PooledRow("5", "Do", "Falcons", "flat tire")),
        _pooled_line(_PooledRow("6", "El", "Falcons")),
    ]
    result = preview(_write_csv(tmp_path, lines), roster)

    report = commit(result)

    falcons = next(e for e in roster.entries if e.display_name == "Falcons")
    assert (falcons.notes, report.moved_count, report.updated_count) == ("flat tire", 1, 1)


def test_preview_pooled_moved_rider_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """The same move previews clean once RUNNING (spec S7:171)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_bo_moves_to_falcons_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_commit_pooled_moved_rider_while_running_succeeds(tmp_path: Path) -> None:
    """commit() applies the RUNNING move exactly like a DRAFT one."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.RUNNING
    result = preview(_bo_moves_to_falcons_csv(tmp_path), roster)

    report = commit(result)

    assert report.moved_count == 1


def test_preview_pooled_moved_rider_while_finished_is_a_conflict(tmp_path: Path) -> None:
    """FINISHED closes the pooled move door too (mirrors R-17)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.FINISHED

    result = preview(_bo_moves_to_falcons_csv(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _MOVE_NOT_ALLOWED_PROBLEM in result.conflicts[0].problem


# ------------------------------------- pooled reshape via re-import


def _wolves_of_three(roster: Roster) -> None:
    """Seed *roster* with a single team, Wolves{Bo,Cy,Zed}."""
    roster.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(name="Bo", plate="2"),
            Rider(name="Cy", plate="3"),
            Rider(name="Zed", plate="4"),
        ],
    )


def _bo_goes_solo_csv(tmp_path: Path) -> Path:
    """Write the re-import file dropping Bo(2) out of Wolves to solo."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("2", "Bo")),
        _pooled_line(_PooledRow("3", "Cy", "Wolves")),
        _pooled_line(_PooledRow("4", "Zed", "Wolves")),
    ]
    return _write_csv(tmp_path, lines)


def test_preview_pooled_team_member_reclassified_solo_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a team member's row to drop to solo (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)

    result = preview(_bo_goes_solo_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_team_member_reclassified_solo_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING still refuses the same team->solo conversion (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_bo_goes_solo_csv(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _TEAM_TO_SOLO_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_team_member_reclassified_solo_extracts_in_draft(
    tmp_path: Path,
) -> None:
    """commit() extracts Bo onto his own solo entry (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    result = preview(_bo_goes_solo_csv(tmp_path), roster)

    report = commit(result)

    wolves = next(e for e in roster.entries if e.display_name == "Wolves")
    bo = next(e for e in roster.entries if e.display_name == "Bo")
    assert (
        [r.name for r in wolves.riders],
        (bo.plate, bo.type, [r.name for r in bo.riders]),
        report.extracted_count,
    ) == (["Cy", "Zed"], ("2", EntryType.SOLO, ["Bo"]), 1)


def test_commit_pooled_team_member_reclassified_solo_appends_extract_audit_event(
    tmp_path: Path,
) -> None:
    """The extraction is audited with the rider's name and new plate."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    result = preview(_bo_goes_solo_csv(tmp_path), roster)

    report = commit(result)

    assert [(e.action, e.payload["rider_name"]) for e in report.audit_events] == [
        ("extract_rider_to_solo", "Bo")
    ]


def test_commit_pooled_team_member_reclassified_solo_also_applies_a_rename(
    tmp_path: Path,
) -> None:
    """A row dropping to solo and renaming applies both changes."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("2", "Bobby")),
        _pooled_line(_PooledRow("3", "Cy", "Wolves")),
        _pooled_line(_PooledRow("4", "Zed", "Wolves")),
    ]
    result = preview(_write_csv(tmp_path, lines), roster)

    report = commit(result)

    bobby = next(e for e in roster.entries if e.plate == "2")
    assert (bobby.display_name, report.extracted_count, report.updated_count) == (
        "Bobby",
        1,
        1,
    )


def _falcons_of_two(roster: Roster) -> None:
    """Seed *roster* with a single team, Falcons{Do,El}."""
    roster.create_team_entry(
        display_name="Falcons",
        riders=[Rider(name="Do", plate="5"), Rider(name="El", plate="6")],
    )


def _fay_joins_falcons_csv(tmp_path: Path) -> Path:
    """Write a re-import file adding rider Fay(99) to Falcons."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("5", "Do", "Falcons")),
        _pooled_line(_PooledRow("6", "El", "Falcons")),
        _pooled_line(_PooledRow("99", "Fay", "Falcons")),
    ]
    return _write_csv(tmp_path, lines)


def test_preview_pooled_new_rider_joining_an_existing_team_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a brand-new plate to land straight on a team."""
    roster = _pooled_roster()
    _falcons_of_two(roster)

    result = preview(_fay_joins_falcons_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_new_rider_joining_an_existing_team_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """RUNNING keeps this open too (add_rider_to_team's carve-out)."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_fay_joins_falcons_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_new_rider_joining_an_existing_team_while_finished_conflicts(
    tmp_path: Path,
) -> None:
    """FINISHED closes this door too (can_move_rider is False here)."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    roster.status = RideStatus.FINISHED

    result = preview(_fay_joins_falcons_csv(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _MOVE_NOT_ALLOWED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_new_rider_joining_an_existing_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() adds Fay onto Falcons via add_rider_to_team."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    result = preview(_fay_joins_falcons_csv(tmp_path), roster)

    report = commit(result)

    falcons = roster.entries[0]
    assert ([r.name for r in falcons.riders], report.joined_count) == (
        ["Do", "El", "Fay"],
        1,
    )


def test_commit_pooled_new_rider_joining_an_existing_team_while_running(
    tmp_path: Path,
) -> None:
    """The same join applies while RUNNING too."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    roster.status = RideStatus.RUNNING
    result = preview(_fay_joins_falcons_csv(tmp_path), roster)

    report = commit(result)

    assert report.joined_count == 1


def _alex_joins_falcons_csv(tmp_path: Path) -> Path:
    """Write the re-import file moving solo Alex(1) onto Falcons."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("5", "Do", "Falcons")),
        _pooled_line(_PooledRow("6", "El", "Falcons")),
        _pooled_line(_PooledRow("1", "Alex", "Falcons")),
    ]
    return _write_csv(tmp_path, lines)


def test_preview_pooled_solo_rider_joining_an_existing_team_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a currently-solo rider to convert onto a team."""
    roster = _pooled_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    _falcons_of_two(roster)

    result = preview(_alex_joins_falcons_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_solo_rider_joining_an_existing_team_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING refuses the solo->team conversion (the carve-out).

    ``add_rider_to_team``'s RUNNING carve-out only covers a brand-new
    plate, not a currently-solo rider converting.
    """
    roster = _pooled_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    _falcons_of_two(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_alex_joins_falcons_csv(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _SOLO_TO_TEAM_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_solo_rider_joining_an_existing_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() dissolves Alex's solo entry, joins him onto Falcons."""
    roster = _pooled_roster()
    alex = roster.create_solo_entry(name="Alex", plate="1")
    _falcons_of_two(roster)
    result = preview(_alex_joins_falcons_csv(tmp_path), roster)

    report = commit(result)

    falcons = next(e for e in roster.entries if e.display_name == "Falcons")
    assert (alex in roster.entries, [r.name for r in falcons.riders], report.joined_count) == (
        False,
        ["Do", "El", "Alex"],
        1,
    )


def test_commit_pooled_solo_rider_joining_an_existing_team_audits_both_steps(
    tmp_path: Path,
) -> None:
    """Dissolving the solo entry and the join are both audited."""
    roster = _pooled_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    _falcons_of_two(roster)
    result = preview(_alex_joins_falcons_csv(tmp_path), roster)

    report = commit(result)

    assert [event.action for event in report.audit_events] == [
        "delete_entry",
        "add_rider_to_team",
    ]


def _newbies_forming_csv(tmp_path: Path) -> Path:
    """Write a fresh-team file pairing solo Alex(1) with Newby(50)."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("1", "Alex", "Newbies")),
        _pooled_line(_PooledRow("50", "Newby", "Newbies")),
    ]
    return _write_csv(tmp_path, lines)


def test_preview_pooled_promoting_a_solo_rider_into_a_fresh_team_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows folding a solo rider into a brand-new team."""
    roster = _pooled_roster()
    roster.create_solo_entry(name="Alex", plate="1")

    result = preview(_newbies_forming_csv(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_promoting_a_solo_rider_into_a_fresh_team_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING refuses it too.

    Forming a team needs no lock of its own, but the solo->team
    conversion folded into it is DRAFT-only regardless.
    """
    roster = _pooled_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    roster.status = RideStatus.RUNNING

    result = preview(_newbies_forming_csv(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _SOLO_TO_TEAM_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_promoting_a_solo_rider_into_a_fresh_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() dissolves Alex's solo entry into the Newbies team."""
    roster = _pooled_roster()
    alex = roster.create_solo_entry(name="Alex", plate="1")
    result = preview(_newbies_forming_csv(tmp_path), roster)

    report = commit(result)

    newbies = roster.entries[0]
    assert (
        alex in roster.entries,
        [r.name for r in newbies.riders],
        (report.inserted_count, report.joined_count, report.extracted_count),
    ) == (False, ["Alex", "Newby"], (1, 0, 0))


def test_preview_pooled_team_over_max_reports_team_over_max_conflict(
    tmp_path: Path,
) -> None:
    """A pooled group over max_team_size(4) is also a conflict."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("1", "A", "Big")),
        _pooled_line(_PooledRow("2", "B", "Big")),
        _pooled_line(_PooledRow("3", "C", "Big")),
        _pooled_line(_PooledRow("4", "D", "Big")),
        _pooled_line(_PooledRow("5", "E", "Big")),
    ]
    path = _write_csv(tmp_path, lines)
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.conflicts == (
        ImportConflict(row=2, problem="team of 5 riders exceeds the maximum of 4 (team-over-max)"),
    )


# ------------------------------------------------- preview: notes join


def test_preview_pooled_team_notes_join_non_empty_member_notes(tmp_path: Path) -> None:
    """A team's notes join every non-empty member note with '; '."""
    lines = [
        _POOLED_HEADER_LINE,
        _pooled_line(_PooledRow("1", "Bo", "Wolves", "flat tire")),
        _pooled_line(_PooledRow("2", "Cy", "Wolves", "")),
        _pooled_line(_PooledRow("3", "Zed", "Wolves", "spare batteries")),
    ]
    path = _write_csv(tmp_path, lines)
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries[0].notes == "flat tire; spare batteries"


# ======================================================== E3.3.3 export


def _read_lines(path: Path) -> list[str]:
    r"""Return *path*'s content as clean lines (no \r\n artifacts)."""
    return path.read_text(encoding="utf-8").splitlines()


def test_export_relay_empty_roster_writes_header_only_file(tmp_path: Path) -> None:
    """An empty relay roster exports to exactly one header line."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()

    export(roster, path)

    assert _read_lines(path) == [_relay_header(DEFAULT_MAX_TEAM_SIZE)]


def test_export_pooled_empty_roster_writes_header_only_file(tmp_path: Path) -> None:
    """An empty pooled roster exports to exactly one header line."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()

    export(roster, path)

    assert _read_lines(path) == [_POOLED_HEADER_LINE]


def test_export_relay_header_matches_the_rides_own_max_team_size(tmp_path: Path) -> None:
    """The exported relay header sizes rider_1..N to the ride's max."""
    path = tmp_path / "out.csv"
    roster = _relay_roster(max_team_size=2)

    export(roster, path)

    assert _read_lines(path) == ["plate,entry_name,type,rider_1,rider_2,notes"]


def test_export_relay_solo_entry_writes_one_row(tmp_path: Path) -> None:
    """A relay solo entry exports as one solo row, riders blank."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.create_solo_entry(name="Alex", plate="1")

    export(roster, path)

    assert _read_lines(path)[1] == "1,Alex,solo,,,,,"


def test_export_relay_team_entry_writes_type_and_rider_columns(tmp_path: Path) -> None:
    """A relay team entry exports its type and member names in order."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team A", riders=[Rider(name="Bo"), Rider(name="Cy")], plate="7"
    )

    export(roster, path)

    assert _read_lines(path)[1] == "7,Team A,team2,Bo,Cy,,,"


def test_export_relay_entry_notes_land_in_the_final_column(tmp_path: Path) -> None:
    """A relay entry's notes are the CSV row's final column."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    roster.update_entry(entry, notes="late scratch")

    export(roster, path)

    assert _read_lines(path)[1] == "1,Alex,solo,,,,,late scratch"


def test_export_pooled_solo_entry_writes_its_own_notes(tmp_path: Path) -> None:
    """A pooled solo entry's one row carries its own notes."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    entry = roster.create_solo_entry(name="Alex", plate="1")
    roster.update_entry(entry, notes="late scratch")

    export(roster, path)

    assert _read_lines(path)[1] == "1,Alex,,late scratch"


def test_export_pooled_team_writes_notes_on_first_row_only(tmp_path: Path) -> None:
    """A pooled team's notes land on its first member row only."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    entry = roster.create_team_entry(
        display_name="Wolves",
        riders=[Rider(name="Bo", plate="2"), Rider(name="Cy", plate="3")],
    )
    roster.update_entry(entry, notes="flat tire; spare batteries")

    export(roster, path)

    assert _read_lines(path)[1:3] == [
        "2,Bo,Wolves,flat tire; spare batteries",
        "3,Cy,Wolves,",
    ]


def test_export_then_preview_reimports_a_pooled_team_with_zero_conflicts(
    tmp_path: Path,
) -> None:
    """A worked example: export, then re-preview a fresh roster."""
    path = tmp_path / "out.csv"
    source = _pooled_roster()
    source.create_solo_entry(name="Alex", plate="1")
    source.create_team_entry(
        display_name="Wolves",
        riders=[Rider(name="Bo", plate="2"), Rider(name="Cy", plate="3")],
    )
    export(source, path)
    target = _pooled_roster()

    result = preview(path, target)

    assert (result.rider_count, result.team_count, result.conflicts) == (3, 1, ())


# ================================================ P3: standings columns


def _placed(  # noqa: PLR0913 -- mirrors standings.EntryResult's 10 fields
    plate: str,
    codes: str,
    *,
    laps: int = 0,
    total_time: float = 0.0,
    dnf: bool = False,
) -> Placed:
    """Build one Placed whose result's hand is best_hand of *codes*."""
    cards = tuple(Card.parse(code) for code in codes.split())
    result = EntryResult(
        entry_id=plate,
        plate=plate,
        name="Rider",
        kind="solo",
        laps=laps,
        total_time=total_time,
        best_lap=0.0,
        cards=cards,
        hand=best_hand(cards),
        dnf=dnf,
    )
    return Placed(place=1, result=result, tie_note=None, draw_required=False)


def test_export_placed_none_omitted_is_byte_identical(tmp_path: Path) -> None:
    """Omitting *placed* and passing None write the same bytes."""
    path_omitted = tmp_path / "omitted.csv"
    path_none = tmp_path / "none.csv"
    roster = _relay_roster()
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Team A", riders=[Rider(name="Bo"), Rider(name="Cy")], plate="7"
    )

    export(roster, path_omitted)
    export(roster, path_none, placed=None)

    assert path_omitted.read_bytes() == path_none.read_bytes()


def test_export_finished_relay_header_appends_standings_columns_after_existing_columns(
    tmp_path: Path,
) -> None:
    """Spec §7: finished rides add the four standings columns."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")

    export(roster, path, placed=[_placed("1", "AS AH")])

    assert _read_lines(path)[0] == (
        "plate,entry_name,type,rider_1,rider_2,rider_3,rider_4,notes,"
        "laps,cards,best_hand,total_time"
    )


def test_export_finished_relay_rows_carry_the_matching_placed_values(
    tmp_path: Path,
) -> None:
    """A relay row appends its entry's laps, cards, hand prose, time."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Team A", riders=[Rider(name="Bo"), Rider(name="Cy")], plate="7"
    )
    placed = [
        _placed("1", "AS AH", laps=5, total_time=20811.0),
        _placed("7", "9H 9D 9S", laps=4, total_time=19000.0),
    ]

    export(roster, path, placed=placed)

    assert _read_lines(path)[1:3] == [
        "1,Alex,solo,,,,,,5,2,Pair — Aces,20811.0",
        "7,Team A,team2,Bo,Cy,,,,4,3,Three of a Kind — Nines,19000.0",
    ]


def test_export_finished_pooled_rows_repeat_entry_stats_on_every_rider_row(
    tmp_path: Path,
) -> None:
    """Pooled rows are per-rider, so each carries the entry's stats."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_team_entry(
        display_name="Wolves",
        riders=[Rider(name="Bo", plate="2"), Rider(name="Cy", plate="3")],
    )
    placed = [
        _placed("1", "AS AH", laps=2, total_time=6000.0),
        _placed("2", "KS KD", laps=3, total_time=9000.0),
    ]

    export(roster, path, placed=placed)

    lines = _read_lines(path)
    assert (lines[0], lines[1:4]) == (
        "plate,name,team_name,notes,laps,cards,best_hand,total_time",
        [
            "1,Alex,,,2,2,Pair — Aces,6000.0",
            "2,Bo,Wolves,,3,2,Pair — Kings,9000.0",
            "3,Cy,Wolves,,3,2,Pair — Kings,9000.0",
        ],
    )


def test_export_finished_ride_writes_dnf_entry_stats_from_placed(tmp_path: Path) -> None:
    """R-33: a DNF placed entry keeps laps/cards; export writes them."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")

    export(roster, path, placed=[_placed("1", "AS AH", laps=3, total_time=12345.0, dnf=True)])

    assert _read_lines(path)[1] == "1,Alex,solo,,,,,,3,2,Pair — Aces,12345.0"


def test_export_finished_ride_ignores_extra_placed_rows_with_no_matching_entry(
    tmp_path: Path,
) -> None:
    """A caller may pass extra placed rows; they are ignored."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")
    placed = [
        _placed("1", "AS AH", laps=5, total_time=20811.0),
        _placed("99", "KS KD", laps=2, total_time=999.0),  # no such entry
    ]

    export(roster, path, placed=placed)

    assert _read_lines(path)[1] == "1,Alex,solo,,,,,,5,2,Pair — Aces,20811.0"


@pytest.mark.parametrize("status", [RideStatus.DRAFT, RideStatus.RUNNING])
def test_export_with_standings_before_finish_raises_csv_io_error(
    tmp_path: Path, *, status: RideStatus
) -> None:
    """Standings columns exist only once the ride is finished."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = status
    roster.create_solo_entry(name="Alex", plate="1")

    with pytest.raises(CsvIoError, match=re.escape("finished ride")):
        export(roster, path, placed=[_placed("1", "AS AH")])


def test_export_finished_ride_with_entry_missing_from_standings_raises_naming_plate(
    tmp_path: Path,
) -> None:
    """An entry absent from *placed* fails loudly, naming its plate."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")
    roster.create_solo_entry(name="Bo", plate="2")

    with pytest.raises(CsvIoError, match=re.escape("no standings for plate 1")):
        export(roster, path, placed=[_placed("2", "AS AH")])


def test_export_placed_none_on_finished_ride_writes_plain_header(tmp_path: Path) -> None:
    """Even FINISHED, omitting *placed* keeps the roster-only shape."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")

    export(roster, path)

    assert _read_lines(path)[0] == _relay_header(DEFAULT_MAX_TEAM_SIZE)


def test_export_finished_ride_with_empty_placed_raises_naming_plate(tmp_path: Path) -> None:
    """An empty *placed* list covers no entry; the plate is named."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")

    with pytest.raises(CsvIoError, match=re.escape("no standings for plate 1")):
        export(roster, path, placed=[])


_CARD_CODES = [f"{rank}{suit}" for rank in "23456789TJQKA" for suit in "CDHS"]


@given(
    codes=st.lists(st.sampled_from(_CARD_CODES), min_size=1, max_size=5),
    laps=st.integers(min_value=0, max_value=100),
    total_time=st.floats(
        min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=25, deadline=None)
def test_export_finished_relay_total_time_column_round_trips_as_float(
    *, codes: list[str], laps: int, total_time: float
) -> None:
    """The total_time cell is repr-clean: float(repr(t)) round-trips."""
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(name="Alex", plate="1")
    placed = [_placed("1", " ".join(codes), laps=laps, total_time=total_time)]

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "out.csv"
        export(roster, path, placed=placed)
        line = _read_lines(path)[1]

    assert float(line.split(",")[-1]) == total_time


# ------------------------------------ §15 standings CSV (E6.4.2)


def test_export_standings_writes_the_s15_header_without_times() -> None:
    """§15: place, plate, entry, laps, hand -- no total_time column."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "standings.csv"
        export_standings([], path)
        assert _read_lines(path)[0] == "place,plate,entry,laps,hand"


def test_export_standings_show_times_appends_total_time_column() -> None:
    """show_times adds the raw-seconds total_time column (R-63)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "standings.csv"
        export_standings([], path, show_times=True)
        assert _read_lines(path)[0] == "place,plate,entry,laps,hand,total_time"


def test_export_standings_rows_carry_the_placed_values() -> None:
    """One row per Placed: place, plate, entry, laps, hand prose."""
    placed = [
        _placed("88", "9S 9D 9C 9H 2C", laps=11, total_time=20_000.0),
        replace(_placed("7", "KH KC 5H 5D AS", laps=10, total_time=21_000.0), place=2),
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "standings.csv"
        export_standings(placed, path, show_times=True)
        lines = _read_lines(path)

    assert lines[1] == "1,88,Rider,11,Four of a Kind — Nines,20000.0"
    assert lines[2] == "2,7,Rider,10,Two Pair — Kings & Fives,21000.0"


def test_export_standings_keeps_dnf_rows() -> None:
    """DNF entries keep their row (R-33: laps/cards retained)."""
    placed = [_placed("3", "AS KS QS JS TS", laps=5, dnf=True)]
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "standings.csv"
        export_standings(placed, path)
        assert _read_lines(path)[1] == "1,3,Rider,5,Royal Flush"


def test_export_standings_zero_card_hand_writes_a_blank_hand() -> None:
    """A 0-card entry (never crossed) renders a blank hand."""
    placed = [_placed("9", "", laps=0)]
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "standings.csv"
        export_standings(placed, path)
        assert _read_lines(path)[1] == "1,9,Rider,0,"
