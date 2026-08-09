# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.csvio (E3.3.1 -- CSV import preview).

Spec S7's column spec and R-21 are this task's specification, narrowed
by task-briefs.md's own named cases: ``preview`` never writes anything
-- to the filesystem or to the target roster -- and reports exact
rider/team counts plus a per-row conflict list before any commit
happens (E3.3.2, not built yet, consumes that list).

Fixtures live in ``tests/unit/fixtures/csv/``: ``clean_180.csv`` is
the EPIC-shaped clean sample (team_relay, 120 solo + 15 team4 rows =
180 riders / 15 teams / 0 conflicts); ``clean_pooled.csv`` is the
rider_pooled equivalent (4 solos + two teams via team_name grouping);
``dup_plate.csv``, ``missing_name.csv``, ``team_over_max.csv`` and
``team_under_min_pooled.csv`` are minimal negatives, each producing
exactly one named conflict at a pinned row. Every other conflict shape
(unknown type, a malformed/mismatched header, an empty file, a blank
pooled name) is built inline against ``tmp_path`` -- small enough not
to need a committed fixture of its own.

Written FIRST, against a module that does not exist yet: this file is
red until rivercrossing/csvio.py lands.
"""

import re
import tempfile
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing.csvio import ImportConflict, ParsedEntry, ParsedRider, preview
from rivercrossing.roster import DEFAULT_MAX_TEAM_SIZE, EntryMode, EntryType, PlateModel, Roster

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "csv"

_HEADER_PROBLEM = "missing or malformed header for this ride's plate model"

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


def test_preview_relay_plate_colliding_with_an_existing_roster_entry_is_flagged(
    tmp_path: Path,
) -> None:
    """A CSV plate already on the target roster is a duplicate too."""
    roster = _relay_roster()
    roster.create_solo_entry(name="Existing Rider", plate="1")
    row = _relay_line(
        _RelayRow(plate="1", entry_name="New Rider", type_field="solo"),
        max_team_size=DEFAULT_MAX_TEAM_SIZE,
    )
    path = _write_csv(tmp_path, [_relay_header(DEFAULT_MAX_TEAM_SIZE), row])

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=2, problem="duplicate plate 1"),)


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
    """A nonexistent path propagates FileNotFoundError, unwrapped."""
    roster = _relay_roster()
    missing = tmp_path / "does-not-exist.csv"

    with pytest.raises(FileNotFoundError, match=re.escape(str(missing))):
        preview(missing, roster)


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
