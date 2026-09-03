# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for csvio's unified header-mapped roster CSV (R-21).

Phase 2 replaces spec S7's two plate-model CSV shapes (relay
``plate,entry_name,type,rider_1..rider_N,notes`` and pooled
``plate,name,team_name,notes``) with ONE header-mapped format: the
file's actual header row resolves to canonical fields via an ordered
matcher list (:func:`_map_header`), a column matching nothing is
ignored, and every data row is one RIDER. ``preview`` reports every
conflict found without writing anything -- to the filesystem or to the
target roster -- and ``commit`` applies a conflict-free preview through
the roster's own mutators, so every change is audit-logged and subject
to the ride's lock matrix (DRAFT reshapes freely; once started, relay
keeps its permanent composition lock while pooled keeps team-to-team
moves and a brand-new plate joining an existing team open through
RUNNING/REOPENED; only solo<->team *conversions* stay DRAFT-only).

The unified contract under test:

- Header mapping order is TEAMNAME, TYPE, FIRSTNAME, LASTNAME, NUMBER,
  NOTES, first match per column wins, all case-insensitive; canonical
  export tokens round-trip through the same map.
- Rows with neither first nor last name are skipped (trailing
  footer/empty rows); a row that still names a number/team/type is a
  missing-name conflict instead of a silent drop.
- TYPE is ``solo``/``team`` after case folding; blank TYPE derives from
  TEAMNAME presence; TEAMNAME groups rows by its normalized form
  (trim, collapse internal whitespace, lowercase) across the whole
  file, never by adjacency.
- NUMBER auto-assigns from :meth:`Roster.next_free_plate` when blank;
  under rider_pooled each rider owns their row's plate, under
  team_relay a team's member rows share the team's one plate.
- Export writes ``FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES``, one
  row per rider; a FINISHED ride's *placed* columns append after them.

Fixtures live in ``tests/unit/fixtures/csv/``: ``clean_180.csv`` is the
relay-shaped clean sample (120 solo + 15 team4 rows = 180 riders /
15 teams / 0 conflicts); ``clean_pooled.csv`` is the pooled equivalent
(4 solos + Falcons(3) + Hawks(2)); ``dup_plate.csv``,
``missing_name.csv``, ``team_over_max.csv`` and
``team_under_min_pooled.csv`` are minimal negatives, each producing
exactly one named conflict at a pinned row; ``gorba_epic.csv`` is a
verbatim copy of a real registration export (accepted as-is).

Written FIRST, against the two-shape module: this file is red until
csvio.py's unified rewrite lands.
"""

import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rivercrossing import csvio
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

_HEADER_PROBLEM = "missing or malformed header: no first or last name column"
_MISSING_NAME_PROBLEM = "missing name"
_STRUCTURAL_PROBLEM = "only new plates or name fixes are allowed"
_MOVE_NOT_ALLOWED_PROBLEM = "team change requires DRAFT, RUNNING or REOPENED"
_TEAM_TO_SOLO_LOCKED_PROBLEM = "converting a team member to a solo entry requires DRAFT"
_SOLO_TO_TEAM_LOCKED_PROBLEM = "converting a solo rider into a team member requires DRAFT"

_UNIFIED_HEADER = "firstname,lastname,type,teamname,number,notes"
_CANONICAL_HEADER = "FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES"

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


class _Row(NamedTuple):
    """One unified data row's six fields, before comma-joining."""

    first: str = ""
    last: str = ""
    type_: str = ""
    team: str = ""
    number: str = ""
    notes: str = ""


def _line(row: _Row) -> str:
    """Render *row* as one unified CSV data line."""
    return f"{row.first},{row.last},{row.type_},{row.team},{row.number},{row.notes}"


def _write_csv(tmp_path: Path, lines: list[str]) -> Path:
    """Write *lines* (header first) as ``tmp_path/riders.csv``."""
    path = tmp_path / "riders.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _unified_file(tmp_path: Path, rows: list[_Row]) -> Path:
    """Write *rows* under the unified test header to a temp CSV."""
    return _write_csv(tmp_path, [_UNIFIED_HEADER, *(_line(row) for row in rows)])


def _read_lines(path: Path) -> list[str]:
    r"""Return *path*'s content as clean lines (no \r\n artifacts)."""
    return path.read_text(encoding="utf-8").splitlines()


# ======================================================= _map_header


@pytest.mark.parametrize(
    ("header_row", "expected"),
    [
        # The GORBA reference file's own 12-column header.
        (
            (
                "Reg Checkout Date,First Name,Last Name,"
                "Are you riding Solo or on a Team?,What is your Team name?,"
                "Team Size,Names of other Team Members,"
                "Would you prefer a vegetarian meal?,T-shirt size?,"
                "Emergency Contact Name,Emergency Contact Number (xxx-xxx-xxxx),"
                "Medical conditions or allergies we need to be aware of in case of emergency"
            ),
            {"FIRSTNAME": 1, "LASTNAME": 2, "TYPE": 3, "TEAMNAME": 4},
        ),
        # The app's canonical export header round-trips field-for-field.
        (
            _CANONICAL_HEADER,
            {
                "FIRSTNAME": 0,
                "LASTNAME": 1,
                "TYPE": 2,
                "TEAMNAME": 3,
                "NUMBER": 4,
                "NOTES": 5,
            },
        ),
        # Case and whitespace are ignored everywhere.
        (
            "  First  Name , LASTNAME , TYPE ,What is your Team name?,number,Notes",
            {"TEAMNAME": 3, "TYPE": 2, "FIRSTNAME": 0, "LASTNAME": 1, "NUMBER": 4, "NOTES": 5},
        ),
    ],
)
def test_map_header_given_header_row_maps_the_expected_columns(
    *, header_row: str, expected: dict[str, int]
) -> None:
    """Map real and canonical headers to the expected field indexes."""
    assert csvio._map_header(header_row.split(",")) == expected


@pytest.mark.parametrize(
    "header",
    [
        # NUMBER is whole-header only (never "Emergency Contact…").
        "Emergency Contact Number (xxx-xxx-xxxx)",
        # TEAMNAME needs the literal word "name" after "team".
        "Team Size",
        # NOTES must be the word note/notes, not "Names of…".
        "Names of other Team Members",
        "Reg Checkout Date",
        "Emergency Contact Name",
        "Medical conditions or allergies we need to be aware of in case of emergency",
        "Would you prefer a vegetarian meal?",
        "T-shirt size?",
        # A fuller "Race Number" is not the whole-header NUMBER token.
        "Race Number",
    ],
)
def test_map_header_given_an_unmapped_column_ignores_it(header: str) -> None:
    """A column matching nothing contributes nothing to the mapping."""
    assert csvio._map_header([header]) == {}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # NUMBER accepts only whole-header number/plate/bib spellings.
        ("number", {"NUMBER": 0}),
        ("Plate", {"NUMBER": 0}),
        ("Bib", {"NUMBER": 0}),
        ("NUMBER", {"NUMBER": 0}),
        # TYPE fires only when the header contains both words (or is the
        # canonical token "type" itself, so an app export round-trips).
        ("Are you riding Solo or on a Team?", {"TYPE": 0}),
        ("Solo or Team?", {"TYPE": 0}),
        ("type", {"TYPE": 0}),
        ("TYPE", {"TYPE": 0}),
        # TEAMNAME needs "name" after team; TYPE text alone does not.
        ("What is your Team name?", {"TEAMNAME": 0}),
        ("Team Name", {"TEAMNAME": 0}),
        ("TEAMNAME", {"TEAMNAME": 0}),
        ("first name", {"FIRSTNAME": 0}),
        ("First  Name", {"FIRSTNAME": 0}),
        ("FIRSTNAME", {"FIRSTNAME": 0}),
        ("last name", {"LASTNAME": 0}),
        ("Last Name", {"LASTNAME": 0}),
        ("LASTNAME", {"LASTNAME": 0}),
        ("notes", {"NOTES": 0}),
        ("Notes", {"NOTES": 0}),
        ("Note", {"NOTES": 0}),
        ("NOTES", {"NOTES": 0}),
    ],
)
def test_map_header_given_each_canonical_spelling_maps_its_field(
    *, header: str, expected: dict[str, int]
) -> None:
    """Each field's accepted spellings resolve to that one field."""
    assert csvio._map_header([header]) == expected


