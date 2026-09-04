# SPDX-License-Identifier: GPL-3.0-only
"""Headless unit tests for the E9.2.1 race child's non-wx logic.

``race_child.py`` (tests/acceptance) is the child program of the
full-race acceptance test (R-74): it opens the store-backed app on a
shared ``rides.db``, records crossings through the real ``plate_input``
and either waits to be SIGKILLed or quits through the real File ▸ Exit
flow. Its JSON envelope, env parsing and crossing-count handling are
pure functions -- unit-tested here headless, the same way every other
piece of UI *logic* in this codebase is (R-71). The wx-touching
scenarios themselves only run in the Tart VM (``pytest.mark.functional``
on ``test_full_race.py``).

``store_staging.py`` (tests/functional) is the shared home of the
store-staging helpers the E5/E9 child-scenario suites use; the two
helpers the race test depends on (``running_ride_with_roster``,
``race_db_facts``) are also pure Store/sqlite logic and are pinned here.

``tests/acceptance/`` and ``tests/functional/`` carry no
``__init__.py`` (implicit PEP 420 namespace packages), so they are only
importable once their directories are on ``sys.path`` -- the same
insertion ``test_scenario_runner.py`` makes for ``tests/functional``.
The imports are deferred into fixtures so a missing ``race_child.py`` /
``store_staging.py`` (this task's RED phase) fails this module's own
tests, never collection of the whole tests/unit session.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from rivercrossing.store import Store

if TYPE_CHECKING:
    from types import ModuleType

_ACCEPTANCE_DIR = Path(__file__).resolve().parents[1] / "acceptance"
_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
for _dir in (_ACCEPTANCE_DIR, _FUNCTIONAL_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))


@pytest.fixture(scope="module")
def race_child_module() -> ModuleType:
    """Return tests/acceptance.race_child, imported lazily."""
    import race_child  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast("ModuleType", race_child)


@pytest.fixture(scope="module")
def store_staging_module() -> ModuleType:
    """Return tests/functional.store_staging, imported lazily."""
    import store_staging  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast("ModuleType", store_staging)


@pytest.mark.parametrize("db_env", [None, ""])
def test_parse_race_env_blank_db_raises_value_error(
    race_child_module: ModuleType, db_env: str | None
) -> None:
    """A missing or empty RIVERCROSSING_RACE_DB is refused."""
    with pytest.raises(ValueError, match=re.escape("must name a rides database")):
        race_child_module.parse_race_env(db_env, None)


@pytest.mark.parametrize(
    ("crossings_env", "expected"),
    [
        (None, 3),
        ("", 3),
        ("0", 0),
        ("1", 1),
        ("5", 5),
    ],
)
def test_parse_race_env_crossings_boundaries(
    race_child_module: ModuleType, crossings_env: str | None, expected: int
) -> None:
    """Unset/empty defaults to 3; 0, 1 and many parse verbatim."""
    env = race_child_module.parse_race_env("race-shared.db", crossings_env)

    assert env.db_path == Path("race-shared.db")
    assert env.crossings == expected


def test_parse_race_env_returns_the_full_race_env_defaults(
    race_child_module: ModuleType,
) -> None:
    """The new fields default: no CSV, 20 plates, default bound."""
    env = race_child_module.parse_race_env("race-shared.db", None)

    assert env.csv_path is None
    assert env.plates == 20
    assert env.exports_dir is None
    assert env.bound_seconds == race_child_module.scenario_runner.SCENARIO_CHILD_BOUND_SECONDS


def test_parse_race_env_optional_csv_and_exports_dir_parse_to_paths(
    race_child_module: ModuleType,
) -> None:
    """A set RIVERCROSSING_RACE_CSV / _EXPORTS_DIR parse to Paths."""
    env = race_child_module.parse_race_env(
        "race-shared.db",
        None,
        csv_env="tmp/riders.csv",
        exports_dir_env="tmp/exports",
    )

    assert env.csv_path == Path("tmp/riders.csv")
    assert env.exports_dir == Path("tmp/exports")


@pytest.mark.parametrize(
    ("plates_env", "expected"),
    [
        (None, 20),
        ("", 20),
        ("1", 1),
        ("20", 20),
        ("24", 24),
    ],
)
def test_parse_race_env_plates_boundaries(
    race_child_module: ModuleType, plates_env: str | None, expected: int
) -> None:
    """Unset/empty defaults to 20; 1 and many parse verbatim."""
    env = race_child_module.parse_race_env("race-shared.db", None, plates_env=plates_env)

    assert env.plates == expected


@pytest.mark.parametrize("plates_env", ["0", "-1", "abc"])
def test_parse_race_env_non_positive_plates_raise_value_error(
    race_child_module: ModuleType, plates_env: str
) -> None:
    """A non-positive or non-integer plate count is refused."""
    expected = "RIVERCROSSING_RACE_PLATES must be a positive integer"
    with pytest.raises(ValueError, match=re.escape(expected)):
        race_child_module.parse_race_env("race-shared.db", None, plates_env=plates_env)


def test_parse_race_env_bound_seconds_defaults_and_parses(
    race_child_module: ModuleType,
) -> None:
    """Unset bound uses the passed default; a set one parses."""
    default = race_child_module.parse_race_env("race-shared.db", None)
    explicit = race_child_module.parse_race_env(
        "race-shared.db", None, bound_env="45", default_bound=12
    )

    assert default.bound_seconds == race_child_module.scenario_runner.SCENARIO_CHILD_BOUND_SECONDS
    assert explicit.bound_seconds == 45


@pytest.mark.parametrize("bound_env", ["0", "abc"])
def test_parse_race_env_non_positive_bound_raises_value_error(
    race_child_module: ModuleType, bound_env: str
) -> None:
    """A non-positive or non-integer bound is refused."""
    with pytest.raises(
        ValueError, match=re.escape("RIVERCROSSING_RACE_BOUND_SECONDS must be a positive integer")
    ):
        race_child_module.parse_race_env("race-shared.db", None, bound_env=bound_env)


def test_wave_plates_cycles_plates_in_wave_order(race_child_module: ModuleType) -> None:
    """The scripted wave pattern cycles 1..plates in strict order."""
    assert race_child_module._wave_plates(5, 3) == ["1", "2", "3", "1", "2"]
    assert race_child_module._wave_plates(0, 20) == []
    assert race_child_module._wave_plates(1, 20) == ["1"]


def test_parse_race_env_non_numeric_crossings_raises_value_error(
    race_child_module: ModuleType,
) -> None:
    """A non-integer RIVERCROSSING_RACE_CROSSINGS is refused."""
    with pytest.raises(ValueError, match=re.escape("must be an integer")):
        race_child_module.parse_race_env("race-shared.db", "abc")


def test_parse_race_env_negative_crossings_raises_value_error(
    race_child_module: ModuleType,
) -> None:
    """A negative RIVERCROSSING_RACE_CROSSINGS is refused."""
    with pytest.raises(ValueError, match=re.escape("must not be negative")):
        race_child_module.parse_race_env("race-shared.db", "-1")


def test_envelope_success_carries_data(race_child_module: ModuleType) -> None:
    """The success envelope carries the data block verbatim."""
    result = race_child_module.envelope(ok=True, error=None, data={"crossings_recorded": 3})

    assert result == {"ok": True, "error": None, "data": {"crossings_recorded": 3}}


def test_envelope_error_carries_message(race_child_module: ModuleType) -> None:
    """The error envelope carries the message and no data."""
    result = race_child_module.envelope(ok=False, error="boom", data=None)

    assert result == {"ok": False, "error": "boom", "data": None}


def test_race_data_carries_verbatim_facts(race_child_module: ModuleType) -> None:
    """The report block names every fact the parent asserts on."""
    data = race_child_module.race_data(
        scenario="record_crash",
        exit_mode="crash",
        ride_id=1,
        resume_dlg_shown=True,
        resume_state="running_at_exit",
        resume_message='"GORBA EPIC 2026" is still running.',
        crossings_recorded=3,
        feed_rows=3,
        feed_plates=["12", "12", "12"],
        audit_actions=["start", "record_crossing", "record_crossing"],
        recorded_crossings=3,
        status_label="RUNNING",
        clock_elapsed="",
    )

    assert data["scenario"] == "record_crash"
    assert data["exit_mode"] == "crash"
    assert data["ride_id"] == 1
    assert data["resume_dlg_shown"] is True
    assert data["resume_state"] == "running_at_exit"
    assert data["resume_message"] == '"GORBA EPIC 2026" is still running.'
    assert data["crossings_recorded"] == 3
    assert data["feed_rows"] == 3
    assert data["feed_plates"] == ["12", "12", "12"]
    assert data["audit_actions"] == ["start", "record_crossing", "record_crossing"]
    assert data["recorded_crossings"] == 3
    assert data["status_label"] == "RUNNING"
    assert data["clock_elapsed"] == ""


def test_running_ride_with_roster_stages_quit_keep_running_session(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """The staged ride reads RUNNING_AT_EXIT with its roster saved."""
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.running_ride_with_roster(db_path)
    store = Store.open(db_path)
    try:
        previous = store.previous_session()
        assert previous.state.value == "running_at_exit"
        assert previous.ride_id == ride_id
        assert [row.name for row in store.rides()] == ["GORBA EPIC 2026"]
        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["12", "77"]
    finally:
        store.close()


def test_running_ride_with_roster_honours_actual_start_override(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """The optional actual_start lands in the staged start event."""
    db_path = tmp_path / "rides.db"
    start = datetime(2026, 9, 20, 8, 0)  # noqa: DTZ001 -- naive local, Store's own contract
    ride_id = store_staging_module.running_ride_with_roster(db_path, actual_start=start)
    with sqlite3.connect(str(db_path)) as conn:
        payload = conn.execute(
            "SELECT payload_json FROM audit WHERE ride_id = ? AND action = 'start'",
            (ride_id,),
        ).fetchone()[0]

    assert json.loads(payload)["actual_start"] == "2026-09-20T08:00:00"


def test_race_db_facts_reports_rows_without_inserting_a_session(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """race_db_facts reads tables directly; it never opens a Store."""
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.create_library_ride(
        db_path, name="GORBA EPIC 2026", running=True
    )
    facts = store_staging_module.race_db_facts(db_path)
    facts_again = store_staging_module.race_db_facts(db_path)

    # The audit trail is the recorded-crossing proof (Store.append's
    # one channel); the spec §2 crossing table is only populated by
    # EPIC 5's writer, so it is deliberately not the fact reported.
    assert facts["record_crossing_count"] == 1
    assert facts["audit_actions"] == ["start", "record_crossing"]
    assert [row["id"] for row in facts["rides"]] == [ride_id]
    # A staged, non-running ride's session is quit-stamped and carries
    # no ride (create_library_ride opens no active_ride_id session).
    assert facts["sessions"][-1]["closed_at"] is None
    # A read must never grow the session table (Store.open would).
    assert len(facts_again["sessions"]) == len(facts["sessions"])


def test_running_ride_with_roster_session_carries_the_ride(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """The staged session row is quit-stamped and names the ride."""
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.running_ride_with_roster(db_path)
    facts = store_staging_module.race_db_facts(db_path)

    assert facts["sessions"][-1]["closed_at"] is not None
    assert facts["sessions"][-1]["active_ride_id"] == ride_id


def test_rich_race_roster_builds_six_active_mixed_entries(
    store_staging_module: ModuleType,
) -> None:
    """rich_race_roster returns 4 solo + 2 team entries, all ACTIVE."""
    roster = store_staging_module.rich_race_roster()

    assert len(roster.entries) == 6
    assert [entry.type.value for entry in roster.entries] == [
        "solo",
        "solo",
        "solo",
        "solo",
        "team",
        "team",
    ]
    assert [entry.status.value for entry in roster.entries] == ["active"] * 6
    assert [entry.team_size for entry in roster.entries] == [1, 1, 1, 1, 2, 3]
    assert [entry.plate for entry in roster.entries] == ["1", "2", "3", "4", "11", "21"]


def test_running_ride_with_roster_stages_the_rich_roster(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """The rich roster round-trips through save_roster/roster_for."""
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.running_ride_with_roster(
        db_path, roster=store_staging_module.rich_race_roster()
    )
    store = Store.open(db_path)
    try:
        loaded = store.roster_for(ride_id)
    finally:
        store.close()

    assert [entry.plate for entry in loaded.entries] == ["1", "2", "3", "4", "11", "21"]
    assert [entry.team_size for entry in loaded.entries] == [1, 1, 1, 1, 2, 3]
    assert [entry.type.value for entry in loaded.entries] == [
        "solo",
        "solo",
        "solo",
        "solo",
        "team",
        "team",
    ]
    assert [entry.status.value for entry in loaded.entries] == ["active"] * 6


def test_running_ride_with_roster_without_roster_keeps_library_roster(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """No roster arg still stages the 2-entry library_roster default."""
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.running_ride_with_roster(db_path)
    store = Store.open(db_path)
    try:
        loaded = store.roster_for(ride_id)
    finally:
        store.close()

    assert [entry.plate for entry in loaded.entries] == ["12", "77"]
    assert [entry.team_size for entry in loaded.entries] == [1, 2]


def test_running_ride_with_roster_stages_a_team_relay_ride(
    store_staging_module: ModuleType, tmp_path: Path
) -> None:
    """plate_model=TEAM_RELAY creates a relay ride; an empty shell fits.

    The E9.2.2 sim stages a TEAM_RELAY ride with an empty relay-shaped
    roster: resume installs that roster on the shared context (the
    resume-roster fix), so the child's CSV import previews against the
    ride's real TEAM_RELAY shape and commits the fixture cleanly. The
    ride row carries the relay config, and the saved empty roster
    round-trips through ``Store.roster_for`` as zero entries.
    """
    db_path = tmp_path / "rides.db"
    ride_id = store_staging_module.running_ride_with_roster(
        db_path,
        plate_model=store_staging_module.PlateModel.TEAM_RELAY,
        roster=store_staging_module.Roster(
            entry_mode=store_staging_module.EntryMode.MIXED,
            plate_model=store_staging_module.PlateModel.TEAM_RELAY,
        ),
    )
    store = Store.open(db_path)
    try:
        config = store.load_engine(ride_id).config
        loaded = store.roster_for(ride_id)
    finally:
        store.close()

    assert config.plate_model.value == "team_relay"
    assert loaded.plate_model.value == "team_relay"
    assert loaded.entry_mode.value == "mixed"
    assert loaded.entries == ()
