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
    db_path, crossings = race_child_module.parse_race_env("race-shared.db", crossings_env)

    assert db_path == Path("race-shared.db")
    assert crossings == expected


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
        crossing_rows=3,
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
    assert data["crossing_rows"] == 3
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
    ride_id = store_staging_module.running_ride_with_roster(db_path)
    facts = store_staging_module.race_db_facts(db_path)
    facts_again = store_staging_module.race_db_facts(db_path)

    assert facts["crossings"] == 0
    assert facts["audit_actions"] == ["start"]
    assert [row["id"] for row in facts["rides"]] == [ride_id]
    # The staged session row is quit-stamped and carries the ride.
    assert facts["sessions"][-1]["closed_at"] is not None
    assert facts["sessions"][-1]["active_ride_id"] == ride_id
    # A read must never grow the session table (Store.open would).
    assert len(facts_again["sessions"]) == len(facts["sessions"])