def test_map_header_team_name_wins_over_type_when_one_column_holds_both() -> None:
    """TEAMNAME precedes TYPE in the matcher order (spec)."""
    header = "Are you riding Solo or on a Team? What is your Team name?"
    assert csvio._map_header([header]) == {"TEAMNAME": 0}


def test_map_header_first_column_wins_for_a_repeated_field() -> None:
    """A repeated First Name column keeps the leftmost one only."""
    assert csvio._map_header(["First Name", "First Name (parent)"]) == {"FIRSTNAME": 0}


# ======================================================= clean fixtures


def test_preview_clean_180_reports_exact_counts_and_no_conflicts() -> None:
    """The relay-shaped fixture: 180 riders, 15 teams, no conflicts."""
    roster = _relay_roster()

    result = preview(FIXTURES / "clean_180.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (180, 15, ())


def test_preview_clean_180_entries_count_matches_135_rows() -> None:
    """180 rider rows (120 solo + 15 team4) parse into 135 entries."""
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
        display_name="falcons",
        type=EntryType.TEAM,
        riders=(
            ParsedRider(first_name="Elin", last_name="Novak", plate="10"),
            ParsedRider(first_name="Faisal", last_name="Rahman", plate="11"),
            ParsedRider(first_name="Gita", last_name="Sundaram", plate="12"),
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
    """missing_name.csv's plated nameless row is the only conflict."""
    roster = _relay_roster()

    result = preview(FIXTURES / "missing_name.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        0,
        0,
        (ImportConflict(row=2, problem="missing name"),),
    )


def test_preview_team_over_max_fixture_reports_exactly_one_conflict_at_row_2() -> None:
    """team_over_max.csv's five-row group exceeds max_team_size(4)."""
    roster = _relay_roster()

    result = preview(FIXTURES / "team_over_max.csv", roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        5,
        1,
        (
            ImportConflict(
                row=2, problem="team of 5 riders exceeds the maximum of 4 (team-over-max)"
            ),
        ),
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


# ==================================================== header conflicts


def test_preview_old_relay_shaped_header_is_a_header_conflict(tmp_path: Path) -> None:
    """The retired two-shape relay header maps no first/last name."""
    path = _write_csv(
        tmp_path,
        ["plate,entry_name,type,rider_1,rider_2,rider_3,rider_4,notes", "1,Alex,solo,,,,,"],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.entries, result.conflicts) == (
        (),
        (ImportConflict(row=1, problem=_HEADER_PROBLEM),),
    )


def test_preview_old_pooled_shaped_header_is_a_header_conflict(tmp_path: Path) -> None:
    """The retired one-row-per-rider pooled header also cannot map."""
    path = _write_csv(tmp_path, ["plate,name,team_name,notes", "1,Alex,,"])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.entries, result.conflicts) == (
        (),
        (ImportConflict(row=1, problem=_HEADER_PROBLEM),),
    )


def test_preview_header_with_only_one_name_column_still_parses(tmp_path: Path) -> None:
    """A first-name-only header is usable; one-word riders are fine."""
    path = _write_csv(
        tmp_path,
        ["firstname,type,teamname,number", "Alex,solo,,1", "Bo,team,Wolves,2", "Cy,team,Wolves,3"],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.conflicts) == (3, ())


def test_preview_empty_file_reports_header_conflict(tmp_path: Path) -> None:
    """A zero-byte file has no header -- reported, not a crash."""
    path = tmp_path / "riders.csv"
    path.write_text("", encoding="utf-8")
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=1, problem=_HEADER_PROBLEM),)


# ==================================================== classification


def test_preview_relay_solo_row_parses_into_one_rider_named_like_the_entry(
    tmp_path: Path,
) -> None:
    """A solo row's rider carries the first/last name columns (S1)."""
    path = _unified_file(
        tmp_path,
        [_Row(first="Marc", last="Tremblay", type_="solo", number="9", notes="late scratch")],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="9",
            display_name="Marc Tremblay",
            type=EntryType.SOLO,
            riders=(ParsedRider(first_name="Marc", last_name="Tremblay"),),
            notes="late scratch",
        ),
    )


def test_preview_pooled_solo_row_carries_its_own_notes_onto_the_entry(tmp_path: Path) -> None:
    """A pooled solo row's notes map 1:1 onto its one-rider entry."""
    path = _unified_file(
        tmp_path,
        [_Row(first="Alex", last="Ferreira", type_="solo", number="5", notes="late scratch")],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="5",
            display_name="Alex Ferreira",
            type=EntryType.SOLO,
            riders=(ParsedRider(first_name="Alex", last_name="Ferreira", plate="5"),),
            notes="late scratch",
        ),
    )


def test_preview_relay_team_rows_group_into_one_entry_with_a_shared_plate(
    tmp_path: Path,
) -> None:
    """team_relay: a team's rows share one plate; riders are bare."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Team A", number="7"),
            _Row(first="Bo", type_="team", team="Team A", number="7"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="7",
            display_name="team a",
            type=EntryType.TEAM,
            riders=(
                ParsedRider(first_name="Alex", last_name=""),
                ParsedRider(first_name="Bo", last_name=""),
            ),
        ),
    )


def test_preview_pooled_team_rows_keep_each_riders_own_plate(tmp_path: Path) -> None:
    """rider_pooled: each team member row keeps its own plate (S1)."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="9"),
            _Row(first="Cy", type_="team", team="Wolves", number="2"),
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="2",
            display_name="wolves",
            type=EntryType.TEAM,
            riders=(
                ParsedRider(first_name="Bo", last_name="", plate="9"),
                ParsedRider(first_name="Cy", last_name="", plate="2"),
            ),
        ),
    )


