# SPDX-License-Identifier: GPL-3.0-only
"""R-74 full scripted race acceptance: import, run, crash, quit, export.

Extends the E9.2.1 multi-process seam (``test_full_race.py``) to the
whole R-74 acceptance race, against ONE temp ``rides.db`` through the
real store-backed UI. Three spawned children (``race_child.py``
scenarios) run the script:

1. **``setup_import_and_run``** -- resumes the staged ride, imports a
   20-rider riders CSV through the real ``mi_import_csv`` route (the
   R-74 persistence gap: the route must save the imported roster to
   the active store ride, or the relaunches below lose every rider),
   types 300 crossings through the real ``plate_input`` on an
   advanceable injected clock (every lap above ``min_lap_s``, so no
   card is held), arms/confirms the stop and continues (entry locks,
   then re-enables with the start preserved), and waits for the
   parent's SIGKILL -- the crash leg at this scale.
2. **``resume_verify_quit``** -- relaunch reads the crash wording
   ("closed unexpectedly"), the 300 crossings survive, 5 more are
   recorded, and the real File ▸ Exit flow quits cleanly.
3. **``finish_and_exports``** -- relaunch reads the quit wording
   ("You quit at"), the 305 crossings survive, Finish Ride runs,
   a baseline export stamps the export watermark, Reopen Ride runs,
   Add Crossing at Time + Void Card corrections land (the export
   watermark is now stale), Finish Ride re-locks to FINISHED, all
   four Results exports (HTML/PDF/poster/CSV) write through the real
   menu rows to real files, and File ▸ Exit quits cleanly.

The parent never opens a ``Store`` itself (``Store.open`` would
insert an ``app_session`` row and corrupt the session sequence under
test -- the same rule ``test_full_race.py`` documents); every db read
goes through ``store_staging.race_db_facts``, and the exports are
verified by parsing the files: the HTML ``race-data`` JSON block, the
PDFs via ``pypdf``, the standings CSV via ``csv`` -- each compared to
the standings the child computed from its live engine (the same
parse/render seam the unit golden tests pin, reused here per R-74).

Only runnable where real wx windows can open (the Tart VM); on a bare
host the children cannot construct ``main_frame``. ``tests/
functional/`` and ``tests/acceptance/`` carry no ``__init__.py``, so
``scenario_runner``/``store_staging`` are importable only once the
directory is on ``sys.path`` (the same insertion
``test_full_race.py`` makes); the imports are deferred into a fixture
so a missing module fails this module's own tests, never collection.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pypdf import PdfReader

if TYPE_CHECKING:
    from types import ModuleType

_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))

pytestmark = pytest.mark.functional

# The child's public env contract (race_child.py module docstring),
# hardcoded here so this module stays importable in the RED phase; a
# drift surfaces as a child failure, never a silent pass.
RACE_DB_ENV = "RIVERCROSSING_RACE_DB"
RACE_CROSSINGS_ENV = "RIVERCROSSING_RACE_CROSSINGS"
RACE_CSV_ENV = "RIVERCROSSING_RACE_CSV"
RACE_PLATES_ENV = "RIVERCROSSING_RACE_PLATES"
RACE_BOUND_ENV = "RIVERCROSSING_RACE_BOUND_SECONDS"
RACE_EXPORTS_DIR_ENV = "RIVERCROSSING_RACE_EXPORTS_DIR"
RACE_CHILD = Path(__file__).resolve().parent / "race_child.py"

# The scripted race's numbers. 300 = 15 waves x 20 plates; the child
# resumes with the crash wording and records 5 more; child C then adds
# one missed crossing, so the final recorded count is 306.
_RIDER_COUNT = 20
_WAVE_CROSSINGS = 300
_RESUME_EXTRA_CROSSINGS = 5
_ADDED_CROSSINGS = 1
# The console feed is capped at the designed "latest 30" (R-32,
# data_source.FEED_CAP), so feed_rows reads 30 after any wave past the
# cap -- the audit count is the real proof, feed_rows proves rendering.
_FEED_CAP = 30

# The children self-terminate (os._exit 124) after their own bound;
# heavy children (300 typed crossings + CSV import) get 120 s, the
# quit-only children 90 s. The parent's timeouts sit above both.
_HEAVY_BOUND_S = 120
_QUIT_BOUND_S = 90
_CRASH_READY_TIMEOUT_S = 120.0
_RUN_CHILD_TIMEOUT_S = 150.0

# The staged ride's pinned actual_start (store_staging); the child's
# injected clock reads it back from the audit log, so no cross-file
# coupling on the crossing times.
_RACE_ACTUAL_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive local, Store's own contract


@pytest.fixture(scope="module")
def race_support() -> ModuleType:
    """Return a namespace of the race-test support modules, lazily."""
    import scenario_runner  # type: ignore[import-not-found]  # noqa: PLC0415
    import store_staging  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast(
        "ModuleType",
        SimpleNamespace(scenario_runner=scenario_runner, store_staging=store_staging),
    )


def _write_riders_csv(path: Path, count: int) -> None:
    """Write a rider_pooled CSV of *count* solo riders ``1..count``."""
    lines = ["plate,name,team_name,notes"]
    lines.extend(f"{number},Rider {number:02d},," for number in range(1, count + 1))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_RACE_ENV_NAMES = (
    RACE_DB_ENV,
    RACE_CROSSINGS_ENV,
    RACE_CSV_ENV,
    RACE_PLATES_ENV,
    RACE_BOUND_ENV,
    RACE_EXPORTS_DIR_ENV,
)


def _run_child(  # noqa: PLR0913 -- (support, scenario, db, crossings, extra, timeout): the spawn inputs
    support: Any,  # noqa: ANN401 -- the lazily-imported module namespace
    scenario: str,
    *,
    db: Path,
    crossings: int,
    extra: dict[str, str] | None = None,
    timeout: float = _RUN_CHILD_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one self-exiting child via _run_bounded; decode its envelope.

    The env vars are set on ``os.environ`` around the spawn (Popen
    inherits it) and restored after, so ``scenario_runner._run_bounded``
    itself stays untouched (the same technique ``test_full_race``'s
    ``_run_quit_child`` uses). Returns ``_decode_scenario_output``'s
    envelope with the captured context attached.
    """
    saved = {name: os.environ.get(name) for name in _RACE_ENV_NAMES}
    os.environ[RACE_DB_ENV] = str(db)
    os.environ[RACE_CROSSINGS_ENV] = str(crossings)
    if extra:
        os.environ.update(extra)
    try:
        completed = support.scenario_runner._run_bounded(
            [sys.executable, str(RACE_CHILD), scenario], timeout
        )
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return support.scenario_runner._decode_scenario_output(scenario, completed)


