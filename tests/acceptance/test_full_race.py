# SPDX-License-Identifier: GPL-3.0-only
"""E9.2.1 full-race acceptance: kill/quit + relaunch on one shared db.

R-74's multi-process seam: no test before this launched a real app
process against a shared ``rides.db``, killed it, relaunched a second
process against the SAME db, and proved the ride resumes with its
crossings intact. This test orchestrates exactly that sequence against
one temp database:

1. Stage a running ride (``store_staging.running_ride_with_roster``)
   whose previous session reads RUNNING_AT_EXIT.
2. Spawn child A (``race_child.py record_crash``) -- it resumes the
   staged ride, records 3 crossings through the real ``plate_input``,
   prints its JSON report, and waits; the parent then SIGKILLs it
   (the crash leg: ``closed_at`` is never stamped).
3. Spawn child B (``record_quit``) -- the resume dialog must read the
   CRASHED-with-ride wording ("closed unexpectedly"), Continue reloads
   the ride with all 3 crossings, the resumed clock shows wall time
   elapsed (the preserved ``actual_start``), 2 more crossings are
   recorded, and the real File ▸ Exit flow stamps ``closed_at``.
4. Spawn child C (``record_quit`` with 0 crossings) -- the resume
   dialog must now read the RUNNING_AT_EXIT wording ("You quit at"),
   Continue shows all 5 crossings, and the child quits cleanly.

Each child runs in a fresh interpreter against the shared db named by
``RIVERCROSSING_RACE_DB`` (env, never argv -- ``scenario_runner``'s
fixed-argv contract is ``<script> <scenario>``). Process handling uses
``scenario_runner._run_bounded`` (bounded drain, never stalls) for the
self-exiting children and a bounded spawn+read+kill helper for child A;
the parent never opens a Store itself (``Store.open`` would insert an
``app_session`` row and corrupt the very sequence under test) -- all
verification reads go through ``store_staging.race_db_facts``.

Only runnable where real wx windows can open (the Tart VM); on a bare
host the children cannot construct ``main_frame``.

``tests/functional/`` carries no ``__init__.py``, so ``scenario_runner``
and ``store_staging`` are importable only once the directory is on
``sys.path`` -- the same insertion ``test_scenario_runner.py`` makes.
The imports are deferred into a fixture so a missing module (this
task's RED phase) fails this module's own tests, never collection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))

pytestmark = pytest.mark.functional

# The child's public env contract (race_child.py module docstring). The
# values are hardcoded here so the test module stays importable in the
# RED phase; a drift surfaces as a child failure, never a silent pass.
RACE_DB_ENV = "RIVERCROSSING_RACE_DB"
RACE_CROSSINGS_ENV = "RIVERCROSSING_RACE_CROSSINGS"
RACE_CHILD = Path(__file__).resolve().parent / "race_child.py"

# Child A must report (the ready signal) well before its own
# self-terminate bound fires (scenario_runner.SCENARIO_CHILD_BOUND_
# SECONDS, 10 s): the parent kills it, never the child's bound timer.
_CRASH_READY_TIMEOUT_S = 10.0

# The staged ride started 90 minutes ago, so the resumed clock reads a
# positive, stable elapsed (~1:30:00) -- the "ride clock resumed" proof.
_RACE_START_LEAD_MINUTES = 90


@pytest.fixture(scope="module")
def race_support() -> ModuleType:
    """Return a namespace of the race-test support modules, lazily."""
    import scenario_runner  # type: ignore[import-not-found]  # noqa: PLC0415
    import store_staging  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast(
        "ModuleType",
        SimpleNamespace(scenario_runner=scenario_runner, store_staging=store_staging),
    )


def _run_quit_child(support: Any, db_path: Path, crossings: int) -> dict[str, Any]:  # noqa: ANN401
    """Run race_child record_quit via _run_bounded; decode its envelope.

    The env vars are set on ``os.environ`` around the spawn (Popen
    inherits it) and restored after, so ``scenario_runner._run_bounded``
    itself stays untouched. Returns ``_decode_scenario_output``'s
    envelope with the captured context attached.
    """
    saved = {name: os.environ.get(name) for name in (RACE_DB_ENV, RACE_CROSSINGS_ENV)}
    os.environ[RACE_DB_ENV] = str(db_path)
    os.environ[RACE_CROSSINGS_ENV] = str(crossings)
    try:
        completed = support.scenario_runner._run_bounded(
            [sys.executable, str(RACE_CHILD), "record_quit"],
            support.scenario_runner.SCENARIO_TIMEOUT_SECONDS,
        )
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return support.scenario_runner._decode_scenario_output("record_quit", completed)


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


def _spawn_crash_child_and_kill(db_path: Path, crossings: int) -> dict[str, Any]:
    """Spawn record_crash; SIGKILL it once its JSON report lands.

    The crash leg of the race: child A records its crossings, prints
    the JSON line (the ready signal) and waits in its own ``MainLoop``;
    the parent then kills it so the session row is left with
    ``closed_at`` NULL -- exactly the bookkeeping a hard crash leaves.
    The pipes are drained through daemon threads (the same bounded
    pattern as ``scenario_runner._run_bounded``) and never waited on
    unboundedly, so a hung child cannot stall the test.
    """
    env = dict(os.environ)
    env[RACE_DB_ENV] = str(db_path)
    env[RACE_CROSSINGS_ENV] = str(crossings)
    proc = subprocess.Popen(  # noqa: S603 -- fixed dev-test argv from this module
        [sys.executable, str(RACE_CHILD), "record_crash"],
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
        "scenario=record_crash"
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


def _elapsed_minutes(label: str) -> float:
    """Parse an ``H:MM:SS`` clock label into minutes."""
    parts = label.split(":")
    assert len(parts) == 3, f"unexpected clock label {label!r}"
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 60 + minutes + seconds / 60


def test_full_race_kill_quit_relaunch_resumes_ride(
    race_support: ModuleType, tmp_path: Path
) -> None:
    """Kill, relaunch, clean quit, relaunch: the ride resumes."""
    support = cast("Any", race_support)
    store_staging = support.store_staging
    db_path = tmp_path / "rides.db"
    actual_start = datetime.now() - timedelta(  # noqa: DTZ005 -- naive local, Store's own contract
        minutes=_RACE_START_LEAD_MINUTES
    )
    # E9.2.2 (R-77): the nightly owns the seed and files it on failure;
    # None keeps the DB-owned random seed (spec §4).
    seed_env = os.environ.get("RIVERCROSSING_ACCEPTANCE_SEED")
    rng_seed = int(seed_env) if seed_env else None
    ride_id = store_staging.running_ride_with_roster(
        db_path, actual_start=actual_start, rng_seed=rng_seed
    )

    # Phase 0: the staged db reads RUNNING_AT_EXIT with no crossings.
    staged = store_staging.race_db_facts(db_path)
    assert staged["record_crossing_count"] == 0
    assert staged["audit_actions"] == ["start"]
    assert staged["sessions"][-1]["closed_at"] is not None
    assert staged["sessions"][-1]["active_ride_id"] == ride_id

    # Phase A: child A resumes, records 3, then the parent SIGKILLs it.
    report_a = _spawn_crash_child_and_kill(db_path, crossings=3)
    data_a = _assert_report_ok(report_a)
    assert data_a["resume_dlg_shown"] is True, report_a["context"]
    assert data_a["resume_state"] == "running_at_exit", report_a["context"]
    assert "You quit at" in data_a["resume_message"], report_a["context"]
    assert data_a["crossings_recorded"] == 3, report_a["context"]
    assert data_a["feed_rows"] == 3, report_a["context"]
    assert data_a["audit_actions"] == [
        "start",
        "record_crossing",
        "record_crossing",
        "record_crossing",
    ], report_a["context"]

    killed = store_staging.race_db_facts(db_path)
    assert killed["record_crossing_count"] == 3
    assert killed["sessions"][-1]["closed_at"] is None  # the kill never stamped
    assert killed["sessions"][-1]["active_ride_id"] == ride_id

    # Phase B: child B sees the crash wording, resumes with the clock,
    # records 2 more, and quits through the real File ▸ Exit flow.
    report_b = _run_quit_child(support, db_path, crossings=2)
    data_b = _assert_report_ok(report_b)
    assert data_b["resume_dlg_shown"] is True, report_b["context"]
    assert data_b["resume_state"] == "crashed", report_b["context"]
    assert "closed unexpectedly" in data_b["resume_message"], report_b["context"]
    assert data_b["status_label"] == "RUNNING", report_b["context"]
    # The ride clock resumed: elapsed reads ~the staged 90-minute lead
    # (the preserved actual_start), never a zeroed/fresh-start clock.
    assert _elapsed_minutes(data_b["clock_elapsed"]) >= _RACE_START_LEAD_MINUTES - 1, report_b[
        "context"
    ]
    assert data_b["crossings_recorded"] == 2, report_b["context"]
    assert data_b["feed_rows"] == 5, report_b["context"]  # 3 survived + 2 new

    quit_after_crash = store_staging.race_db_facts(db_path)
    assert quit_after_crash["record_crossing_count"] == 5
    assert quit_after_crash["sessions"][-1]["closed_at"] is not None  # clean quit stamped
    assert quit_after_crash["sessions"][-1]["active_ride_id"] == ride_id

    # Phase C: child C sees the quit wording; everything survived.
    report_c = _run_quit_child(support, db_path, crossings=0)
    data_c = _assert_report_ok(report_c)
    assert data_c["resume_dlg_shown"] is True, report_c["context"]
    assert data_c["resume_state"] == "running_at_exit", report_c["context"]
    assert "You quit at" in data_c["resume_message"], report_c["context"]
    assert data_c["feed_rows"] == 5, report_c["context"]  # everything survived
    assert data_c["recorded_crossings"] == 5, report_c["context"]

    final = store_staging.race_db_facts(db_path)
    assert final["record_crossing_count"] == 5
    assert final["sessions"][-1]["closed_at"] is not None