def test_preview_blank_type_with_team_name_derives_team(tmp_path: Path) -> None:
    """Blank TYPE: a row with a team name is a team row (spec)."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", team="Wolves", number="2"),
            _Row(first="Cy", team="Wolves", number="3"),
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.entries[0].type, result.team_count, result.conflicts) == (
        EntryType.TEAM,
        1,
        (),
    )


def test_preview_blank_type_without_team_name_derives_solo(tmp_path: Path) -> None:
    """Blank TYPE and no team name: the row is a solo rider."""
    path = _unified_file(tmp_path, [_Row(first="Alex", last="Ferreira", number="1")])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.entries[0].type, result.team_count) == (EntryType.SOLO, 0)


def test_preview_explicit_solo_type_ignores_a_stray_team_name(tmp_path: Path) -> None:
    """TYPE=solo wins: a leftover team name does not regroup."""
    path = _unified_file(tmp_path, [_Row(first="Alex", type_="solo", team="N/A", number="1")])
    roster = _pooled_roster()

    result = preview(path, roster)

    assert (result.entries[0].type, result.entries[0].display_name) == (EntryType.SOLO, "Alex")


def test_preview_unknown_type_value_is_a_conflict_excluded_from_counts(tmp_path: Path) -> None:
    """An unrecognized type token conflicts and contributes no entry."""
    path = _unified_file(tmp_path, [_Row(first="Alex", type_="triple", number="1")])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (
        0,
        0,
        (ImportConflict(row=2, problem="unknown entry type 'triple'"),),
    )


def test_preview_explicit_team_without_a_team_name_reports_missing_name(
    tmp_path: Path,
) -> None:
    """TYPE=team but no team name is a nameless, ungroupable row."""
    path = _unified_file(tmp_path, [_Row(first="Alex", type_="team", number="1")])
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=2, problem="missing name"),)


# ==================================================== footer-row skips


@pytest.mark.parametrize(
    "footer_line",
    [
        # A fully empty row (the trailing blank separator line).
        "",
        # Text living only in unmapped columns (Reg Checkout Date col).
        "Basic info for 2026 GORBA EPIC & MTB Festival generated at 2026/09/02 06:17:01 PDT",
        # A quoted footer with an embedded comma, in an unmapped column.
        '"event: 2026 GORBA EPIC & MTB Festival, "',
    ],
)
def test_preview_row_with_no_name_and_no_mapped_content_is_skipped(
    tmp_path: Path, *, footer_line: str
) -> None:
    """Trailing footer/empty rows drop out without conflict or count.

    The file's first column (Reg Checkout Date) is unmapped, so footer
    text there never reads as a rider name -- the real GORBA shape.
    """
    header = "Reg Checkout Date," + _UNIFIED_HEADER
    data_row = "," + _line(_Row(first="Alex", number="1"))  # empty Reg Checkout Date cell
    path = _write_csv(tmp_path, [header, data_row, footer_line])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.conflicts) == (1, ())


def test_preview_plated_row_with_no_name_reports_missing_name_not_skip(tmp_path: Path) -> None:
    """A row with a plate but no rider name is a loud data error."""
    path = _unified_file(tmp_path, [_Row(number="1")])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.conflicts) == (
        0,
        (ImportConflict(row=2, problem="missing name"),),
    )


def test_preview_row_with_only_a_last_name_is_kept(tmp_path: Path) -> None:
    """One usable name component is enough (one-word riders exist)."""
    path = _unified_file(tmp_path, [_Row(last="Cher", type_="solo", number="1")])
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.rider_count, result.conflicts) == (1, ())


# =================================================== team grouping


def test_preview_groups_team_rows_by_normalized_name_across_the_file(tmp_path: Path) -> None:
    """Full Send / full   send / FULL SEND rows collapse into one team.

    Grouping keys are the normalized team name -- trim, collapse
    internal whitespace, lowercase -- matched across the whole file,
    never by adjacency.
    """
    path = _unified_file(
        tmp_path,
        [
            _Row(first="A", type_="solo", number="1"),  # interleaved solo
            _Row(first="Jonathan", type_="team", team="Full Send", number="2"),
            _Row(first="Willem", type_="team", team=" full   send ", number="2"),
            _Row(first="Bev", type_="team", team="FULL SEND", number="2"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert (result.team_count, result.rider_count) == (1, 4)
    full_send = next(
        e for e in result.entries if e.type is EntryType.TEAM and e.display_name == "full send"
    )
    solo = result.entries[0]
    assert ([r.full_name for r in full_send.riders], solo.type) == (
        ["Jonathan", "Willem", "Bev"],
        EntryType.SOLO,
    )


def test_preview_whitespace_collapse_keeps_distinct_words_separate(tmp_path: Path) -> None:
    """BNBA1 and BNBA 1 stay two teams: collapse keeps one space."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Lars", type_="team", team="BNBA1", number="2"),
            _Row(first="Matt", type_="team", team="BNBA1", number="2"),
            _Row(first="Pedro", type_="team", team="BNBA 1", number="3"),
            _Row(first="Pamela", type_="team", team="BNBA 1", number="3"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert sorted(e.display_name for e in result.entries) == ["bnba 1", "bnba1"]


@pytest.mark.parametrize(
    ("member_count", "expect_conflict"),
    [
        (1, True),  # min - 1
        (2, False),  # min
        (3, False),  # min + 1 == max - 1 (max=4)
        (4, False),  # max
        (5, True),  # max + 1
    ],
)
def test_preview_team_size_boundary_flags_outside_2_to_max(
    tmp_path: Path, *, member_count: int, expect_conflict: bool
) -> None:
    """Team groups outside 2..max_team_size(4) are the only ones."""
    rows = [
        _Row(first=f"Rider{i}", type_="team", team="Big Team") for i in range(1, member_count + 1)
    ]
    path = _unified_file(tmp_path, rows)
    roster = _relay_roster()

    result = preview(path, roster)

    assert len(result.conflicts) == (1 if expect_conflict else 0)


# ==================================================== duplicate plates


def test_preview_relay_plate_colliding_with_an_existing_roster_entry_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """A CSV plate matching an existing entry is the update path."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Existing", last_name="Rider", plate="1")
    path = _unified_file(
        tmp_path, [_Row(first="Existing", last="Rider", type_="solo", number="1")]
    )

    result = preview(path, roster)

    assert result.conflicts == ()


def test_preview_two_independent_conflicts_both_appear_in_row_order(tmp_path: Path) -> None:
    """Independent conflicts across a file all appear, in row order."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="triple", number="1"),
            _Row(number="2"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (
        ImportConflict(row=2, problem="unknown entry type 'triple'"),
        ImportConflict(row=3, problem="missing name"),
    )


def test_preview_relay_solo_and_team_sharing_one_plate_conflicts(tmp_path: Path) -> None:
    """team_relay: a solo row and a team cannot share the same plate."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="7"),
            _Row(first="Bo", type_="team", team="Wolves", number="7"),
            _Row(first="Cy", type_="team", team="Wolves", number="7"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=3, problem="duplicate plate 7"),)


def test_preview_relay_team_rows_carrying_different_plates_conflict(tmp_path: Path) -> None:
    """team_relay: one team must not straddle two entry plates."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert len(result.conflicts) == 1
    assert "carry different plates" in result.conflicts[0].problem


# ================================================= NUMBER auto-assign


def test_preview_relay_solo_and_team_without_numbers_auto_assign_sequential_plates(
    tmp_path: Path,
) -> None:
    """Blank NUMBER cells auto-assign from next_free_plate()."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo"),  # -> plate 1
            _Row(first="Bo", type_="team", team="Wolves"),  # -> plate 2 (group)
            _Row(first="Cy", type_="team", team="Wolves"),
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert [entry.plate for entry in result.entries] == ["1", "2"]


def test_preview_relay_auto_assigned_plates_avoid_existing_roster_plates(tmp_path: Path) -> None:
    """Auto-assignment starts past the roster's highest plate (R-20)."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Seeded", last_name="Rider", plate="3")
    path = _unified_file(tmp_path, [_Row(first="Alex", type_="solo")])

    result = preview(path, roster)

    assert result.entries[0].plate == "4"


def test_preview_relay_auto_assigned_plate_avoids_an_explicit_file_plate(tmp_path: Path) -> None:
    """Auto-assignment never collides with a plate given elsewhere."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="1"),
            _Row(first="Bo", type_="solo"),  # would be "1" -- skip to 2
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert [entry.plate for entry in result.entries] == ["1", "2"]


def test_preview_pooled_blank_numbers_auto_assign_one_plate_per_rider(tmp_path: Path) -> None:
    """rider_pooled: blank NUMBER means a fresh plate for that rider."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves"),
            _Row(first="Cy", type_="team", team="Wolves"),
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries == (
        ParsedEntry(
            plate="1",
            display_name="wolves",
            type=EntryType.TEAM,
            riders=(
                ParsedRider(first_name="Bo", last_name="", plate="1"),
                ParsedRider(first_name="Cy", last_name="", plate="2"),
            ),
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


# =========================================================== E3.3.2
# commit(): atomic apply, matched-plate reshape, RUNNING rules.

# -------------------------------------------- commit: refuses + atomic


def test_commit_with_conflicts_present_raises_and_mutates_nothing(tmp_path: Path) -> None:
    """commit() refuses outright while any conflict remains (R-21)."""
    path = _unified_file(tmp_path, [_Row(number="1")])
    roster = _relay_roster()
    result = preview(path, roster)
    before = (roster.entries, roster.audit_log)

    with pytest.raises(ImportConflictsPresentError, match=re.escape("1 conflict")):
        commit(result)

    assert (roster.entries, roster.audit_log) == before


# ------------------------------------------------------- commit: insert


def test_commit_relay_inserts_every_new_plate_and_reports_counts(tmp_path: Path) -> None:
    """A conflict-free relay file inserts every parsed entry."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", last="Tremblay", type_="solo", number="1"),
            _Row(first="Bo", type_="team", team="Wolves", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="2"),
        ],
    )
    roster = _relay_roster()
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.moved_count) == (2, 0, 0)


def test_commit_relay_insert_creates_matching_roster_entries(tmp_path: Path) -> None:
    """The inserted relay entries carry the file's plates and riders."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Team A", number="2"),
            _Row(first="Cy", type_="team", team="Team A", number="2"),
        ],
    )
    roster = _relay_roster()
    result = preview(path, roster)

    commit(result)

    assert [
        (e.plate, e.display_name, [r.full_name for r in e.riders]) for e in roster.entries
    ] == [("2", "team a", ["Bo", "Cy"])]


def test_commit_relay_insert_keeps_team_riders_plateless(tmp_path: Path) -> None:
    """team_relay riders never carry a plate of their own (S1)."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Team A", number="2"),
            _Row(first="Cy", type_="team", team="Team A", number="2"),
        ],
    )
    roster = _relay_roster()
    result = preview(path, roster)

    commit(result)

    assert [rider.plate for rider in roster.entries[0].riders] == [None, None]


def test_commit_pooled_inserts_solo_and_team_and_reports_counts(tmp_path: Path) -> None:
    """A conflict-free pooled file inserts a solo entry and a team."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="1"),
            _Row(first="Bo", type_="team", team="Wolves", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
        ],
    )
    roster = _pooled_roster()
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.moved_count) == (2, 0, 0)