def _last_nonempty(lines: list[str]) -> str | None:
    """Return the last non-blank line from *lines*, or None."""
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return None


def _wait_for_crash_report(
    proc: subprocess.Popen[str], out_lines: list[str], timeout: float
) -> dict[str, Any] | None:
    """Wait up to *timeout* for *proc*'s ok-report JSON line.

    The report is the child's ready signal: it printed what it did and
    is now waiting in ``MainLoop`` for the kill. Returns the parsed
    envelope, or None when the line never arrives (or the child dies
    first). Bounded polling -- the caller always kills *proc* after.
    """
    report: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = _last_nonempty(out_lines)
        if last is not None:
            try:
                parsed = json.loads(last)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None and parsed.get("ok") is True:
                report = parsed
                break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    return report


def _spawn_crash_child_and_kill(  # noqa: PLR0913 -- (scenario, db, crossings, extra): the crash-spawn inputs
    scenario: str,
    *,
    db: Path,
    crossings: int,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Spawn the heavy child; SIGKILL it once its JSON report lands.

    The crash leg of the race: the child runs the whole setup (CSV
    import, 300 typed crossings, stop/continue), prints the JSON line
    (the ready signal) and waits in its own ``MainLoop``; the parent
    then kills it so the session row is left with ``closed_at`` NULL --
    exactly the bookkeeping a hard crash leaves. The pipes are drained
    through daemon threads (the same bounded pattern as
    ``scenario_runner._run_bounded``) and never waited on unboundedly.
    """
    env = dict(os.environ)
    env[RACE_DB_ENV] = str(db)
    env[RACE_CROSSINGS_ENV] = str(crossings)
    if extra:
        env.update(extra)
    proc = subprocess.Popen(  # noqa: S603 -- fixed dev-test argv from this module
        [sys.executable, str(RACE_CHILD), scenario],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _read(stream: Any, sink: list[str]) -> None:  # noqa: ANN401
        try:
            for line in stream:
                sink.append(line)  # noqa: PERF402 -- incremental drain, same as scenario_runner._run_bounded
        except OSError, ValueError:
            pass

    tout = threading.Thread(target=_read, args=(proc.stdout, out_lines), daemon=True)
    terr = threading.Thread(target=_read, args=(proc.stderr, err_lines), daemon=True)
    tout.start()
    terr.start()

    report = _wait_for_crash_report(proc, out_lines, _CRASH_READY_TIMEOUT_S)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=2)
    context = (
        f"scenario={scenario}"
        f" returncode={proc.returncode}\n--- child stdout ---\n{''.join(out_lines)}"
        f"--- child stderr ---\n{''.join(err_lines)}"
    )
    if report is None:
        return {
            "ok": False,
            "error": "crash child never reported ok before the kill",
            "data": None,
            "context": context,
        }
    report["context"] = context
    return report


def _assert_report_ok(report: dict[str, Any]) -> dict[str, Any]:
    """Return report['data'] or fail with the child's context."""
    assert report["ok"] is True, report["context"]
    data = report["data"]
    assert data is not None, report["context"]
    return data


def _imported_plates() -> list[str]:
    """Return the roster plates the import must leave on the ride.

    The import REPLACES the ride's roster with the CSV's riders (the
    staged team's plates are not carried over -- csvio.commit defines
    the roster from the file), so the expected set is exactly the CSV's
    plates.
    """
    return sorted(str(number) for number in range(1, _RIDER_COUNT + 1))


# ------------------------------------------- export verification (R-74)

# The same parse pattern tests/unit/htmlexport_fixtures.race_data_block
# uses; reproduced here because tests/acceptance runs on a sys.path
# that does not include tests/unit (R-74 reuses the pattern, not the
# module).
_RACE_DATA_RE = re.compile(
    r'<script type="application/json" id="race-data">(.*?)</script>', re.DOTALL
)


def _race_data_block(path: Path) -> dict[str, object]:
    """Extract and parse a results page's ``race-data`` JSON block.

    Raises:
        AssertionError: *path* has no ``race-data`` block.
    """
    html = path.read_text(encoding="utf-8")
    match = _RACE_DATA_RE.search(html)
    if match is None:
        raise AssertionError(f"no race-data block found in {path}")
    return json.loads(match.group(1))


def _pdf_text(path: Path) -> str:
    """Return every page's extracted text, joined with newlines."""
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _projected(standings: list[dict[str, Any]]) -> tuple[tuple[int, str, int, str], ...]:
    """Normalize child rows to ``(place, plate, laps, hand)`` tuples."""
    return tuple(
        (int(row["place"]), str(row["plate"]), int(row["laps"]), str(row["hand"]))
        for row in standings
    )


def _html_standings(path: Path) -> tuple[tuple[int, str, int, str], ...]:
    """Read the race-data results as ``(place, plate, laps, hand)``."""
    record = _race_data_block(path)
    results = cast("list[dict[str, object]]", record["results"])
    return tuple(
        (int(row["place"]), str(row["plate"]), int(row["laps"]), str(row["hand"]))
        for row in results
    )


def _csv_standings(path: Path) -> tuple[tuple[int, str, int, str], ...]:
    """Read the standings CSV rows as ``(place, plate, laps, hand)``."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["place", "plate", "entry", "laps", "hand"]
    return tuple((int(row[0]), row[1], int(row[3]), row[4]) for row in rows[1:])


def test_full_race_r74_scripted_race_runs_end_to_end_through_the_real_ui(  # noqa: PLR0915 -- the script IS the test: one acceptance scenario
    race_support: ModuleType, tmp_path: Path
) -> None:
    """Import, 300 crossings, stop/continue, crash, quit, exports."""
    support = cast("Any", race_support)
    store_staging = support.store_staging
    db_path = tmp_path / "rides.db"
    csv_path = tmp_path / "riders.csv"
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    _write_riders_csv(csv_path, _RIDER_COUNT)
    # E9.2.2 (R-77): the nightly owns the seed and files it on failure;
    # None keeps the DB-owned random seed (spec §4).
    seed_env = os.environ.get("RIVERCROSSING_ACCEPTANCE_SEED")
    rng_seed = int(seed_env) if seed_env else None
    ride_id = store_staging.running_ride_with_roster(
        db_path, actual_start=_RACE_ACTUAL_START, rng_seed=rng_seed
    )

    # Phase 0: the staged db reads RUNNING_AT_EXIT with no crossings.
    staged = store_staging.race_db_facts(db_path)
    assert staged["record_crossing_count"] == 0
    assert staged["audit_actions"] == ["start"]
    assert staged["sessions"][-1]["closed_at"] is not None
    assert staged["sessions"][-1]["active_ride_id"] == ride_id

    # Phase A: CSV in, RUNNING entry, 300 typed crossings, stop/continue
    # then the parent SIGKILLs the child (the crash leg).
    report_a = _spawn_crash_child_and_kill(
        "setup_import_and_run",
        db=db_path,
        crossings=_WAVE_CROSSINGS,
        extra={
            RACE_CSV_ENV: str(csv_path),
            RACE_PLATES_ENV: str(_RIDER_COUNT),
            RACE_BOUND_ENV: str(_HEAVY_BOUND_S),
        },
    )
    data_a = _assert_report_ok(report_a)
    assert data_a["resume_dlg_shown"] is True, report_a["context"]
    assert data_a["resume_state"] == "running_at_exit", report_a["context"]
    assert "You quit at" in data_a["resume_message"], report_a["context"]
    assert data_a["status_label"] == "RUNNING", report_a["context"]
    assert data_a["entry_enabled"] is True, report_a["context"]
    # The import replaced the staged roster with the CSV's riders and
    # persisted to the store ride (the R-74 gap), so the rebuilt roster
    # carries exactly the CSV's plates.
    assert data_a["imported_plates"] == _imported_plates(), report_a["context"]
    assert data_a["crossings_typed"] == _WAVE_CROSSINGS, report_a["context"]
    assert data_a["feed_rows"] == _FEED_CAP, report_a["context"]  # feed caps at 30 (R-32)
    assert data_a["recorded_crossings"] == _WAVE_CROSSINGS, report_a["context"]
    assert data_a["entry_locked_when_stopped"] is True, report_a["context"]
    assert data_a["entry_reenabled_after_continue"] is True, report_a["context"]
    assert data_a["continue_kept_start"] is True, report_a["context"]
    assert data_a["audit_actions"] == (
        ["start"] + ["record_crossing"] * _WAVE_CROSSINGS + ["stop", "continue"]
    ), report_a["context"]

    killed = store_staging.race_db_facts(db_path)
    assert killed["record_crossing_count"] == _WAVE_CROSSINGS
    assert killed["sessions"][-1]["closed_at"] is None  # the kill never stamped
    assert killed["sessions"][-1]["active_ride_id"] == ride_id

    # Phase B: the relaunch reads the crash wording, all 300 crossings
    # survive, 5 more are recorded, and the child quits cleanly.
    report_b = _run_child(
        support,
        "resume_verify_quit",
        db=db_path,
        crossings=_RESUME_EXTRA_CROSSINGS,
        extra={RACE_BOUND_ENV: str(_QUIT_BOUND_S)},
    )
    data_b = _assert_report_ok(report_b)
    assert data_b["resume_dlg_shown"] is True, report_b["context"]
    assert data_b["resume_state"] == "crashed", report_b["context"]
    assert "closed unexpectedly" in data_b["resume_message"], report_b["context"]
    assert data_b["status_label"] == "RUNNING", report_b["context"]
    assert data_b["feed_rows_before"] == _FEED_CAP, report_b["context"]  # cap 30 (R-32)
    assert data_b["recorded_crossings_before"] == _WAVE_CROSSINGS, report_b["context"]
    assert data_b["crossings_recorded"] == _RESUME_EXTRA_CROSSINGS, report_b["context"]
    assert data_b["feed_rows_after"] == _FEED_CAP, report_b["context"]  # cap 30 (R-32)
    assert data_b["recorded_crossings_after"] == _WAVE_CROSSINGS + _RESUME_EXTRA_CROSSINGS, (
        report_b["context"]
    )

    quit_after_crash = store_staging.race_db_facts(db_path)
    assert quit_after_crash["record_crossing_count"] == _WAVE_CROSSINGS + _RESUME_EXTRA_CROSSINGS
    assert quit_after_crash["sessions"][-1]["closed_at"] is not None  # clean quit stamped
    assert quit_after_crash["sessions"][-1]["active_ride_id"] == ride_id

    # Phase C: the relaunch reads the quit wording, everything survives,
    # Finish runs, reopen + corrections re-rank the standings (the
    # baseline export watermark goes stale), finish-again re-locks, all
    # four exports write real files, and the child quits cleanly.
    report_c = _run_child(
        support,
        "finish_and_exports",
        db=db_path,
        crossings=0,
        extra={
            RACE_BOUND_ENV: str(_QUIT_BOUND_S),
            RACE_EXPORTS_DIR_ENV: str(exports_dir),
        },
    )
    data_c = _assert_report_ok(report_c)
    assert data_c["resume_dlg_shown"] is True, report_c["context"]
    assert data_c["resume_state"] == "running_at_exit", report_c["context"]
    assert "You quit at" in data_c["resume_message"], report_c["context"]
    assert data_c["feed_rows"] == _FEED_CAP, report_c["context"]  # cap 30 (R-32)
    assert data_c["status_after_finish_1"] == "FINISHED", report_c["context"]
    assert data_c["reopened"] is True, report_c["context"]
    assert data_c["add_crossing_plate"] in {
        str(number) for number in range(1, _RIDER_COUNT + 1)
    }, report_c["context"]
    assert data_c["voided_card"] != "", report_c["context"]
    assert data_c["status_after_finish_2"] == "FINISHED", report_c["context"]
    # The corrections landed past the baseline export watermark (stale)
    # and the final exports advanced it to the current event count.
    assert data_c["standings_before_corrections"] != data_c["standings"], report_c["context"]
    assert data_c["watermark_baseline"] < data_c["events_after_finish"], report_c["context"]
    assert data_c["watermark_final"] == data_c["events_after_finish"], report_c["context"]
    export_paths = {target: Path(value) for target, value in data_c["export_paths"].items()}
    assert set(export_paths) == {
        "export_html",
        "export_pdf",
        "export_poster",
        "export_results_csv",
    }, report_c["context"]

    finished = store_staging.race_db_facts(db_path)
    # The missed crossing persisted as its own audited action (E7.1:
    # add_crossing_at), not a record_crossing event, so the record
    # count stays 305 and the add shows up in the action list.
    assert finished["record_crossing_count"] == (_WAVE_CROSSINGS + _RESUME_EXTRA_CROSSINGS)
    assert finished["audit_actions"].count("add_crossing_at") == _ADDED_CROSSINGS
    assert finished["sessions"][-1]["closed_at"] is not None  # child D quit cleanly

    # Phase D: every export's content matches the standings the child
    # computed from its live engine.
    expected = _projected(data_c["standings"])
    assert expected

    html_path = export_paths["export_html"]
    assert html_path.exists()
    assert html_path.stat().st_size > 0
    assert _html_standings(html_path) == expected

    csv_export = export_paths["export_results_csv"]
    assert csv_export.exists()
    assert csv_export.stat().st_size > 0
    assert _csv_standings(csv_export) == expected

    pdf_path = export_paths["export_pdf"]
    poster_path = export_paths["export_poster"]
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0
    assert poster_path.exists()
    assert poster_path.stat().st_size > 0
    pdf_text = _pdf_text(pdf_path)
    poster_text = _pdf_text(poster_path)
    assert len(PdfReader(str(pdf_path)).pages) >= 1
    assert len(PdfReader(str(poster_path)).pages) == 1
    # The default export options (no results window open) render the
    # full field and laps board; the poster never shows a page count.
    assert "Full field" in pdf_text
    assert "Most laps" in pdf_text
    assert "Page 1 of" not in poster_text
    for row in data_c["standings"][:3]:
        plate, name = str(row["plate"]), str(row["name"])
        assert f"#{plate} {name}" in pdf_text
        assert f"#{plate} {name}" in poster_text