def test_commit_pooled_insert_derives_the_new_teams_plate(tmp_path: Path) -> None:
    """A freshly inserted pooled team's plate is its lowest rider's."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="9"),
            _Row(first="Cy", type_="team", team="Wolves", number="2"),
        ],
    )
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    team = roster.entries[0]
    assert (team.plate, [r.full_name for r in team.riders]) == ("2", ["Bo", "Cy"])


def test_commit_relay_insert_with_notes_sets_them_on_the_new_entry(tmp_path: Path) -> None:
    """An inserted relay solo's notes column lands on the new entry."""
    path = _unified_file(
        tmp_path, [_Row(first="Alex", type_="solo", number="1", notes="late scratch")]
    )
    roster = _relay_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "late scratch"


def test_commit_pooled_insert_with_notes_sets_them_on_the_new_solo_entry(
    tmp_path: Path,
) -> None:
    """An inserted pooled solo entry's own row notes land on it too."""
    path = _unified_file(
        tmp_path, [_Row(first="Alex", type_="solo", number="1", notes="late scratch")]
    )
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "late scratch"


def test_commit_pooled_forming_a_new_team_with_notes_joins_and_sets_them(
    tmp_path: Path,
) -> None:
    """A freshly formed team's per-member notes join onto the entry."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="2", notes="flat tire"),
            _Row(first="Cy", type_="team", team="Wolves", number="3", notes="spare batteries"),
        ],
    )
    roster = _pooled_roster()
    result = preview(path, roster)

    commit(result)

    assert roster.entries[0].notes == "flat tire; spare batteries"


# -------------------------------------------- commit: name/notes update


def test_commit_relay_matched_plate_renames_the_existing_entry_in_place(
    tmp_path: Path,
) -> None:
    """A matched relay solo plate with only a name change updates it."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    path = _unified_file(tmp_path, [_Row(first="Alexandra", type_="solo", number="1")])
    result = preview(path, roster)

    report = commit(result)

    entry = roster.entries[0]
    assert (entry.display_name, entry.riders[0].first_name, report.updated_count) == (
        "Alexandra",
        "Alexandra",
        1,
    )


def test_commit_pooled_matched_solo_plate_renames_in_place(tmp_path: Path) -> None:
    """A matched pooled solo plate with only a name change updates."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    path = _unified_file(tmp_path, [_Row(first="Alexandra", type_="solo", number="1")])
    result = preview(path, roster)

    report = commit(result)

    entry = roster.entries[0]
    assert (entry.display_name, entry.riders[0].first_name, report.updated_count) == (
        "Alexandra",
        "Alexandra",
        1,
    )


def test_commit_pooled_matched_solo_plate_notes_only_change_updates_in_place(
    tmp_path: Path,
) -> None:
    """A matched pooled solo plate with only a notes change updates."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    path = _unified_file(
        tmp_path, [_Row(first="Alex", type_="solo", number="1", notes="flat tire")]
    )
    result = preview(path, roster)

    report = commit(result)

    assert (roster.entries[0].notes, report.updated_count) == ("flat tire", 1)


def test_commit_relay_matched_plate_with_no_changes_reports_zero_updates(
    tmp_path: Path,
) -> None:
    """A re-import that changes nothing updates nothing (no noise)."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    path = _unified_file(tmp_path, [_Row(first="Alex", type_="solo", number="1")])
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
        display_name="Team X",
        riders=[Rider(first_name="Alex", last_name=""), Rider(first_name="Bo", last_name="")],
        plate="10",
    )
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Team X", number="10"),
            _Row(first="Bo", type_="team", team="Team X", number="10"),
            _Row(first="Cy", type_="team", team="Team X", number="10"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    assert ([r.full_name for r in roster.entries[0].riders], report.updated_count) == (
        ["Alex", "Bo", "Cy"],
        1,
    )


def test_commit_relay_matched_plate_type_change_from_solo_to_team_reshapes(
    tmp_path: Path,
) -> None:
    """A matched plate switching solo<->team is a composition change."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Team A", number="1"),
            _Row(first="Bo", type_="team", team="Team A", number="1"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    assert (roster.entries[0].type, report.updated_count) == (EntryType.TEAM, 1)


def test_preview_relay_matched_plate_composition_change_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """The same reshape is a conflict once the ride is RUNNING (S7)."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team X",
        riders=[Rider(first_name="Alex", last_name=""), Rider(first_name="Bo", last_name="")],
        plate="10",
    )
    roster.status = RideStatus.RUNNING
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Team X", number="10"),
            _Row(first="Bo", type_="team", team="Team X", number="10"),
            _Row(first="Cy", type_="team", team="Team X", number="10"),
        ],
    )

    result = preview(path, roster)

    assert len(result.conflicts) == 1
    assert _STRUCTURAL_PROBLEM in result.conflicts[0].problem


def test_commit_relay_structural_change_while_running_refuses(tmp_path: Path) -> None:
    """commit() refuses the RUNNING structural conflict, unmutated."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team X",
        riders=[Rider(first_name="Alex", last_name=""), Rider(first_name="Bo", last_name="")],
        plate="10",
    )
    roster.status = RideStatus.RUNNING
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Team X", number="10"),
            _Row(first="Bo", type_="team", team="Team X", number="10"),
            _Row(first="Cy", type_="team", team="Team X", number="10"),
        ],
    )
    result = preview(path, roster)
    before = [r.full_name for r in roster.entries[0].riders]

    with pytest.raises(ImportConflictsPresentError, match=re.escape("1 conflict")):
        commit(result)

    assert [r.full_name for r in roster.entries[0].riders] == before


def test_preview_relay_new_plate_insert_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """RUNNING still allows a brand-new plate ("add new plates")."""
    roster = _relay_roster()
    roster.status = RideStatus.RUNNING
    path = _unified_file(tmp_path, [_Row(first="Newcomer", type_="solo", number="99")])

    result = preview(path, roster)

    assert result.conflicts == ()


# --------------------------------------------- commit: pooled team move


def _seed_wolves_and_falcons(roster: Roster) -> None:
    """Seed *roster* with Wolves{Bo,Cy,Zed} and Falcons{Do,El}."""
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.create_team_entry(
        display_name="wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
            Rider(first_name="Zed", last_name="", plate="4"),
        ],
    )
    roster.create_team_entry(
        display_name="falcons",
        riders=[
            Rider(first_name="Do", last_name="", plate="5"),
            Rider(first_name="El", last_name="", plate="6"),
        ],
    )


def _bo_moves_to_falcons_file(tmp_path: Path) -> Path:
    """Write the re-import file moving Bo(2) from Wolves to Falcons."""
    return _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="1"),
            _Row(first="Bo", type_="team", team="Falcons", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
            _Row(first="Zed", type_="team", team="Wolves", number="4"),
            _Row(first="Do", type_="team", team="Falcons", number="5"),
            _Row(first="El", type_="team", team="Falcons", number="6"),
        ],
    )


def test_commit_pooled_moved_rider_updates_team_membership_in_draft(
    tmp_path: Path,
) -> None:
    """Re-import moving Bo to Falcons updates membership (R-17)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    result = preview(_bo_moves_to_falcons_file(tmp_path), roster)

    report = commit(result)

    wolves = next(e for e in roster.entries if e.display_name == "wolves")
    falcons = next(e for e in roster.entries if e.display_name == "falcons")
    assert (
        [r.full_name for r in wolves.riders],
        [r.full_name for r in falcons.riders],
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
    result = preview(_bo_moves_to_falcons_file(tmp_path), roster)

    report = commit(result)

    assert [(e.action, e.payload["rider_name"]) for e in report.audit_events] == [
        ("move_rider", "Bo")
    ]


def test_commit_pooled_team_move_also_updates_the_targets_notes(tmp_path: Path) -> None:
    """A move that also carries a new team note updates the target."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="1"),
            _Row(first="Bo", type_="team", team="Falcons", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
            _Row(first="Zed", type_="team", team="Wolves", number="4"),
            _Row(first="Do", type_="team", team="Falcons", number="5", notes="flat tire"),
            _Row(first="El", type_="team", team="Falcons", number="6"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    falcons = next(e for e in roster.entries if e.display_name == "falcons")
    assert (falcons.notes, report.moved_count, report.updated_count) == ("flat tire", 1, 1)


def test_preview_pooled_moved_rider_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """The same move previews clean once RUNNING (spec S7:171)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_bo_moves_to_falcons_file(tmp_path), roster)

    assert result.conflicts == ()


def test_commit_pooled_moved_rider_while_running_succeeds(tmp_path: Path) -> None:
    """commit() applies the RUNNING move exactly like a DRAFT one."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.RUNNING
    result = preview(_bo_moves_to_falcons_file(tmp_path), roster)

    report = commit(result)

    assert report.moved_count == 1


def test_preview_pooled_moved_rider_while_finished_is_a_conflict(tmp_path: Path) -> None:
    """FINISHED closes the pooled move door too (mirrors R-17)."""
    roster = _pooled_roster()
    _seed_wolves_and_falcons(roster)
    roster.status = RideStatus.FINISHED

    result = preview(_bo_moves_to_falcons_file(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _MOVE_NOT_ALLOWED_PROBLEM in result.conflicts[0].problem


# ------------------------------------- pooled reshape via re-import


def _wolves_of_three(roster: Roster) -> None:
    """Seed *roster* with a single team, Wolves{Bo,Cy,Zed}."""
    roster.create_team_entry(
        display_name="wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
            Rider(first_name="Zed", last_name="", plate="4"),
        ],
    )


def _bo_goes_solo_file(tmp_path: Path) -> Path:
    """Write the re-import file dropping Bo(2) out of Wolves to solo."""
    return _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="solo", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
            _Row(first="Zed", type_="team", team="Wolves", number="4"),
        ],
    )


def test_preview_pooled_team_member_reclassified_solo_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a team member's row to drop to solo (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)

    result = preview(_bo_goes_solo_file(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_team_member_reclassified_solo_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING still refuses the same team->solo conversion (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_bo_goes_solo_file(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _TEAM_TO_SOLO_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_team_member_reclassified_solo_extracts_in_draft(
    tmp_path: Path,
) -> None:
    """commit() extracts Bo onto his own solo entry (S1)."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    result = preview(_bo_goes_solo_file(tmp_path), roster)

    report = commit(result)

    wolves = next(e for e in roster.entries if e.display_name == "wolves")
    bo = next(e for e in roster.entries if e.display_name == "Bo")
    assert (
        [r.full_name for r in wolves.riders],
        (bo.plate, bo.type, [r.full_name for r in bo.riders]),
        report.extracted_count,
    ) == (["Cy", "Zed"], ("2", EntryType.SOLO, ["Bo"]), 1)


def test_commit_pooled_team_member_reclassified_solo_appends_extract_audit_event(
    tmp_path: Path,
) -> None:
    """The extraction is audited with the rider's name and new plate."""
    roster = _pooled_roster()
    _wolves_of_three(roster)
    result = preview(_bo_goes_solo_file(tmp_path), roster)

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
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bobby", type_="solo", number="2"),
            _Row(first="Cy", type_="team", team="Wolves", number="3"),
            _Row(first="Zed", type_="team", team="Wolves", number="4"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    bobby = next(e for e in roster.entries if e.plate == "2")
    assert (
        bobby.display_name,
        bobby.riders[0].first_name,
        report.extracted_count,
        report.updated_count,
    ) == (
        "Bobby",
        "Bobby",
        1,
        1,
    )


def _falcons_of_two(roster: Roster) -> None:
    """Seed *roster* with a single team, Falcons{Do,El}."""
    roster.create_team_entry(
        display_name="falcons",
        riders=[
            Rider(first_name="Do", last_name="", plate="5"),
            Rider(first_name="El", last_name="", plate="6"),
        ],
    )


def _fay_joins_falcons_file(tmp_path: Path) -> Path:
    """Write a re-import file adding rider Fay(99) to Falcons."""
    return _unified_file(
        tmp_path,
        [
            _Row(first="Do", type_="team", team="Falcons", number="5"),
            _Row(first="El", type_="team", team="Falcons", number="6"),
            _Row(first="Fay", type_="team", team="Falcons", number="99"),
        ],
    )


def test_preview_pooled_new_rider_joining_an_existing_team_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a brand-new plate to land straight on a team."""
    roster = _pooled_roster()
    _falcons_of_two(roster)

    result = preview(_fay_joins_falcons_file(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_new_rider_joining_an_existing_team_while_running_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """RUNNING keeps this open too (add_rider_to_team's carve-out)."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_fay_joins_falcons_file(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_new_rider_joining_an_existing_team_while_finished_conflicts(
    tmp_path: Path,
) -> None:
    """FINISHED closes this door too (can_move_rider is False here)."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    roster.status = RideStatus.FINISHED

    result = preview(_fay_joins_falcons_file(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _MOVE_NOT_ALLOWED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_new_rider_joining_an_existing_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() adds Fay onto Falcons via add_rider_to_team."""
    roster = _pooled_roster()
    _falcons_of_two(roster)
    result = preview(_fay_joins_falcons_file(tmp_path), roster)

    report = commit(result)

    falcons = roster.entries[0]
    assert ([r.full_name for r in falcons.riders], report.joined_count) == (
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
    result = preview(_fay_joins_falcons_file(tmp_path), roster)

    report = commit(result)

    assert report.joined_count == 1


def _alex_joins_falcons_file(tmp_path: Path) -> Path:
    """Write the re-import file moving solo Alex(1) onto Falcons."""
    return _unified_file(
        tmp_path,
        [
            _Row(first="Do", type_="team", team="Falcons", number="5"),
            _Row(first="El", type_="team", team="Falcons", number="6"),
            _Row(first="Alex", type_="team", team="Falcons", number="1"),
        ],
    )


def test_preview_pooled_solo_rider_joining_an_existing_team_is_not_a_conflict_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows a currently-solo rider to convert onto a team."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    _falcons_of_two(roster)

    result = preview(_alex_joins_falcons_file(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_solo_rider_joining_an_existing_team_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING refuses the solo->team conversion (the carve-out)."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    _falcons_of_two(roster)
    roster.status = RideStatus.RUNNING

    result = preview(_alex_joins_falcons_file(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _SOLO_TO_TEAM_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_solo_rider_joining_an_existing_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() dissolves Alex's solo entry, joins him onto Falcons."""
    roster = _pooled_roster()
    alex = roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    _falcons_of_two(roster)
    result = preview(_alex_joins_falcons_file(tmp_path), roster)

    report = commit(result)

    falcons = next(e for e in roster.entries if e.display_name == "falcons")
    assert (
        alex in roster.entries,
        [r.full_name for r in falcons.riders],
        report.joined_count,
    ) == (
        False,
        ["Do", "El", "Alex"],
        1,
    )


def test_commit_pooled_solo_rider_joining_an_existing_team_audits_both_steps(
    tmp_path: Path,
) -> None:
    """Dissolving the solo entry and the join are both audited."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    _falcons_of_two(roster)
    result = preview(_alex_joins_falcons_file(tmp_path), roster)

    report = commit(result)

    assert [event.action for event in report.audit_events] == [
        "delete_entry",
        "add_rider_to_team",
    ]


def _newbies_forming_file(tmp_path: Path) -> Path:
    """Write a fresh-team file pairing solo Alex(1) with Newby(50)."""
    return _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="team", team="Newbies", number="1"),
            _Row(first="Newby", type_="team", team="Newbies", number="50"),
        ],
    )


def test_preview_pooled_promoting_a_solo_rider_into_a_fresh_team_in_draft(
    tmp_path: Path,
) -> None:
    """DRAFT allows folding a solo rider into a brand-new team."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    result = preview(_newbies_forming_file(tmp_path), roster)

    assert result.conflicts == ()


def test_preview_pooled_promoting_a_solo_rider_into_a_fresh_team_while_running_conflicts(
    tmp_path: Path,
) -> None:
    """RUNNING refuses it too: the conversion is DRAFT-only."""
    roster = _pooled_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.status = RideStatus.RUNNING

    result = preview(_newbies_forming_file(tmp_path), roster)

    assert len(result.conflicts) == 1
    assert _SOLO_TO_TEAM_LOCKED_PROBLEM in result.conflicts[0].problem


def test_commit_pooled_promoting_a_solo_rider_into_a_fresh_team_in_draft(
    tmp_path: Path,
) -> None:
    """commit() dissolves Alex's solo entry into the Newbies team."""
    roster = _pooled_roster()
    alex = roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    result = preview(_newbies_forming_file(tmp_path), roster)

    report = commit(result)

    newbies = roster.entries[0]
    assert (
        alex in roster.entries,
        [r.full_name for r in newbies.riders],
        (report.inserted_count, report.joined_count, report.extracted_count),
    ) == (False, ["Alex", "Newby"], (1, 0, 0))


def test_preview_pooled_team_over_max_reports_team_over_max_conflict(
    tmp_path: Path,
) -> None:
    """A pooled group over max_team_size(4) is also a conflict."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first=letter, type_="team", team="Big", number=str(i))
            for i, letter in enumerate("ABCDE", start=1)
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.conflicts == (
        ImportConflict(row=2, problem="team of 5 riders exceeds the maximum of 4 (team-over-max)"),
    )


# ------------------------------------------------- preview: notes join


def test_preview_pooled_team_notes_join_non_empty_member_notes(tmp_path: Path) -> None:
    """A team's notes join every non-empty member note with '; '."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="1", notes="flat tire"),
            _Row(first="Cy", type_="team", team="Wolves", number="2"),
            _Row(first="Zed", type_="team", team="Wolves", number="3", notes="spare batteries"),
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.entries[0].notes == "flat tire; spare batteries"


# ======================================================== E3.3.3 export


def test_export_empty_roster_writes_header_only_file(tmp_path: Path) -> None:
    """An empty roster exports to exactly the canonical header line."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()

    export(roster, path)

    assert _read_lines(path) == [_CANONICAL_HEADER]


def test_export_header_is_identical_for_both_plate_models(tmp_path: Path) -> None:
    """One header replaces the two plate-model shapes (Phase 2)."""
    pooled_path = tmp_path / "pooled.csv"
    relay_path = tmp_path / "relay.csv"

    export(_pooled_roster(), pooled_path)
    export(_relay_roster(), relay_path)

    assert _read_lines(pooled_path) == _read_lines(relay_path) == [_CANONICAL_HEADER]


def test_export_solo_entry_writes_one_row(tmp_path: Path) -> None:
    """A solo entry exports as one solo row with its own plate."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="Tremblay", plate="1")

    export(roster, path)

    assert _read_lines(path)[1] == "Alex,Tremblay,solo,,1,"


def test_export_relay_team_writes_one_row_per_member_sharing_the_entry_plate(
    tmp_path: Path,
) -> None:
    """team_relay: each member row carries the shared entry plate."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="Team A",
        riders=[Rider(first_name="Bo", last_name=""), Rider(first_name="Cy", last_name="")],
        plate="7",
    )

    export(roster, path)

    assert _read_lines(path)[1:] == ["Bo,,team,Team A,7,", "Cy,,team,Team A,7,"]


def test_export_pooled_team_writes_each_members_own_plate(tmp_path: Path) -> None:
    """rider_pooled: each member row carries that rider's own plate."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    roster.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
        ],
    )

    export(roster, path)

    assert _read_lines(path)[1:] == ["Bo,,team,Wolves,2,", "Cy,,team,Wolves,3,"]


def test_export_solo_entry_notes_land_in_the_final_column(tmp_path: Path) -> None:
    """An entry's notes are the CSV row's final column."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    entry = roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.update_entry(entry, notes="late scratch")

    export(roster, path)

    assert _read_lines(path)[1] == "Alex,,solo,,1,late scratch"


def test_export_pooled_team_writes_notes_on_first_row_only(tmp_path: Path) -> None:
    """A team's notes land on its first member row only (round-trip)."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    entry = roster.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
        ],
    )
    roster.update_entry(entry, notes="flat tire; spare batteries")

    export(roster, path)

    assert _read_lines(path)[1:] == [
        "Bo,,team,Wolves,2,flat tire; spare batteries",
        "Cy,,team,Wolves,3,",
    ]


def test_export_then_preview_reimports_a_pooled_team_with_zero_conflicts(
    tmp_path: Path,
) -> None:
    """A worked example: export, then re-preview a fresh roster."""
    path = tmp_path / "out.csv"
    source = _pooled_roster()
    source.create_solo_entry(first_name="Alex", last_name="", plate="1")
    source.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
        ],
    )
    export(source, path)
    target = _pooled_roster()

    result = preview(path, target)

    assert (result.rider_count, result.team_count, result.conflicts) == (3, 1, ())


def test_export_of_an_uppercase_team_name_reimports_as_its_normalized_form(
    tmp_path: Path,
) -> None:
    """TEAMNAME's normalized form is the team name (Phase 2 spec)."""
    path = tmp_path / "out.csv"
    source = _pooled_roster()
    source.create_team_entry(
        display_name="Full Send",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
        ],
    )
    export(source, path)
    target = _pooled_roster()

    result = preview(path, target)

    assert result.entries[0].display_name == "full send"


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
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.create_team_entry(
        display_name="Team A",
        riders=[Rider(first_name="Bo", last_name=""), Rider(first_name="Cy", last_name="")],
        plate="7",
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
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    export(roster, path, placed=[_placed("1", "AS AH")])

    assert _read_lines(path)[0] == f"{_CANONICAL_HEADER},laps,cards,best_hand,total_time"


def test_export_finished_relay_rows_carry_the_matching_placed_values(
    tmp_path: Path,
) -> None:
    """Every rider row appends the entry's laps, cards, hand, time."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.create_team_entry(
        display_name="Team A",
        riders=[Rider(first_name="Bo", last_name=""), Rider(first_name="Cy", last_name="")],
        plate="7",
    )
    placed = [
        _placed("1", "AS AH", laps=5, total_time=20811.0),
        _placed("7", "9H 9D 9S", laps=4, total_time=19000.0),
    ]

    export(roster, path, placed=placed)

    assert _read_lines(path)[1:] == [
        "Alex,,solo,,1,,5,2,Pair — Aces,20811.0",
        "Bo,,team,Team A,7,,4,3,Three of a Kind — Nines,19000.0",
        "Cy,,team,Team A,7,,4,3,Three of a Kind — Nines,19000.0",
    ]


def test_export_finished_pooled_rows_repeat_entry_stats_on_every_rider_row(
    tmp_path: Path,
) -> None:
    """Pooled rows are per-rider, so each carries the entry's stats."""
    path = tmp_path / "out.csv"
    roster = _pooled_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.create_team_entry(
        display_name="Wolves",
        riders=[
            Rider(first_name="Bo", last_name="", plate="2"),
            Rider(first_name="Cy", last_name="", plate="3"),
        ],
    )
    placed = [
        _placed("1", "AS AH", laps=2, total_time=6000.0),
        _placed("2", "KS KD", laps=3, total_time=9000.0),
    ]

    export(roster, path, placed=placed)

    lines = _read_lines(path)
    assert (lines[0], lines[1:]) == (
        f"{_CANONICAL_HEADER},laps,cards,best_hand,total_time",
        [
            "Alex,,solo,,1,,2,2,Pair — Aces,6000.0",
            "Bo,,team,Wolves,2,,3,2,Pair — Kings,9000.0",
            "Cy,,team,Wolves,3,,3,2,Pair — Kings,9000.0",
        ],
    )


def test_export_finished_ride_writes_dnf_entry_stats_from_placed(tmp_path: Path) -> None:
    """R-33: a DNF placed entry keeps laps/cards; export writes them."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    export(roster, path, placed=[_placed("1", "AS AH", laps=3, total_time=12345.0, dnf=True)])

    assert _read_lines(path)[1] == "Alex,,solo,,1,,3,2,Pair — Aces,12345.0"


def test_export_finished_ride_ignores_extra_placed_rows_with_no_matching_entry(
    tmp_path: Path,
) -> None:
    """A caller may pass extra placed rows; they are ignored."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    placed = [
        _placed("1", "AS AH", laps=5, total_time=20811.0),
        _placed("99", "KS KD", laps=2, total_time=999.0),  # no such entry
    ]

    export(roster, path, placed=placed)

    assert _read_lines(path)[1] == "Alex,,solo,,1,,5,2,Pair — Aces,20811.0"


@pytest.mark.parametrize("status", [RideStatus.DRAFT, RideStatus.RUNNING])
def test_export_with_standings_before_finish_raises_csv_io_error(
    tmp_path: Path, *, status: RideStatus
) -> None:
    """Standings columns exist only once the ride is finished."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = status
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    with pytest.raises(CsvIoError, match=re.escape("finished ride")):
        export(roster, path, placed=[_placed("1", "AS AH")])


def test_export_finished_ride_with_entry_missing_from_standings_raises_naming_plate(
    tmp_path: Path,
) -> None:
    """An entry absent from *placed* fails loudly, naming its plate."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    roster.create_solo_entry(first_name="Bo", last_name="", plate="2")

    with pytest.raises(CsvIoError, match=re.escape("no standings for plate 1")):
        export(roster, path, placed=[_placed("2", "AS AH")])


def test_export_placed_none_on_finished_ride_writes_plain_header(tmp_path: Path) -> None:
    """Even FINISHED, omitting *placed* keeps the roster-only shape."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    export(roster, path)

    assert _read_lines(path)[0] == _CANONICAL_HEADER


# ------------------------------------------- R-52 atomic export writes


def test_export_stages_a_same_directory_temp_file_then_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """export() swaps the destination in wholesale via os.replace()."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    export(roster, path)

    assert calls == [(str(path.with_name(path.name + ".tmp")), str(path))]
    assert _read_lines(path) == [_CANONICAL_HEADER, "Alex,,solo,,1,"]
    assert sorted(tmp_path.iterdir()) == [path]


def test_export_standings_stages_a_temp_file_then_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """export_standings() stages a temp sibling, then swaps it in."""
    path = tmp_path / "standings.csv"
    placed = [_placed("88", "9S 9D 9C 9H 2C", laps=11, total_time=20_000.0)]
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    export_standings(placed, path, show_times=True)

    assert calls == [(str(path.with_name(path.name + ".tmp")), str(path))]
    assert _read_lines(path)[1] == "1,88,Rider,11,Four of a Kind — Nines,20000.0"
    assert sorted(tmp_path.iterdir()) == [path]


def test_export_failure_during_replace_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash at the swap leaves the old complete file intact."""
    path = tmp_path / "out.csv"
    path.write_text("previous complete export\n", encoding="utf-8")
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

    def crashing_replace(_src: str, _dst: str) -> None:
        raise OSError("simulated crash during atomic replace")

    monkeypatch.setattr(os, "replace", crashing_replace)

    with pytest.raises(OSError, match=re.escape("simulated crash")):
        export(roster, path)

    assert path.read_text(encoding="utf-8") == "previous complete export\n"


def test_export_finished_ride_with_empty_placed_raises_naming_plate(tmp_path: Path) -> None:
    """An empty *placed* list covers no entry; the plate is named."""
    path = tmp_path / "out.csv"
    roster = _relay_roster()
    roster.status = RideStatus.FINISHED
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")

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
    roster.create_solo_entry(first_name="Alex", last_name="", plate="1")
    placed = [_placed("1", " ".join(codes), laps=laps, total_time=total_time)]

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "out.csv"
        export(roster, path, placed=placed)
        line = _read_lines(path)[1]

    assert float(line.split(",")[-1]) == total_time


# ======================================================= GORBA fixture


def test_preview_gorba_fixture_imports_as_is_against_a_relay_roster() -> None:
    """The real registration export: header-mapped, grouped, plated.

    The GORBA file has no NUMBER column at all, so every entry plate is
    auto-assigned; its trailing footer rows (empty lines, "Basic info…",
    "Filters", "event: …") have no rider name and are skipped; and its
    "Emergency Contact Number (xxx-xxx-xxxx)" column is never treated
    as the race-plate NUMBER column.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)

    result = preview(FIXTURES / "gorba_epic.csv", roster)

    # Eight riders registered as Team but are the only row of their team
    # (teammates registered separately or later) -- team-under-min
    # conflict at each lone team's first row.
    assert result.conflicts == tuple(
        ImportConflict(
            row=row, problem="team of 1 rider is below the minimum of 2 (team-under-min)"
        )
        for row in (17, 36, 41, 45, 59, 84, 85, 86)
    )
    assert result.rider_count == 101
    assert result.team_count == 31
    assert len(result.entries) == 71
    assert all(entry.plate.isdigit() for entry in result.entries)
    assert len({entry.plate for entry in result.entries}) == 71
    # Solo rows became solo entries; team rows grouped by name.
    solo_entries = [e for e in result.entries if e.type is EntryType.SOLO]
    team_entries = [e for e in result.entries if e.type is EntryType.TEAM]
    assert len(solo_entries) == 40
    assert all(len(e.riders) == 1 for e in solo_entries)
    # BNBA1 rows and BNBA 1 rows stay two distinct teams (collapse never
    # deletes the single space between words), each with its two riders.
    bnba1 = next(e for e in team_entries if e.display_name == "bnba1")
    assert [r.full_name for r in bnba1.riders] == ["Lars Pastrik", "Matt Plaumann"]
    bnba_1 = next(e for e in team_entries if e.display_name == "bnba 1")
    assert [r.full_name for r in bnba_1.riders] == ["Pedro Faria", "Pamela Santos"]
    # The full-send pair and the four Brady Bunch rows form their teams.
    full_send = next(e for e in team_entries if e.display_name == "full send")
    assert len(full_send.riders) == 2
    brady = next(e for e in team_entries if e.display_name == "bathgate brady bunch")
    assert len(brady.riders) == 4
    # No plate ever equals an Emergency Contact phone number.
    assert not any(entry.plate in {"519-831-6613", "2269794431"} for entry in result.entries)


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


# ========================================================= T-7 property


@given(row_count=st.integers(min_value=1, max_value=30))
@settings(max_examples=25, deadline=None)
def test_preview_clean_solo_rows_rider_count_matches_generated_row_count(
    row_count: int,
) -> None:
    """rider_count always equals the clean solo rows given (T-7)."""
    rows = [
        _Row(first="Rider", last=str(i), type_="solo", number=str(i))
        for i in range(1, row_count + 1)
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = _unified_file(Path(tmp_dir), rows)
        roster = _relay_roster()

        result = preview(path, roster)

    assert (result.rider_count, result.team_count, result.conflicts) == (row_count, 0, ())


def test_csvio_all_lists_the_public_api_sorted() -> None:
    """csvio.__all__ exposes every public symbol, sorted (RUF022)."""
    assert csvio.__all__ == sorted(csvio.__all__)
    assert set(csvio.__all__) == {
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
    }


# ================================================= branch-coverage pins


def test_preview_pooled_duplicate_rider_plate_reports_one_conflict(tmp_path: Path) -> None:
    """rider_pooled: one plate on two rows is a duplicate conflict."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Wolves", number="7"),
            _Row(first="Cy", type_="team", team="Wolves", number="7"),
        ],
    )
    roster = _pooled_roster()

    result = preview(path, roster)

    assert result.conflicts == (ImportConflict(row=3, problem="duplicate plate 7"),)


def test_preview_relay_auto_assigned_plates_skip_a_run_of_explicit_plates(
    tmp_path: Path,
) -> None:
    """Auto-assignment skips every explicit plate, run or gap."""
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Alex", type_="solo", number="3"),
            _Row(first="Bo", type_="solo", number="4"),
            _Row(first="Cy", type_="solo"),  # -> 1, not 3/4
            _Row(first="Do", type_="solo"),  # -> 2
            _Row(first="El", type_="solo"),  # -> 5
        ],
    )
    roster = _relay_roster()

    result = preview(path, roster)

    assert [entry.plate for entry in result.entries] == ["3", "4", "1", "2", "5"]


def test_commit_relay_matched_team_rename_updates_display_name_in_place(
    tmp_path: Path,
) -> None:
    """A same-composition relay team rename updates in place (S7)."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="team a",
        riders=[Rider(first_name="Bo", last_name=""), Rider(first_name="Cy", last_name="")],
        plate="10",
    )
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Team Aaa", number="10"),
            _Row(first="Cy", type_="team", team="Team Aaa", number="10"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    assert (roster.entries[0].display_name, report.updated_count) == ("team aaa", 1)


def test_commit_relay_matched_solo_rename_changes_the_last_name_too(
    tmp_path: Path,
) -> None:
    """A matched solo rename updates the rider's last name as well."""
    roster = _relay_roster()
    roster.create_solo_entry(first_name="Alex", last_name="Smith", plate="1")
    path = _unified_file(tmp_path, [_Row(first="Alex", last="Smythe", type_="solo", number="1")])
    result = preview(path, roster)

    report = commit(result)

    rider = roster.entries[0].riders[0]
    assert (rider.first_name, rider.last_name, report.updated_count) == ("Alex", "Smythe", 1)


def test_commit_relay_matched_team_with_no_changes_reports_zero_updates(
    tmp_path: Path,
) -> None:
    """A relay team re-import that changes nothing updates nothing."""
    roster = _relay_roster()
    roster.create_team_entry(
        display_name="team a",
        riders=[Rider(first_name="Bo", last_name=""), Rider(first_name="Cy", last_name="")],
        plate="10",
    )
    path = _unified_file(
        tmp_path,
        [
            _Row(first="Bo", type_="team", team="Team A", number="10"),
            _Row(first="Cy", type_="team", team="Team A", number="10"),
        ],
    )
    result = preview(path, roster)

    report = commit(result)

    assert (report.inserted_count, report.updated_count, report.audit_events) == (0, 0, ())
